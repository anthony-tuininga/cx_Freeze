"""
Base class for freezing scripts into executables.
"""

from __future__ import print_function

import datetime
import distutils.sysconfig
from importlib.util import MAGIC_NUMBER
import marshal
import os
import shutil
import socket
import stat
import struct
import sys
import sysconfig
import time
import zipfile

import cx_Freeze

__all__ = [ "ConfigError", "ConstantsModule", "Executable", "Freezer" ]

def process_path_specs(specs):
    """Prepare paths specified as config.

    The input is a list of either strings, or 2-tuples (source, target).
    Where single strings are supplied, the basenames are used as targets.
    Where targets are given explicitly, they must not be absolute paths.

    Returns a list of 2-tuples, or throws ConfigError if something is wrong
    in the input.
    """
    processedSpecs = []
    for spec in specs:
        if not isinstance(spec, (list, tuple)):
            source = spec
            target = None
        elif len(spec) != 2:
            raise ConfigError("path spec must be a list or tuple of "
                    "length two")
        else:
            source, target = spec
        source = os.path.normpath(source)
        if not target:
            target = os.path.basename(source)
        elif os.path.isabs(target):
            raise ConfigError("target path for include file may not be "
                    "an absolute path")
        processedSpecs.append((source, target))
    return processedSpecs

def get_resource_file_path(dirName, name, ext):
    """Return the path to a resource file shipped with cx_Freeze.

    This is used to find our base executables and initscripts when they are
    just specified by name.
    """
    if os.path.isabs(name):
        return name
    name = os.path.normcase(name)
    fullDir = os.path.join(os.path.dirname(cx_Freeze.__file__), dirName)
    if os.path.isdir(fullDir):
        for fileName in os.listdir(fullDir):
            checkName, checkExt = \
                    os.path.splitext(os.path.normcase(fileName))
            if name == checkName and ext == checkExt:
                return os.path.join(fullDir, fileName)


