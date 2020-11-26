#!/usr/bin/env python
# RCM 2020-8-22 Modified from https://github.com/kurianinc/opkg
# Note Vector runs python 2.7, and the rest of the world is on 3

from __future__ import print_function
"""
Vector openpkg package management engine.
"""
__author__ = "Randall Maas <randym@randym.name>"


# The original used yaml manifest, we use a windows .ini style, which is
# compatible with what is already installed on Vector
import re
import os
import subprocess
import ConfigParser
import time
import hashlib
import sys

'''This file will be looked up under OPKG_DIR/conf'''
OPKG_CONF_FILE='/etc/vpkg/conf/vpkg.env'

META_FILE_PREVIOUS='Previous.meta'
META_FILE_LATEST='Latest.meta'
EXTRA_PARAM_DELIM=','
EXTRA_PARAM_KEY_VAL_SEP='='

def loge(msg):
    "Send error message to stderr"
    print(msg, file=sys.stderr)
    sys.stderr.flush()
    
''' Classes '''

'''Tracks local env configuration'''
class EnvConfig():
    def __init__(self):
        self.config_file=OPKG_CONF_FILE
        self.conf=None

    def setConfigFile(self,config_file):
        self.config_file=config_file

    def loadConfigFile(self):
        self.conf = ConfigParser.ConfigParser()
        if os.path.isfile(self.config_file) and os.access(self.config_file, os.R_OK):
            self.conf.read(self.config_file)

    def updateConfigItem(self,section,key,val):
        try:
            self.conf.set(section,key,val)
        except:
            print ("Warning: Cannot locate config item "+key+" in "+self.config_file)
            return False

        return True

    def getConfigItem(self,section,item):
        return self.conf.get(section,item)


'''Reads the manifest config parsed from INI file'''
def get_manifest(manifest_file):
    "Returns config parsed from INI file in filelike object"
    config = None
    with open(manifest_file, 'r') as stream:
        try:
            config = ConfigParser.ConfigParser();
            config.readfp(stream);

        except Exception as exc:
            loge ("Error: Problem loading manifest file "+manifest_file)
            print(exc)
            return

    if not config.has_option("META", 'rel_num'):
        loge ("Error: rel_num not found in "+manifest_file)
        return
    return config


