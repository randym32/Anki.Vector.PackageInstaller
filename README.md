# Vector Package Project

Vector Package is an open-source, platform independent packaging and deployment tool intended for use primarily on Vector.

# Basic Features 

Using Vector Package tool a package can be created using the following command:

```$ vector-pkg.py create --pkg=myapp```

The above command will create a tarball myapp.vpkg based on the meta-data defined in myapp.ini. In myapp.ini, you can specify the content of myapp.vpkg, and, how myapp is installed on a target host.

A package is installed on the local system as in the following example:

```$ vector-pkg.py install --pkg=/path/to/myapp.vpkg```

or:

```$ vector-pkg.py install --pkg=/path/to/myapp.tgz```

To uninstall a previously installed package:

```$ vector-pkg.py uninstall --pkg=myapp```

# Advantages of Vector Package Installer

- extremely simple packaging system that uses an open archive format, tarball.
- vector-pkg.py packages are flexible enough to be installed in environments with varied configuration.



# Package Manifest

The package manifest follows the .ini format as the extension of the file indicates. There is only one attribute, name of the package,  required in the manifest file. However, to use a package for anything useful, you may have to use multiple options that direct the pakaging of application code and its deployment.

There are 3 groups of options that you can specifiy in an vector-pkg.py manifest that are related to: 
- package meta data, 
- packaging files, 
- installing the package

A vpkg package also supports template variables in manifests, bounded by "{{ }}" - a feature that can be used to install a package to multiple environments with varied configurations.  

## Sample Package Manifest

Following is a sample package manifest with all the supported options used:

```
#Sample vpkg manifest file 
--- 

[META]
name=myapp
rel_num=1.2.3

#Package Content
[files]
#Destination         = Local source file
/Anki/greeting/hi.txt= hi.txt
/usr/apps= src/archives

#vector-pkg.py defined vars: vector-pkg.py_NAME, vector-pkg.py_REL_NUM, vector-pkg.py_ACTION
[vars]
MYAPP_ENV1= "sample env"
DB_NAME= myapp_db
DC_NAME= "{{ DC_NAME }}"

#The target paths are relative to vector-pkg.py_DEPLOY_DIR unless absolute path is specified.
[templates]
0=/Anki/greeting/hi.txt

#The target paths are relative to vector-pkg.py_DEPLOY_DIR unless absolute path is specified.
[replaces]
apps/conf/server.conf= HTTP_PORT=80: HTTP_PORT=9090

#The target paths are relative to vector-pkg.py_DEPLOY_DIR unless absolute path is specified.
[symlinks]
/etc/myapp/apps= apps

#The target paths are relative to vector-pkg.py_DEPLOY_DIR unless absolute path is specified.
#Format: PATH= chown_input chmod_input
[permissions]
apps= root:root 0444

```

## Package Meta-data
### name
Name of the package, required.

### rel_num
Release number of the package, optional. Default dev.


### files

Both files and directories can be specified as content of the package. 
```
/path/to/file/in/package: path/to/file/on/local/system
```
The local relative paths are with respect to the location of the manifest file and in the package the relative paths are with respect to the root directory.

Examples:
```
[files]
greeting/hi.txt= hi.txt
apps= src/archives
```
In the example above, 
- hi.txt which is located at the same directory as the package manifest is added to the package under the directory greeting. Note that the file name could be added with a different name too.
- the src/archives folder on the local system will be added the the package as directory apps.

If absolute paths are specified as source locations they are treated as such.

### vars

There are 2 groups of variables, system defined and externally provided. Both types of variables are meant to be used during deployment and maintenance of corresponding package.

These variables are available in the runtime environment so deployment and runtime maintenance scripts can use them. The templates used in a package will be resolved with these variables and it is a powerful tool to configure an application environment. We will see how it is done in the "templates" section.

#### System Defined Variables
The package tool makes these variables available for templates and scripts:

- OPKG_NAME: Name of package being deployed or run.
- OPKG_REL_NUM: Release number of the package being deployed or run.
- OPKG_ACTION: The action being done using package tool, like install, start etc.
- OPKG_TS: A timestamp used to track a deployment session across packages.

#### Externally Provided Variables
To make these variable available to templates and scripts, they must be defined in the manifest as in the following example:
```
[vars]
MYAPP_ENV1= "sample env"
DB_NAME= {{ myapp_db }}
DC_NAME= "{{ dc_name }}"
```
Using --extra-vars option, values for these variables can be provided during deployment and they are resolved at that time. During runtime, like start and stop of a package, these variables will be available in the environment for the scripts to use with the values set during deployment. 

The values to these variables can be provided from the command-line using the following syntax, each hash-value pair delimited by a comma:
```
--extra-vars=myapp_db=westcoast,dc_name=us-west
```

## Install Code

Multiple packages can be specified in a deployment session as below:

```
$ vector-pkg.py install --pkg=pkg1-latest,pkg2-latest,pkg3-1.2.3
```
In this case, latest versions of pk1 and pkg2 and pkg3-1.2.3 will be deployed. 


### templates

This is a powerful option to make environment specific changes to the generic application configuration files maintained source code control system, as in the following example:
```
[templates]
0=/hi.txt
1=apps/conf
```
The paths are relative to vector-pkg.py_DEPLOY_DIR unless an absolute path is specified. If a file is specified, vector-pkg.py will look at that file for template variables specified in the format {{ var_name }}. If found, {{ var_name }} is replaced with related hash value. If the hash value is not available, package tool will throw error.

If a directory is specified, all the files in that directory will be checked for template variables. This is not recursive and so sub-directories have to be specified, if needed.

As a best practice, configuration files with template variables must be limited to few directories and files for simpler handling and minimizing deployment errors.

### replaces

This is another available to configure applications for a specific environment. While "templates" option rely on template variables that are enclosed with "{{ }}" in a file and such instrumentations will be possible only in your own applications. If some change has to be done in a third-party application, for example Tomcat Server that runs your application war file, then you have to use this search and replace option.

```
[replaces]
apps/conf/server.conf=HTTP_PORT=80:HTTP_PORT=9090
```
Using this option, multiple files or directories can be specified in which tokens have to be replaced with values provided from the manifest. In the example, in OPKG_DEPLOY_DIR/apps/conf/server.conf, every occurance of "HTTP_PORT=80" will be replaced with "HTTP_PORT=9090".

### symlinks

This option is used mainly to mark the latest deployment as the currently one. However, any number of symlinks can be defined on the target host using this option. 
```
[symlinks]
/etc/myapp/apps=apps
```
The hash key in this specification is the symlink and the hash value is the source. The package tool doesn't make any validation checks on these paths and it's up to the designer of the package to specify appropriate paths. The package tool will try to create those as part of this installation step.

### permissions

The permission settings that can be implemented using chown and chmod commands can be set using this option.

```
[permissions]
apps=root:root 0444
```
In the example above, the changes will be same as that done by the following commands:
```
$ chown -R root:root OPKG_DEPLOY_DIR/apps
$ chmod -R 0444 OPKG_DEPLOY_DIR/apps
```

### rollback

The vector-pkg.py includes support for uninstalling a package.  It is rudimentary, restoring files that were altered.