class Freezer(object):

    def __init__(self, executables, constantsModule = None, includes = [],
            excludes = [], packages = [], replacePaths = [], compress = True,
            optimizeFlag = 0, path = None, targetDir = None, binIncludes = [],
            binExcludes = [], binPathIncludes = [], binPathExcludes = [],
            includeFiles = [], zipIncludes = [], silent = False,
            namespacePackages = [], metadata = None, includeMSVCR = False,
            zipIncludePackages = [], zipExcludePackages = ["*"]):
        self.executables = list(executables)
        if constantsModule is None:
            constantsModule = ConstantsModule()
        self.constantsModule = constantsModule
        self.includes = list(includes)
        self.excludes = list(excludes)
        self.packages = list(packages)
        self.namespacePackages = list(namespacePackages)
        self.replacePaths = list(replacePaths)
        self.compress = compress
        self.optimizeFlag = optimizeFlag
        self.path = path
        self.includeMSVCR = includeMSVCR
        self.targetDir = targetDir
        self.binIncludes = [os.path.normcase(n) \
                for n in self._GetDefaultBinIncludes() + binIncludes]
        self.binExcludes = [os.path.normcase(n) \
                for n in self._GetDefaultBinExcludes() + binExcludes]
        self.binPathIncludes = [os.path.normcase(n) for n in binPathIncludes]
        self.binPathExcludes = [os.path.normcase(n) \
                for n in self._GetDefaultBinPathExcludes() + binPathExcludes]
        self.includeFiles = process_path_specs(includeFiles)
        self.zipIncludes = process_path_specs(zipIncludes)
        self.silent = silent
        self.metadata = metadata
        self.zipIncludePackages = list(zipIncludePackages)
        self.zipExcludePackages = list(zipExcludePackages)
        self._VerifyConfiguration()

    def _AddVersionResource(self, exe):
        warning_msg = "*** WARNING *** unable to create version resource"
        try:
            from win32verstamp import stamp
        except:
            print(warning_msg)
            print("install pywin32 extensions first")
            return
        if not self.metadata.version:
            print(warning_msg)
            print("version must be specified")
            return
        fileName = exe.targetName
        versionInfo = VersionInfo(self.metadata.version,
                comments = self.metadata.long_description,
                description = self.metadata.description,
                company = self.metadata.author,
                product = self.metadata.name,
                copyright = exe.copyright,
                trademarks = exe.trademarks)
        stamp(fileName, versionInfo)

    def _CopyFile(self, source, target, copyDependentFiles,
            includeMode = False, relativeSource = False):
        normalizedSource = os.path.normcase(os.path.normpath(source))
        normalizedTarget = os.path.normcase(os.path.normpath(target))
        if normalizedTarget in self.filesCopied:
            return
        if normalizedSource == normalizedTarget:
            return
        self._RemoveFile(target)
        targetDir = os.path.dirname(target)
        self._CreateDirectory(targetDir)
        if not self.silent:
            sys.stdout.write("copying %s -> %s\n" % (source, target))
        shutil.copyfile(source, target)
        shutil.copystat(source, target)
        if includeMode:
            shutil.copymode(source, target)
        self.filesCopied[normalizedTarget] = None
        if copyDependentFiles \
                and source not in self.finder.exclude_dependent_files:
            # Always copy dependent files on root directory
            # to allow to set relative reference
            if sys.platform == 'darwin':
                targetDir = self.targetDir
            sourceDir = os.path.dirname(source)
            for source in self._GetDependentFiles(source):
                if relativeSource and os.path.isabs(source) and \
                        os.path.commonpath((source, sourceDir)) == sourceDir:
                    relative = os.path.relpath(source, sourceDir)
                    target = os.path.join(targetDir, relative)
                else:
                    target = os.path.join(targetDir, os.path.basename(source))
                self._CopyFile(source, target, copyDependentFiles,
                               relativeSource=relativeSource)

    def _CreateDirectory(self, path):
        if not os.path.isdir(path):
            if not self.silent:
                sys.stdout.write("creating directory %s\n" % path)
            os.makedirs(path)

    def _FreezeExecutable(self, exe):
        finder = self.finder
        finder.IncludeFile(exe.script, exe.moduleName)
        finder.IncludeFile(exe.initScript, exe.initModuleName)
        startupModule = get_resource_file_path("initscripts", "__startup__",
                ".py")
        finder.IncludeFile(startupModule)

        # Copy the python dynamic libraries
        copyDependentFiles = True
        if sys.platform == "linux":
            # Always copy the python dynamic libraries into lib folder
            targetDir = os.path.join(os.path.dirname(exe.targetName), 'lib')
            dependentFiles = self._GetDependentFiles(exe.base) or \
                             self._GetDependentFiles(sys.executable)
            for source in dependentFiles:
                target = os.path.join(targetDir, os.path.basename(source))
                self._CopyFile(source, target,
                               copyDependentFiles=True, includeMode=True)
            copyDependentFiles = False
        elif sys.platform == "win32":
            # Copy the python dynamic libraries into build folder
            targetDir = os.path.dirname(exe.targetName)
            dependentFiles = self._GetDependentFiles(exe.base) or \
                             self._GetDependentFiles(sys.executable)
            # Ensure the copy of default python libraries
            sourceDir = os.path.dirname(dependentFiles[0])
            for name in self._GetDefaultBinIncludes():
                source = os.path.join(sourceDir, os.path.normcase(name))
                if source not in dependentFiles:
                    dependentFiles.append(source)
            for source in dependentFiles:
                target = os.path.join(targetDir, os.path.basename(source))
                self._CopyFile(source, target,
                               copyDependentFiles=True, includeMode=True)
            copyDependentFiles = False
        self._CopyFile(exe.base, exe.targetName,
                       copyDependentFiles=copyDependentFiles, includeMode=True)
        if not os.access(exe.targetName, os.W_OK):
            mode = os.stat(exe.targetName).st_mode
            os.chmod(exe.targetName, mode | stat.S_IWUSR)

        if self.includeMSVCR:
            self._IncludeMSVCR(exe)

        # Copy icon
        if exe.icon is not None:
            if sys.platform == "win32":
                import cx_Freeze.util
                cx_Freeze.util.AddIcon(exe.targetName, exe.icon)
            else:
                targetName = os.path.join(os.path.dirname(exe.targetName),
                                          os.path.basename(exe.icon))
                self._CopyFile(exe.icon, targetName,
                               copyDependentFiles=False)

        if self.metadata is not None and sys.platform == "win32":
            self._AddVersionResource(exe)

    def _GetDefaultBinExcludes(self):
        """Return the file names of libraries that need not be included because
           they would normally be expected to be found on the target system or
           because they are part of a package which requires independent
           installation anyway."""
        if sys.platform == "win32":
            return ["comctl32.dll", "oci.dll", "cx_Logging.pyd"]
        else:
            return ["libclntsh.so", "libwtc9.so", "ldd"]

    def _GetDefaultBinIncludes(self):
        """Return the file names of libraries which must be included for the
           frozen executable to work."""
        python_shared_libs = []
        if sys.platform == "win32":
            if sysconfig.get_platform() == "mingw":
                name = distutils.sysconfig.get_config_var("INSTSONAME")
                if name:
                    python_shared_libs.append(name.replace(".dll.a", ".dll"))
            else:
                python_shared_libs += ["python%s.dll" % sys.version_info[0],
                                       "python%s%s.dll" % sys.version_info[:2],
                                       "vcruntime140.dll"]
        else:
            name = distutils.sysconfig.get_config_var("INSTSONAME")
            if name:
                python_shared_libs.append(self._RemoveVersionNumbers(name))
        return python_shared_libs

    def _GetDefaultBinPathExcludes(self):
        """Return the paths of directories which contain files that should not
           be included, generally because they contain standard system
           libraries."""
        if sys.platform == "win32":
            import cx_Freeze.util
            systemDir = cx_Freeze.util.GetSystemDir()
            windowsDir = cx_Freeze.util.GetWindowsDir()
            return [windowsDir, systemDir, os.path.join(windowsDir, "WinSxS")]
        elif sys.platform == "darwin":
            return ["/lib", "/usr/lib", "/System/Library/Frameworks"]
        else:
            return ["/lib", "/lib32", "/lib64", "/usr/lib", "/usr/lib32",
                    "/usr/lib64"]

    def _GetDependentFiles(self, path):
        """Return the file's dependencies using platform-specific tools (the
           imagehlp library on Windows, otool on Mac OS X and ldd on Linux);
           limit this list by the exclusion lists as needed"""
        path = os.path.normcase(path)
        dirname = os.path.dirname(path)
        dependentFiles = self.dependentFiles.get(path)
        if dependentFiles is None:
            if sys.platform == "win32":
                if path.endswith(('.exe', '.dll', '.pyd')):
                    origPath = os.environ["PATH"]
                    os.environ["PATH"] = origPath + os.pathsep + \
                            os.pathsep.join(sys.path)
                    try:
                        dependentFiles = cx_Freeze.util.GetDependentFiles(path)
                    except cx_Freeze.util.BindError as exc:
                        # Sometimes this gets called when path is not actually a
                        # library See issue 88
                        dependentFiles = []
                        fmt = "error during GetDependentFiles() of \"%s\": %s\n"
                        sys.stderr.write(fmt % (path, str(exc)))
                    os.environ["PATH"] = origPath
                else:
                    dependentFiles = []
            else:
                if not os.access(path, os.X_OK):
                    self.dependentFiles[path] = []
                    return []
                dependentFiles = []
                if sys.platform == "darwin":
                    command = 'otool -L "%s"' % path
                    splitString = " (compatibility"
                    dependentFileIndex = 0
                else:
                    command = 'ldd "%s"' % path
                    splitString = " => "
                    dependentFileIndex = 1
                for line in os.popen(command):
                    parts = line.expandtabs().strip().split(splitString)
                    if len(parts) != 2:
                        continue
                    dependentFile = parts[dependentFileIndex].strip()
                    if dependentFile == os.path.basename(path):
                        continue
                    if dependentFile in ("not found", "(file not found)"):
                        fileName = parts[0]
                        if fileName not in self.linkerWarnings:
                            self.linkerWarnings[fileName] = None
                            message = "WARNING: cannot find %s\n" % fileName
                            sys.stdout.write(message)
                        continue
                    if dependentFile.startswith("("):
                        continue
                    pos = dependentFile.find(" (")
                    if pos >= 0:
                        dependentFile = dependentFile[:pos].strip()
                    if dependentFile:
                        dependentFiles.append(dependentFile)
                if sys.platform == "darwin":
                    # Make library paths absolute. This is needed to use
                    # cx_Freeze on OSX in e.g. a conda-based distribution.
                    # Note that with @rpath we just assume Python's lib dir,
                    # which should work in most cases.
                    dependentFiles = [p.replace('@loader_path', dirname)
                                      for p in dependentFiles]
                    dependentFiles = [p.replace('@rpath', sys.prefix + '/lib')
                                      for p in dependentFiles]
            if sys.platform == "darwin":
                dependentFiles = [self._CheckDependentFile(f, dirname)
                    for f in dependentFiles if self._ShouldCopyFile(f)]
            else:
                dependentFiles = [os.path.normcase(f)
                    for f in dependentFiles if self._ShouldCopyFile(f)]
            self.dependentFiles[path] = dependentFiles
        return dependentFiles

    def _CheckDependentFile(self, dependentFile, dirname):
        """If the file does not exist, try to locate it in the directory of the
           parent file (this is to workaround an issue in how otool returns
           dependencies. See issue #292.
           https://github.com/anthony-tuininga/cx_Freeze/issues/292"""
        if os.path.isfile(dependentFile):
            return dependentFile
        basename = os.path.basename(dependentFile)
        joined = os.path.join(dirname, basename)
        if os.path.isfile(joined):
            return joined
        raise FileNotFoundError("otool returned a dependent file that "
                "could not be found: " + dependentFile)

    def _GetModuleFinder(self, argsSource = None):
        if argsSource is None:
            argsSource = self
        finder = cx_Freeze.ModuleFinder(self.includeFiles, self.excludes,
                self.path, self.replacePaths, self.zipIncludeAllPackages,
                self.zipExcludePackages, self.zipIncludePackages,
                self.constantsModule, self.zipIncludes)
        finder.SetOptimizeFlag(self.optimizeFlag)
        for name in self.namespacePackages:
            finder.IncludeModule(name, namespace = True)
        for name in self.includes:
            finder.IncludeModule(name)
        for name in self.packages:
            finder.IncludePackage(name)
        return finder

    def _IncludeMSVCR(self, exe):
        targetDir = os.path.dirname(exe.targetName)
        for fullName in self.filesCopied:
            path, name = os.path.split(os.path.normcase(fullName))
            if name.startswith("msvcr") and name.endswith(".dll"):
                for otherName in [name.replace("r", c) for c in "mp"]:
                    sourceName = os.path.join(self.msvcRuntimeDir, otherName)
                    if not os.path.exists(sourceName):
                        continue
                    targetName = os.path.join(targetDir, otherName)
                    self._CopyFile(sourceName, targetName,
                            copyDependentFiles = False)
                break

    def _PrintReport(self, fileName, modules):
        sys.stdout.write("writing zip file %s\n\n" % fileName)
        sys.stdout.write("  %-25s %s\n" % ("Name", "File"))
        sys.stdout.write("  %-25s %s\n" % ("----", "----"))
        for module in modules:
            if module.path:
                sys.stdout.write("P")
            else:
                sys.stdout.write("m")
            sys.stdout.write(" %-25s %s\n" % (module.name, module.file or ""))
        sys.stdout.write("\n")

    def _RemoveFile(self, path):
        if os.path.exists(path):
            os.chmod(path, stat.S_IWRITE)
            os.remove(path)

    def _RemoveVersionNumbers(self, libName):
        tweaked = False
        parts = libName.split(".")
        while parts:
            if not parts[-1].isdigit():
                break
            parts.pop(-1)
            tweaked = True
        if tweaked:
            libName = ".".join(parts)
        return libName

    def _ShouldCopyFile(self, path):
        """Return true if the file should be copied to the target machine. This
           is done by checking the binPathIncludes, binPathExcludes,
           binIncludes and binExcludes configuration variables using first the
           full file name, then just the base file name, then the file name
           without any version numbers.

           Files are included unless specifically excluded but inclusions take
           precedence over exclusions."""

        # check for C runtime, if desired
        path = os.path.normcase(path)
        dirName, fileName = os.path.split(path)
        if fileName.startswith("msvcr") and fileName.endswith(".dll"):
            self.msvcRuntimeDir = dirName
            return self.includeMSVCR

        # check the full path
        if path in self.binIncludes:
            return True
        if path in self.binExcludes:
            return False

        # check the file name by itself (with any included version numbers)
        if fileName in self.binIncludes:
            return True
        if fileName in self.binExcludes:
            return False

        # check the file name by itself (version numbers removed)
        name = self._RemoveVersionNumbers(fileName)
        if name in self.binIncludes:
            return True
        if name in self.binExcludes:
            return False

        # check the path for inclusion/exclusion
        for path in self.binPathIncludes:
            if dirName.startswith(path):
                return True
        for path in self.binPathExcludes:
            if dirName.startswith(path):
                return False

        return True

    def _VerifyConfiguration(self):
        if self.compress is None:
            self.compress = True
        if self.targetDir is None:
            self.targetDir = os.path.abspath("dist")
        if self.path is None:
            self.path = sys.path

        for sourceFileName, targetFileName in \
                self.includeFiles + self.zipIncludes:
            if not os.path.exists(sourceFileName):
                raise ConfigError("cannot find file/directory named %s",
                        sourceFileName)
            if os.path.isabs(targetFileName):
                raise ConfigError("target file/directory cannot be absolute")

        self.zipExcludeAllPackages = "*" in self.zipExcludePackages
        self.zipIncludeAllPackages = "*" in self.zipIncludePackages
        if self.zipExcludeAllPackages and self.zipIncludeAllPackages:
            raise ConfigError("all packages cannot be included and excluded " \
                    "from the zip file at the same time")
        for name in self.zipIncludePackages:
            if name in self.zipExcludePackages:
                raise ConfigError("package %s cannot be both included and " \
                        "excluded from zip file", name)

        for executable in self.executables:
            executable._VerifyConfiguration(self)

    def _WriteModules(self, fileName, finder):
        self.constantsModule.Create(finder)
        modules = [m for m in finder.modules \
                if m.name not in self.excludeModules]
        modules.sort(key = lambda m: m.name)

        if not self.silent:
            self._PrintReport(fileName, modules)
        finder.ReportMissingModules()

        targetDir = os.path.dirname(fileName)
        self._CreateDirectory(targetDir)

        # Prepare zip file
        outFile = zipfile.PyZipFile(fileName, "w", zipfile.ZIP_DEFLATED)

        filesToCopy = []
        ignore_patterns_glob = ["*.py", "*.pyc", "*.pyo", "__pycache__"]
        if sys.platform == "linux":
            ignore_patterns_glob.extend(["*.so", "*.so.*"])
        ignore_patterns = shutil.ignore_patterns(*ignore_patterns_glob)
        for module in modules:

            # determine if the module should be written to the file system;
            # a number of packages make the assumption that files that they
            # require will be found in a location relative to where
            # they are located on disk; these packages will fail with strange
            # errors when they are written to a zip file instead
            includeInFileSystem = module.WillBeStoredInFileSystem()

            # if the module refers to a package, check to see if this package
            # should be included in the zip file or should be written to the
            # file system; if the package should be written to the file system,
            # data files (non-Python and non-DL files) are copied at this point
            # if the target directory does not already exist
            if includeInFileSystem and module.path is not None and \
                    module.file is not None:
                parts = module.name.split(".")
                target_package_dir = os.path.join(targetDir, *parts)
                if not os.path.exists(target_package_dir):
                    print("Copying data from package", module.name + "...")
                    source_package_dir = os.path.dirname(module.file)
                    shutil.copytree(source_package_dir, target_package_dir,
                                    ignore=ignore_patterns)

            # if an extension module is found in a package that is to be
            # included in a zip file, copy the actual file to the build
            # directory because shared libraries cannot be loaded from a
            # zip file
            if module.code is None and module.file is not None \
                    and not includeInFileSystem:
                parts = module.name.split(".")[:-1]
                parts.append(os.path.basename(module.file))
                target = os.path.join(targetDir, ".".join(parts))
                filesToCopy.append((module, target))

            # starting with Python 3.3 the pyc file format contains the source
            # size; it is not actually used for anything except determining if
            # the file is up to date so we can safely set this value to zero
            if module.code is not None:
                if module.file is not None and os.path.exists(module.file):
                    stat = os.stat(module.file)
                    mtime = stat.st_mtime
                    size = stat.st_size & 0xFFFFFFFF
                else:
                    mtime = time.time()
                    size = 0
                if sys.version_info[:2] < (3, 7):
                    header = MAGIC_NUMBER + struct.pack("<ii", int(mtime), size)
                else:
                    header = MAGIC_NUMBER + struct.pack("<iii", 0, int(mtime), size)
                data = header + marshal.dumps(module.code)

            # if the module should be written to the file system, do so
            if includeInFileSystem:
                parts = module.name.split(".")
                if module.code is None:
                    parts.pop()
                    parts.append(os.path.basename(module.file))
                    targetName = os.path.join(targetDir, *parts)
                    self._CopyFile(module.file, targetName,
                            copyDependentFiles = True)
                else:
                    if module.path is not None:
                        parts.append("__init__")
                    targetName = os.path.join(targetDir, *parts) + ".pyc"
                    open(targetName, "wb").write(data)

            # otherwise, write to the zip file
            elif module.code is not None:
                zipTime = time.localtime(mtime)[:6]
                fileName = "/".join(module.name.split("."))
                if module.path:
                    fileName += "/__init__"
                zinfo = zipfile.ZipInfo(fileName + ".pyc", zipTime)
                if self.compress:
                    zinfo.compress_type = zipfile.ZIP_DEFLATED
                outFile.writestr(zinfo, data)

            # put the distribution files metadata in the zip file
            if module.dist_files:
                for filepath, arcname in module.dist_files:
                    if arcname not in outFile.namelist():
                        outFile.write(filepath, arcname)

        # write any files to the zip file that were requested specially
        for sourceFileName, targetFileName in finder.zip_includes:
            if os.path.isdir(sourceFileName):
                for dirPath, _, fileNames in os.walk(sourceFileName):
                    basePath = dirPath[len(sourceFileName):]
                    targetPath = targetFileName + basePath.replace("\\", "/")
                    for name in fileNames:
                        outFile.write(os.path.join(dirPath, name),
                                targetPath + "/" + name)
            else:
                outFile.write(sourceFileName, targetFileName)

        outFile.close()

        # Copy Python extension modules from the list built above.
        origPath = os.environ["PATH"]
        for module, target in filesToCopy:
            try:
                if module.parent is not None:
                    path = os.pathsep.join([origPath] + module.parent.path)
                    os.environ["PATH"] = path
                self._CopyFile(module.file, target, copyDependentFiles=True,
                               relativeSource=(sys.platform == "linux"))
            finally:
                os.environ["PATH"] = origPath

    def Freeze(self):
        self.finder = None
        self.excludeModules = {}
        self.dependentFiles = {}
        self.filesCopied = {}
        self.linkerWarnings = {}
        self.msvcRuntimeDir = None

        self.finder = self._GetModuleFinder()
        for executable in self.executables:
            self._FreezeExecutable(executable)
        targetDir = self.targetDir
        zipTargetDir = os.path.join(targetDir, "lib")
        fileName = os.path.join(zipTargetDir, "library.zip")
        self._RemoveFile(fileName)
        self._WriteModules(fileName, self.finder)

        for sourceFileName, targetFileName in self.finder.include_files:
            if os.path.isdir(sourceFileName):
                # Copy directories by recursing into them.
                # Can't use shutil.copytree because we may need dependencies
                for path, dirNames, fileNames in os.walk(sourceFileName):
                    shortPath = path[len(sourceFileName) + 1:]
                    if ".svn" in dirNames:
                        dirNames.remove(".svn")
                    if "CVS" in dirNames:
                        dirNames.remove("CVS")
                    fullTargetDir = os.path.join(targetDir,
                            targetFileName, shortPath)
                    self._CreateDirectory(fullTargetDir)
                    for fileName in fileNames:
                        fullSourceName = os.path.join(path, fileName)
                        fullTargetName = os.path.join(fullTargetDir, fileName)
                        self._CopyFile(fullSourceName, fullTargetName,
                                copyDependentFiles = True)
            else:
                # Copy regular files.
                fullName = os.path.join(targetDir, targetFileName)
                self._CopyFile(sourceFileName, fullName,
                        copyDependentFiles = True)