'''Class for core Open Pkg'''
class Pkg():
    def __init__(self,name):
        if re.search("[^\w\-]", name) is not None:
            loge ("Error: Illegal character in package name (" + name + ")")
            return
        self.name=name
        self.rel_num=None
        self.rel_ts=None
        self.manifest_file = name + '.ini'
        self.tarball_name = name + '.vpkg'
        self.md5=None #md5 of package being installed.
        self.manifest=None
        self.build_root = os.getcwd()
        self.manifest_path=self.build_root+'/'+self.manifest_file

        '''stage_dir - where files to create a tarball are staged and
        files from a tarball are extracted for installation.
        By default, the stage_dir is set for action create pkg.
        '''
        self.stage_dir = self.build_root + '/.pkg/' + name
        self.deploy_dir=self.stage_dir+'/.install'

        self.env_conf=None
        self.install_meta=None #meta data of existing installation
        self.install_md5=None #md5 of currently installed version

    @staticmethod
    def parseName(pkg_label):
        '''pkg can be specified in following ways:
        - /path/to/mypkg.vpkg -- Vector package (tarball) available locally
        - /path/to/mypkg-rel_num.vpkg -- Vector package (tarball) available  locally
        - /path/to/mypkg.tgz -- tarball available locally
        - /path/to/mypkg-rel_num.tgz -- tarball available locally
        - mypkg
        - mypkg-rel_num
        '''
        pkg_name_rel_num = os.path.basename(pkg_label)
        pkg_name_rel_num = pkg_name_rel_num.replace('.tgz', '')
        pkg_name_rel_num = pkg_name_rel_num.replace('.vpkg', '')
        tarball_name = pkg_name_rel_num + '.vpkg'
        pkg_name = re.split('-', pkg_name_rel_num)[0]

        return pkg_name,pkg_name_rel_num,tarball_name

    @staticmethod
    def parseTarballName(tarball_name):
        rel_num, rel_ts = 'dev', None
        '''The dev version will not have any rel_num or rel_ts
        The parsing is based on the assumption that the tarball names can have only 2 formats:
         name.vpkg - dev
         name.tgz - dev
         name-rel_num-rel_ts.vpkg - release
         name-rel_num-rel_ts.tgz - release
        '''
        m = re.search('.+?-(.+?)-(.+).(tgz|vpkg)', tarball_name)
        if m:
            rel_num = m.group(1)
            rel_ts = m.group(2)

        return rel_num,rel_ts

    def setManifest(self,f):
        self.manifest_path=f

    def setRelNum(self,rel_num):
        self.rel_num=rel_num
    def setRelTs(self,rel_ts):
        self.rel_ts=rel_ts

    '''Meta file has this syntax: pkg_name,rel_num,rel_ts,pkg_md5,deploy_ts'''
    def loadMeta(self):
        self.install_meta=dict()
        meta_dir=self.env_conf['basic']['opkg_dir'] + '/meta/' + self.name
        meta_path = meta_dir + "/" + META_FILE_LATEST
        self.install_meta['dir']=meta_dir
        self.install_meta['latest_install']=self.loadMetaFile(meta_path)
        if not self.install_meta['latest_install']:
            print ("Info: No active installation of "+self.name+" found at "+self.env_conf['basic']['opkg_dir'])
        meta_path = meta_dir + "/" + META_FILE_PREVIOUS
        self.install_meta['previous_install'] = self.loadMetaFile(meta_path)
        if not self.install_meta['previous_install']:
            print ("Info: No previous installation of "+self.name+" found.")
        if self.install_meta['latest_install']:
            self.install_md5 = self.install_meta['latest_install']['pkg_md5']

    def getMeta(self):
        return self.install_meta

    '''Load .meta files that keep track of deployments and verifies the data in those.
    The meta data on package deployment is a single line with attrs delimited by , in the following order:
    pkg_name,pkg_rel_num,pkg_ts,pkg_md5,deploy_ts
    '''
    def loadMetaFile(self,file_path):
        if not os.path.isfile(file_path): return None
        str = loadFile(file_path)
        install_info = str.strip().split(',')
        if len(install_info) < 5: return None
        meta=dict()
        meta['pkg_name']=install_info[0]
        meta['pkg_rel_num'] = install_info[1]
        meta['pkg_ts'] = install_info[2]
        meta['pkg_md5'] = install_info[3]
        meta['deploy_ts'] = install_info[4]
        meta['undo_package'] = install_info[5]

        return meta

    '''Reset install meta files upon successful installation of a package.'''
    def registerInstall(self,deploy_inst, uninstall):
        meta_dir=deploy_inst.opkg_dir + "/meta/" + self.name
        meta_file_previous = meta_dir + "/" + META_FILE_PREVIOUS
        meta_file_latest = meta_dir + "/" + META_FILE_LATEST
        runCmd("mkdir -p "+meta_dir)

        # move the uninstall package to the folder
        if not execOSCommand("mv -f " + uninstall + " " + meta_dir):
            print ("Problem moving " + uninstall + " to uninstall directory")
            return False

        if os.path.exists(meta_file_latest):
            if not execOSCommand("mv -f " + meta_file_latest + " " + meta_file_previous):
                print ("Problem moving " + meta_file_latest + " as " + meta_file_previous)
                return False

        '''Meta file has this syntax: pkg_name,rel_num,rel_ts,pkg_md5,deploy_dir,undo-pkg'''
        rel_num=''
        if self.rel_num: rel_num=self.rel_num
        rel_ts=0
        if self.rel_ts: rel_ts=self.rel_ts
        strx = self.name+','+rel_num+','+str(rel_ts)+','+self.pkg_md5+','+deploy_inst.deploy_ts+','+os.path.basename(uninstall)
        cmd = "echo " + strx + ">" + meta_file_latest
        if not execOSCommand(cmd):
            loge ("Error: Couldn't record the package installation.")
            return False
        self.loadMeta()

        return True

    def setEnvConfig(self,env_conf):
        self.env_conf=env_conf

    def create(self):
        #the default manifest points to that in build dir
        self.manifest = get_manifest(self.manifest_path)


        runCmd("rm -rf "+self.stage_dir)
        runCmd("mkdir -p " + self.stage_dir)
        os.chdir(self.stage_dir)

        '''Copy manifest to the deploy folder in archive'''
        runCmd('mkdir -p ' + self.deploy_dir)
        if not execOSCommand('cp ' + self.manifest_path + ' ' + self.deploy_dir + '/'):
            loge ("Error: Problem copying package manifest.")
            return False

        '''Stage files content for archiving'''
        if self.manifest.has_section('files'):
            for tgt in self.manifest.options('files'):
                src = self.manifest.get('files',tgt)
                if not self.stageContent(os.path.join(self.build_root,src),tgt.strip('/')):
                    loge ("Error: Cannot copy content at "+src+" for archiving.")
                    return False

        '''Make tarball and clean up the staging area'''
        if self.manifest.has_option("META", 'rel_num'):
            rel_num=self.manifest.get("META", 'rel_num')
            self.tarball_name = self.name + '-' + rel_num + '.vpkg'
        os.chdir(self.stage_dir)
        rc = runCmd("tar czf " + self.tarball_name + ' * '+os.path.basename(self.deploy_dir))
        if rc != 0:
            loge ("Error: Couldn't create package " + self.tarball_name)
            return False
        os.chdir(self.build_root)
        rc = runCmd('mv ' + self.stage_dir + '/' + self.tarball_name + ' ./')
        if rc == 0:
            runCmd("rm -rf " + self.stage_dir)
            print ("Package " + self.tarball_name + " has been created.")
        else:
            loge ("Error: Package " + self.tarball_name + " couldn't be created.")
            return False

    '''Creates an archive snapshotting the current state.  This is used to to
       later undoan installation.'''
    def createUndoManifest(self,undoManifestPath):
        undoConfig = ConfigParser.ConfigParser()
        #copy the key pieces from the manifest
        self.manifest = get_manifest(self.manifest_path)
        # copy the manifest information
        if self.manifest.has_section('META'):
            undoConfig.add_section('META')
            for tgt in self.manifest.options('META'):
                undoConfig.set('META', tgt, self.manifest.get('META', tgt))

        # copy the list of files -- just the ones that will be modified
        # not the ones from the pkg
        undoConfig.add_section('files')
        if self.manifest.has_section('files'):
            for tgt in self.manifest.options('files'):
                target = '/' + tgt.strip('/')
                undoConfig.set('files',target,target)

        if self.manifest.has_section('templates'):
            for index in self.manifest.options('templates'):
                target = '/' + self.manifest.get('templates',index)+ tgt.strip('/')
                undoConfig.set('files',target,target)
                

        # copy and reverse the string flipper
        # todo: this maybe should be save that file?
        # - the pattern could be a regular expression so that won'y work easily
        if self.manifest.has_section('replaces'):
            undoConfig.add_section('replaces')
            for replaces_file,token in self.manifest.items('replaces'):
                '''Each entry for replacement in the replaces_file is a dict as replacement entries are delimited with :
                  We'll reverse those'''
                pattern, replace = re.split(Tmpl.TMPL_KEY_VAL_DELIM,token)
                undoConfig.set('replaces', replaces_file, replace+Tmpl.TMPL_KEY_VAL_DELIM+pattern)

        # TODO: could fix up the symlinks, permissions but that isn't clear enough how to
        # write it out
        cfgfile = open(undoManifestPath, 'w+')
        undoConfig.write(cfgfile)
        cfgfile.close()
    
    # How to launch a making of the archive?

    def stageContent(self,src,tgt):
        os.chdir(self.stage_dir)
        if os.path.isdir(src):
            '''skip build folder silently, but individual files can still be added.'''
            if runCmd("mkdir -p " + tgt) != 0: return False
            if runCmd("cp -r " + src + '/. ' + tgt + '/') != 0: return False
        else:
            tgt_dir = os.path.dirname(tgt)
            if tgt_dir != '':
                if runCmd("mkdir -p " + tgt_dir) != 0: return False
            if os.path.exists(src):
                if runCmd("cp " + src + ' ' + tgt) != 0: return False

        return True

    '''Execute the deploy playbook for a package specified in the manifest'''
    def install(self,tarball_path,deploy_inst,pkg_name):
        deploy_inst.logHistory("Installing package "+self.name+" using "+tarball_path)

        ''' Track the md5 of package being installed '''
        self.pkg_md5=getFileMD5(tarball_path)

        '''Extract the tarball in stage_dir, to prepare for deploy playbook to  execute steps'''
        stage_dir=os.path.join(deploy_inst.stage_dir,self.name,deploy_inst.deploy_ts)
        if not execOSCommand('mkdir -p ' + stage_dir):
                return
        os.chdir(stage_dir)

        if not execOSCommand('tar xzf ' + tarball_path):
            loge ("Error: Problem extracting " + tarball_path + " in " + stage_dir)
            return False


        '''Resolve manifest, and files defined under templates and replaces with actual values 
        defined for this specific installation.'''
        manifest_path=os.path.join(os.getcwd(),os.path.join(".install",self.manifest_file))
        tmpl_inst=Tmpl(manifest_path)
        if not tmpl_inst.resolveVars(deploy_inst.getVars()):
            loge ("Error: Problem resolving "+self.manifest_file)
            return False
        pkg_manifest=get_manifest(manifest_path)
        
        '''Snapshot the files that will be changed to allow undoing'''
        if not deploy_inst.uninstall:
            deploy_inst.logHistory("Making backup")
            undo_manifest_path=os.path.join(deploy_inst.stage_dir, pkg_name+"_undo.ini")
            self.createUndoManifest(undo_manifest_path)
            os.chdir(deploy_inst.stage_dir)
            undo_pkg=Pkg(pkg_name+"_undo")
            undo_pkg.create()
            runCmd("rm " + undo_manifest_path)
            os.chdir(stage_dir)

        '''Run pre-deploy steps. 
        These are run immediately after the tarball is extracted in stage_dir
        '''
        if pkg_manifest.has_section('pre_deploy'):
            for index in pkg_manifest.options('pre_deploy'):
                step=pkg_manifest.get('pre_deploy',index)
                if not execOSCommand(step):
                    loge ("Error: Problem executing the following step in pre_deploy phase: "+step)
                    return False

        '''copy targets entries to install_root'''
        # Only uses well-defined folders to prevent too much damage
        for base_path in ['anki','etc','home', 'usr', 'var']:
            source_path = os.path.join(stage_dir, base_path)
            # Did the archive include this top-level folder?
            if not os.path.exists(source_path): continue

            # Command to copy the folder onto the main system
            target_path = '/' + base_path
            cmd='cp -r '+source_path+'/* '+target_path+'/'
            if not execOSCommand(cmd):
                loge ("Error: Problem copying from " + stage_dir + ". command: " + cmd)
                return False

        '''Generate installation template files with actual values, variables are marked as {{ var }} '''
        if pkg_manifest.has_section('templates'):
            for index in pkg_manifest.options('templates'):
                tmpl = pkg_manifest.get('templates',index)
                tmpl_path=tmpl
                #if not re.match("^\/", tmpl): tmpl_path = deploy_dir + "/" + tmpl
                tmpl_inst=Tmpl(tmpl_path)
                if not tmpl_inst.resolveVars(deploy_inst.getVars()):
                    loge ("Error: Couldn't install resolved files for those marked as templates, with real values.")
                    return False

        '''Replaces tokens in files flagged for that, tokens are unmarked like PORT=80 etc'''
        if pkg_manifest.has_section('replaces'):
            for replaces_file,pattern in pkg_manifest.items('replaces'):
                '''Each entry for replacement in the replaces_file is a dict as replacement entries are delimited with :'''
                replaces_path=replaces_file
                #if not re.match("^\/", replaces_file): replaces_path = deploy_dir + "/" + replaces_file
                tmpl_inst=Tmpl(replaces_path)
                if not tmpl_inst.replaceTokens(pattern):
                    loge ("Error: Couldn't install resolved files for those marked with having tokens in the 'replaces' section, with real values.")
                    return False

        '''Symlinks'''
        if pkg_manifest.has_section('symlinks'):
            for tgt_path in pkg_manifest.options('symlinks'):
                src_path = pkg_manifest.get('symlinks',tgt_path)
                #if not re.match("^\/", tgt_path): tgt_path = deploy_dir + "/" + tgt_path
                #if not re.match("^\/", src_path): src_path = deploy_dir + "/" + src_path
                cmd = "ln -sfn " + src_path + " " + tgt_path
                if not execOSCommand(cmd):
                    loge ("Error: Problem creating symlink " + cmd)
                    return False

        '''Permissions
        The list items will be returned in the format, dir:owner:group mod; eg: 'apps:root:root 0444'
        Parse each line accordingly.
        '''
        if pkg_manifest.has_section('permissions'):
            for fpath in pkg_manifest.options('permissions'):
                perm_opt= pkg_manifest.get('permissions',fpath)
                chown_opt,chmod_opt = perm_opt.split(' ')
                #if not re.match("^\/", fpath): fpath = deploy_dir + "/" + fpath
                cmd="chown -R "+chown_opt+" "+fpath+';chmod -R '+chmod_opt+' '+fpath
                if not execOSCommand(cmd):
                    loge ("Error: Problem setting permissions on " + fpath+'. Command: '+cmd)
                    return False

        '''Post-deploy steps'''
        if pkg_manifest.has_section('post_deploy'):
            for index in pkg_manifest.options('post_deploy'):
                step=pkg_manifest.get('post_deploy',index)
                if not execOSCommand(step):
                    loge ("Error: Problem executing the following step in post_deploy phase: " + step)
                    return False
        
        ''' Register the installation and the uninstall package'''
        if not deploy_inst.uninstall:
            self.registerInstall(deploy_inst, os.path.join(deploy_inst.stage_dir, undo_pkg.tarball_name))

        '''delete the stage_dir upon successful installation of the package'''
        os.chdir("/tmp")  # a workaround to avoid system warning when curr dir stage_dir is deleted.
        print ("deleting " + stage_dir)
        if not execOSCommand('rm -r ' + stage_dir):
            print ("Warning: Couldn't delete " + stage_dir)

        if not deploy_inst.uninstall:
            print ("Info: Package "+self.name+" has been installed")
        else:
            print ("Info: Package has been uninstalled.")

        return True

    def isInstalled(self,tarball_path):
        if not self.getMeta()['latest_install']:
            return False
        md5_local = self.install_meta['latest_install']['pkg_md5']

        return (getFileMD5(tarball_path) == md5_local)