class ConfigError(Exception):

    def __init__(self, format, *args):
        self.what = format % args

    def __str__(self):
        return self.what


class Executable(object):

    def __init__(self, script, initScript = None, base = None,
            targetName = None, icon = None, shortcutName = None,
            shortcutDir = None, copyright = None, trademarks = None):
        self.script = script
        self.initScript = initScript or "Console"
        self.base = base or "Console"
        self.targetName = targetName
        self.icon = icon
        self.shortcutName = shortcutName
        self.shortcutDir = shortcutDir
        self.copyright = copyright
        self.trademarks = trademarks

    def __repr__(self):
        return "<Executable script=%s>" % self.script

    def _VerifyConfiguration(self, freezer):
        self._GetInitScriptFileName()
        self._GetBaseFileName()
        if self.targetName is None:
            name, _ = os.path.splitext(os.path.basename(self.script))
            baseName, ext = os.path.splitext(self.base)
            self.targetName = name + ext
        name, ext = os.path.splitext(self.targetName)
        if ext == "" and sys.platform == "win32":
            self.targetName += ".exe"
        self.moduleName = "%s__main__" % os.path.normcase(name)
        self.initModuleName = "%s__init__" % os.path.normcase(name)
        self.targetName = os.path.join(freezer.targetDir, self.targetName)

    def _GetBaseFileName(self):
        name = self.base
        ext = ".exe" if sys.platform == "win32" else ""
        self.base = get_resource_file_path("bases", name, ext)
        if self.base is None:
            raise ConfigError("no base named %s", name)

    def _GetInitScriptFileName(self):
        name = self.initScript
        self.initScript = get_resource_file_path("initscripts", name, ".py")
        if self.initScript is None:
            raise ConfigError("no initscript named %s", name)