'''Class to process the main opkg actions'''
class opkg():
    ACTIONS=['create','list','install']

    '''action specific required configs'''
    ACTION_CONFIGS={
        'install': ['install_root'],
        'list':['install_root'],
    }

    OPKG_LABEL='vector-pkg'
    OPKG_VERSION='0.1.0'

    def __init__(self,params):
        self.arg_dict=dict()
        self.arg_dict['opkg_cmd']=params[0]
        self.action=None #This will be available in the env as OPKG_ACTION
        self.extra_vars=dict()
        self.conf_file=None
        self.configs=dict()
        self.pkgs=None
        self.opkg_dir=None

        if len(params) < 2:
            self.printHelp()
            Exit(0)

        '''action is positional'''
        self.action=params[1]

        '''The args can be in these formats: argx,--opt_x,--opt_y=opt_val'''
        for argx in params[1:]:
            if re.match("^--", argx) is not None:
                m = re.split('--', argx)
                n = re.match("^(.+?)=(.+)", m[1])
                if n is not None:
                    self.arg_dict[n.group(1)] = n.group(2)
                else:
                    self.arg_dict[m[1]] = ''
            else:
                self.arg_dict[argx] = ''

        '''Set extra-vars dict'''
        if 'extra-vars' in self.arg_dict:
            extra_vars = re.split(EXTRA_PARAM_DELIM,self.arg_dict['extra-vars'])
            for extra_var in extra_vars:
                k, v = re.split(EXTRA_PARAM_KEY_VAL_SEP,extra_var)
                self.extra_vars[k] = v

        if self.arg_dict.has_key('help'):
            self.printHelp()
            Exit(0)
        elif self.arg_dict.has_key('version'):
            self.printVersion()
            Exit(0)

        '''Check if config file is specified, if it exists load and initialize configs from it.
        Note, the config items are grouped under sections in config file, but, 
        from command-line there is no option to qualify an item with section and so it should be unique across sections.
        '''
        opkg_conf_file=OPKG_CONF_FILE
        if 'opkg_dir' in self.arg_dict: opkg_conf_file=self.arg_dict['opkg_dir']+'/conf/opkg.env'
        self.conf_file=opkg_conf_file
        self.loadConfigFile()
        self.opkg_dir=self.configs['basic']['opkg_dir']

        '''Override config items specified in config file with those from command-line'''
        for section in self.configs:
            for item in self.configs[section]:
                if item in self.arg_dict: self.configs[section][item]=self.arg_dict[item]

        '''Parse out common options such as pkg'''
        if 'pkg' in self.arg_dict:
            self.pkgs=re.split(',',self.arg_dict['pkg'])

        return

    '''Loads configs from opkg.env as a dictionary'''
    def loadConfigFile(self):
        self.configs['basic']={'opkg_dir': '/etc/vpkg','stage_dir':'/tmp/vpkg-staging',
                                        'deploy_history_file':'/var/log/deploy_history.log',
                                        'install_root': '/tmp/vpkg'};
        Config = ConfigParser.ConfigParser()
        Config.read(self.conf_file)
        sections=Config.sections()
        for section in sections:
            self.configs[section]=dict()
            for item in Config.options(section):
                self.configs[section][item]=Config.get(section,item)
        return

    def printVersion(self):
        print (opkg.OPKG_LABEL + " v" + opkg.OPKG_VERSION)

        return True

    def printHelp(self):
        self.printVersion()

        script = os.path.basename(self.arg_dict['opkg_cmd'])
        print ("Usages:")
        print (script + " --version")
        print (script + " --help")
        print (script + " list [--pkg=pkg1,pkg2,...]")
        print (script + " create --pkg=pkg1,pkg2,... [--release]")
        print (script + " install --pkg=pkg1,pkg2[-REL_NUM|dev],... [--install_root=/path/to/install]")
        print (script + " uninstall [--pkg=pkg1,pkg2,...]")

        return True

    '''Execute the action'''
    def main(self):

        if self.action=='create':
            self.extra_vars['ACTION'] = 'create'
            for pkg in self.pkgs:
                pkg_inst=Pkg(pkg)
                pkg_inst.create()

        elif self.action=='list':
            self.extra_vars['ACTION'] = 'list'
            if None == self.pkgs:
                self.pkgs = os.listdir(os.path.join(self.configs['basic']['opkg_dir'], 'meta'))
            
            for pkg in self.pkgs:
                pkg_name, pkg_name_rel_num, tarball_name = Pkg.parseName(pkg)
                pkg_inst=Pkg(pkg_name)
                pkg_inst.setEnvConfig(self.configs)
                pkg_inst.loadMeta()
                pkg_meta=pkg_inst.getMeta()
                if not pkg_meta: continue
                print (pkg_name+'-'+pkg_meta['latest_install']['pkg_rel_num'])

        elif self.action=='install':
            self.extra_vars['ACTION'] = 'install'
            deploy_inst=Deploy(self.configs,self.arg_dict,self.extra_vars)
            for pkg in self.pkgs:
                pkg_name,pkg_name_rel_num,tarball_name =Pkg.parseName(pkg)

                '''Start installation of the package once the tarball is copied to staging location.'''
                deploy_inst.installPackage(pkg_name,tarball_name,pkg_name_rel_num,os.path.join(os.getcwd(),pkg))
        elif self.action=='uninstall':
            self.extra_vars['ACTION'] = 'uninstall'
            self.arg_dict['uninstall']=''
            deploy_inst=Deploy(self.configs,self.arg_dict,self.extra_vars)
            for pkg in self.pkgs:
                # First, look up the package to undo it
                pkg_name,pkg_name_rel_num,tarball_name =Pkg.parseName(pkg)
                pkg_inst=Pkg(pkg_name)
                pkg_inst.setEnvConfig(self.configs)
                pkg_inst.loadMeta()
                pkg_meta=pkg_inst.getMeta()
                if not pkg_meta: continue

                undo_package_name = pkg_meta['latest_install']['undo_package'];
                pkg_name,pkg_name_rel_num,tarball_name =Pkg.parseName(undo_package_name)
                
                '''Start uninstallation of the package.'''
                deploy_inst.installPackage(pkg_name,os.path.join(pkg_meta['dir'],tarball_name),pkg_name_rel_num,os.path.join(pkg_meta['dir'],undo_package_name))

                # finally nuke the old folder
                runCmd("rm -rf "+pkg_meta['dir'])
        else:
            print ("Unsupported action: "+self.action)

'''Class for installation specific methods'''
class Deploy():
    def __init__(self,env_conf,deploy_options,extra_vars=None):
        self.deploy_ts=str(int(time.time()))
        self.env_conf=env_conf
        self.install_root=self.env_conf['basic']['install_root']
        self.deploy_root=self.install_root+'/installs/'+self.deploy_ts
        self.opkg_dir=self.env_conf['basic']['opkg_dir']
        self.stage_dir=self.env_conf['basic']['stage_dir']
        self.history_dir=os.path.join(self.opkg_dir,'history')
        self.extra_vars=extra_vars

        self.deploy_force=False
        self.uninstall=False
        if 'force' in deploy_options: self.deploy_force=True
        if 'uninstall' in deploy_options:
            self.deploy_force=True
            self.uninstall=True

        if not self.extra_vars: self.extra_vars=dict()
        self.extra_vars['OPKG_NAME'] = None
        self.extra_vars['OPKG_REL_NUM'] = None
        self.extra_vars['OPKG_TS'] = None
        self.extra_vars['OPKG_ACTION'] = None

        if not execOSCommand('mkdir -p ' + self.history_dir): return

    '''Returns the extra-vars specified from commandline and the OPKG_ vars'''
    def getVars(self):
        return self.extra_vars

    def logHistory(self,log_entry):
        history_log=self.deploy_ts+": "+log_entry
        history_file=self.env_conf['basic']['deploy_history_file']
        with open(os.path.join(self.history_dir, history_file), "a+") as hf: hf.write(history_log+"\n")

        return True

    '''The tarball is downloaded/copied to download_dir'''
    def installPackage(self,pkg_name,tarball_name,pkg_name_rel_num,tarball_path):
        rel_num,rel_ts=Pkg.parseTarballName(tarball_name)

        self.extra_vars['OPKG_NAME'] = pkg_name
        self.extra_vars['OPKG_REL_NUM'] = rel_num
        self.extra_vars['OPKG_TS'] = rel_ts

        pkg=Pkg(pkg_name)
        pkg.setRelNum(rel_num)
        pkg.setRelTs(rel_ts)
        pkg.setEnvConfig(self.env_conf)
        pkg.loadMeta()
        if self.deploy_force or not pkg.isInstalled(tarball_path):
            pkg.install(tarball_path,self,pkg_name)
        else:
            print ("Info: This revision of package "+pkg_name+" is already installed at "+self.install_root+'/installs/'+pkg.getMeta()['latest_install']['deploy_ts']+'/'+pkg_name)
            print ("Info: Use --force option to override.")

        return True