class ConstantsModule(object):

    def __init__(self, releaseString = None, copyright = None,
            moduleName="BUILD_CONSTANTS", timeFormat="%B %d, %Y %H:%M:%S",
            constants=[]):
        self.moduleName = moduleName
        self.timeFormat = timeFormat
        self.values = {}
        self.values["BUILD_RELEASE_STRING"] = releaseString
        self.values["BUILD_COPYRIGHT"] = copyright
        for constant in constants:
            parts = constant.split("=")
            if len(parts) == 1:
                name = constant
                value = None
            else:
                name, stringValue = parts
                value = eval(stringValue)
            self.values[name] = value

    def Create(self, finder):
        """Create the module which consists of declaration statements for each
           of the values."""
        today = datetime.datetime.today()
        sourceTimestamp = 0
        for module in finder.modules:
            if module.file is None:
                continue
            if module.source_is_zip_file:
                continue
            if not os.path.exists(module.file):
                raise ConfigError("no file named %s (for module %s)",
                        module.file, module.name)
            timestamp = os.stat(module.file).st_mtime
            sourceTimestamp = max(sourceTimestamp, timestamp)
        sourceTimestamp = datetime.datetime.fromtimestamp(sourceTimestamp)
        self.values["BUILD_TIMESTAMP"] = today.strftime(self.timeFormat)
        self.values["BUILD_HOST"] = socket.gethostname().split(".")[0]
        self.values["SOURCE_TIMESTAMP"] = \
                sourceTimestamp.strftime(self.timeFormat)
        module = finder._AddModule(self.moduleName)
        sourceParts = []
        names = list(self.values.keys())
        names.sort()
        for name in names:
            value = self.values[name]
            sourceParts.append("%s = %r" % (name, value))
        source = "\n".join(sourceParts)
        module.code = compile(source, "%s.py" % self.moduleName, "exec")
        return module


class VersionInfo(object):

    def __init__(self, version, internalName = None, originalFileName = None,
            comments = None, company = None, description = None,
            copyright = None, trademarks = None, product = None, dll = False,
            debug = False, verbose = True):
        parts = version.split(".")
        while len(parts) < 4:
            parts.append("0")
        self.version = ".".join(parts)
        self.internal_name = internalName
        self.original_filename = originalFileName
        self.comments = comments
        self.company = company
        self.description = description
        self.copyright = copyright
        self.trademarks = trademarks
        self.product = product
        self.dll = dll
        self.debug = debug
        self.verbose = verbose