'''Utility classes '''

'''Utility class to do template related tasks'''
class Tmpl():
    TMPL_KEY_VAL_DELIM=':'

    def __init__(self,tmpl_path):
        self.tmpl_path=tmpl_path
        self.is_dir=False
        if not os.path.exists(tmpl_path):
            loge ("Error: " + tmpl_path + " doesn't exist.")
            return
        if os.path.isdir(tmpl_path): self.is_dir = True

    '''Recreates files under tmpl_path with values from vars_dict
    The template vars are searched for using pattern {{ var }}
    tmpl_path could be single file or a directory, 
    in the latter case all files in the dir will be checked for recursively.
    '''
    def resolveVars(self,vars_dict,backup=False):
        if self.is_dir:
            files=os.listdir(self.tmpl_path)
            for f in files:
                ftmpl=Tmpl(self.tmpl_path+'/'+f)
                ftmpl.resolveVars(vars_dict,backup)
        else:
            if not self.resolveVarsFile(self.tmpl_path,vars_dict,backup):
                loge ("Error: Failed to resolve template "+self.tmpl_path)
                return False

        return True

    '''Recreates files under tmpl_path with values from vars_list
    vars_list contains a search/replace pair SEARCH-STR:REPLACE-STR, the file is updated by replacing all SEARCH-STR with REPLACE-STR
    tmpl_path could be single file or a directory, 
    in the latter case all files in the dir will be checked for recursively.
    '''
    def replaceTokens (self,tokens_list,backup=False):
        if self.is_dir:
            files = os.listdir(self.tmpl_path)
            for f in files:
                fpath = os.path.join(self.tmpl_path, f)
                ftmpl = Tmpl(fpath)
                ftmpl.replaceTokens(tokens_list,backup)
        else:
            if not self.replaceTokensFile(self.tmpl_path,tokens_list, backup):
                loge ("Error: Failed to resolve template " + self.tmpl_path)
                return False

        return True

    def resolveVarsFile(self,file_path, vars_dict, backup=False):
        str = loadFile(file_path)
        for var in vars_dict:
            if var not in vars_dict or not vars_dict[var]: continue
            str = re.sub('{{ ' + var + ' }}', vars_dict[var], str)
        if backup:
            if not execOSCommand("mv " + file_path + " " + file_path + '.' + str(int(time.time()))):
                loge ("Error: Couldn't backup " + file_path)
                return False
        try:
            with open(file_path, "w") as f:
                f.write(str)
        except EnvironmentError:
            loge ("Error: Cannot save updated " + file_path)
            return False

        return True

    def replaceTokensFile(self,file_path, token, backup=False):
        str = loadFile(file_path)
        pattern, replace = re.split(Tmpl.TMPL_KEY_VAL_DELIM,token)
        str = re.sub(pattern, replace, str)
        if backup:
            if not execOSCommand("mv " + file_path + " " + file_path + '.' + str(int(time.time()))):
                loge ("Error: Couldn't backup " + file_path)
                return False
        try:
            with open(file_path, "w") as f:
                f.write(str)
        except EnvironmentError:
            loge ("Error: Cannot save updated " + file_path)
            return False

        return True

''' Utility Functions '''

'''returns the status code after executing cmd in the shell'''
def runCmd(cmd):
    return subprocess.call(cmd,shell=True)

'''process return status from a command execution '''
def execOSCommand(cmd):
   rc=runCmd(cmd)
   if rc!=0:
       loge ("Error executing "+cmd)
       return False

   return True


'''returns the file content as a string.'''
def loadFile(file_path):
    s = open(file_path)
    str = s.read()
    s.close()

    return str


def Exit(rc):
    sys.exit(rc)

def getFileMD5(file_path):
    return hashlib.md5(open(file_path, 'rb').read()).hexdigest()

''' main '''

opkg_cmd=opkg(sys.argv)
opkg_cmd.main()
