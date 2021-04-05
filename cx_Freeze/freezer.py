"""
Base class for freezing scripts into executables.
"""

from distutils.dist import DistributionMetadata
import distutils.sysconfig
from importlib.util import MAGIC_NUMBER
import marshal
import os
import shutil
import stat
import struct
import sys
import sysconfig
import time
from typing import Any, Dict, List, Optional
import zipfile

from .common import get_resource_file_path, process_path_specs
from .constantesmodule import ConstantsModule
from .darwintools import DarwinFile, MachOReference, DarwinFileTracker
from .exception import ConfigError
from .executable import Executable
from .finder import ModuleFinder
from .petools import get_pe_dependencies

if sys.platform == "linux":
    from .patchelf import Patchelf
if sys.platform == "win32":
    from . import winmsvcr
    from . import util as winutil
    from .winversioninfo import VersionInfo

    try:
        from win32verstamp import stamp as version_stamp
    except:
        version_stamp = None

__all__ = ["ConfigError", "ConstantsModule", "Executable", "Freezer"]


class Freezer:
    def __init__(
        self,
        executables: List["Executable"],
        constantsModule: Optional["ConstantsModule"] = None,
        includes: Optional[List[str]] = None,
        excludes: Optional[List[str]] = None,
        packages: Optional[List[str]] = None,
        replacePaths: Optional[List] = None,
        compress: bool = True,
        optimizeFlag: int = 0,
        path: Optional[List[str]] = None,
        targetDir: Optional[str] = None,
        binIncludes: Optional[List] = None,
        binExcludes: Optional[List] = None,
        binPathIncludes: Optional[List] = None,
        binPathExcludes: Optional[List] = None,
        includeFiles: Optional[List] = None,
        zipIncludes: Optional[List] = None,
        silent: bool = False,
        metadata: Optional[DistributionMetadata] = None,
        includeMSVCR: bool = False,
        zipIncludePackages: Optional[List[str]] = None,
        zipExcludePackages: Optional[List[str]] = None,
    ):
        self.executables = list(executables)
        self.constantsModule = constantsModule or ConstantsModule()
        self.includes = list(includes or [])
        self.excludes = list(excludes or [])
        self.packages = set(list(packages or []))
        self.replacePaths = list(replacePaths or [])
        self.compress = True if compress is None else compress
        self.optimize_flag = optimizeFlag
        self.path = sys.path if path is None else path
        self.include_msvcr = includeMSVCR
        self.targetdir = targetDir
        self.binIncludes = binIncludes
        self.binExcludes = binExcludes
        self.binPathIncludes = binPathIncludes
        self.binPathExcludes = binPathExcludes
        self.includeFiles = process_path_specs(includeFiles)
        self.zipIncludes = process_path_specs(zipIncludes)
        self.silent = silent
        self.metadata = metadata
        self.zipIncludePackages = zipIncludePackages
        self.zipExcludePackages = zipExcludePackages
        self._VerifyConfiguration()

    def _AddVersionResource(self, exe):
        warning_msg = "*** WARNING *** unable to create version resource"
        if version_stamp is None:
            print(warning_msg)
            print("install pywin32 extensions first")
            return
        if not self.metadata.version:
            print(warning_msg)
            print("version must be specified")
            return
        filename = os.path.join(self.targetdir, exe.target_name)
        versionInfo = VersionInfo(
            self.metadata.version,
            comments=self.metadata.long_description,
            description=self.metadata.description,
            company=self.metadata.author,
            product=self.metadata.name,
            copyright=exe.copyright,
            trademarks=exe.trademarks,
        )
        version_stamp(filename, versionInfo)

    def _CopyFile(
        self,
        source,
        target,
        copyDependentFiles,
        includeMode=False,
        machOReference: Optional[MachOReference] = None,
    ):
        if not self._ShouldCopyFile(source):
            return

        normalizedSource = os.path.normcase(os.path.normpath(source))
        normalizedTarget = os.path.normcase(os.path.normpath(target))
        norm_target_name = os.path.basename(normalizedTarget)

        # fix the target path for C runtime files
        if norm_target_name in self.runtime_files:
            target_name = os.path.basename(target)
            target = os.path.join(self.targetdir, "lib", target_name)
            # vcruntime140.dll must be in the root and in the lib directory
            if norm_target_name in self.runtime_files_to_dup:
                self.runtime_files_to_dup.remove(norm_target_name)
                self._CopyFile(source, target, copyDependentFiles=False)
                target = os.path.join(self.targetdir, target_name)
            normalizedTarget = os.path.normcase(os.path.normpath(target))

        if normalizedTarget in self.files_copied:
            if sys.platform == "darwin" and (machOReference is not None):
                # If file was already copied, and we are following a reference
                # from a DarwinFile, then we need to tell the reference where
                # the file was copied to (so the reference can later be updated).
                copiedDarwinFile = self.darwinTracker.getDarwinFile(
                    sourcePath=normalizedSource, targetPath=normalizedTarget
                )
                machOReference.setTargetFile(darwinFile=copiedDarwinFile)
            return
        if normalizedSource == normalizedTarget:
            return
        targetdir = os.path.dirname(target)
        self._CreateDirectory(targetdir)
        if not self.silent:
            print(f"copying {source} -> {target}")
        shutil.copyfile(source, target)
        shutil.copystat(source, target)
        if includeMode:
            shutil.copymode(source, target)
        self.files_copied.add(normalizedTarget)

        newDarwinFile = None
        if sys.platform == "darwin":
            # The file was not previously copied, so need to create a
            # DarwinFile file object to represent the file being copied.
            referencingFile = None
            if machOReference is not None:
                referencingFile = machOReference.sourceFile
            newDarwinFile = DarwinFile(
                originalFilePath=source, referencingFile=referencingFile
            )
            newDarwinFile.setBuildPath(normalizedTarget)
            if machOReference is not None:
                machOReference.setTargetFile(darwinFile=newDarwinFile)
            self.darwinTracker.recordCopiedFile(
                targetPath=normalizedTarget, darwinFile=newDarwinFile
            )

        if (
            copyDependentFiles
            and source not in self.finder.exclude_dependent_files
        ):
            # Always copy dependent files on root directory
            # to allow to set relative reference
            if sys.platform == "darwin":
                targetdir = self.targetdir
                for dependent_file in self._GetDependentFiles(
                    source, darwinFile=newDarwinFile
                ):
                    target = os.path.join(
                        targetdir, os.path.basename(dependent_file)
                    )
                    self._CopyFile(
                        dependent_file,
                        target,
                        copyDependentFiles=True,
                        machOReference=newDarwinFile.getMachOReferenceForPath(
                            path=dependent_file
                        ),
                    )
            elif sys.platform == "win32":
                for dependent_file in self._GetDependentFiles(source):
                    target = os.path.join(
                        targetdir, os.path.basename(dependent_file)
                    )
                    self._CopyFile(dependent_file, target, copyDependentFiles)
            else:
                source_dir = os.path.dirname(source)
                library_dir = os.path.join(self.targetdir, "lib")
                fix_rpath = set()
                for dependent_file in self._GetDependentFiles(source):
                    dep_base = os.path.basename(dependent_file)
                    dep_abs = os.path.abspath(dependent_file)
                    dep_rel = os.path.relpath(dep_abs, source_dir)
                    if targetdir == library_dir:
                        while dep_rel.startswith(os.pardir + os.sep):
                            dep_rel = dep_rel[len(os.pardir + os.sep) :]
                    dep_libs = dep_rel[: -(len(dep_base) + 1)]
                    if dep_libs:
                        fix_rpath.add(dep_libs)
                    dependent_target = os.path.join(targetdir, dep_rel)
                    self._CopyFile(
                        dependent_file,
                        dependent_target,
                        copyDependentFiles,
                    )
                if fix_rpath:
                    has_rpath = self.patchelf.get_rpath(target)
                    rpath = ":".join([f"$ORIGIN/{r}" for r in fix_rpath])
                    if has_rpath != rpath:
                        self.patchelf.set_rpath(target, rpath)

    def _CreateDirectory(self, path: str):
        if not self.silent and not os.path.isdir(path):
            print("creating directory %s" % path)
        os.makedirs(path, exist_ok=True)

    def _FreezeExecutable(self, exe):
        finder: ModuleFinder = self.finder
        finder.IncludeFile(exe.main_script, exe.main_module_name)
        finder.IncludeFile(exe.init_script, exe.init_module_name)
        finder.IncludeFile(
            get_resource_file_path("initscripts", "__startup__", ".py")
        )

        # Ensure the copy of default python libraries
        python_libs = tuple(self._GetDefaultBinIncludes())
        dependent_files = set()
        dependent_files.update(self._GetDependentFiles(exe.base))
        if not dependent_files:
            dependent_files.update(self._GetDependentFiles(sys.executable))
        if not dependent_files:
            for name in python_libs:
                source_dir = os.path.dirname(exe.base)
                source = os.path.join(source_dir, name)
                if os.path.isfile(source):
                    dependent_files.add(source)
        if not dependent_files:
            print("*** WARNING *** shared libraries not found:", python_libs)

        # Search the C runtimes, using the directory of the python libraries
        # and the directories of the base executable
        if sys.platform == "win32" and self.runtime_files:
            search_dirs = set()
            for filename in dependent_files:
                search_dirs.add(os.path.split(filename)[0])
            for filename in self.runtime_files:
                for search_dir in search_dirs:
                    filepath = os.path.join(search_dir, filename)
                    if os.path.exists(filepath):
                        dependent_files.add(filepath)

        # Always copy the dynamic libraries into lib folder
        target_dir = os.path.join(self.targetdir, "lib")
        for source in dependent_files:
            # but python dlls should be copied within the executable
            if sys.platform == "win32" and source.endswith(python_libs):
                target = os.path.join(self.targetdir, os.path.basename(source))
            else:
                target = os.path.join(target_dir, os.path.basename(source))
            if sys.platform == "darwin":
                # this recovers the cached MachOReference pointers to the files found
                # by the _GetDependentFiles calls above. If one is found, pass into _CopyFile.
                # We need to do this so the file knows what file referenced it, and can therefore
                # calculate the appropriate rpath.
                # (We only cache one reference.)
                cachedReference = self.darwinTracker.getCachedReferenceTo(
                    sourcePath=source
                )
                self._CopyFile(
                    source,
                    target,
                    copyDependentFiles=True,
                    includeMode=True,
                    machOReference=cachedReference,
                )
            else:
                self._CopyFile(
                    source, target, copyDependentFiles=True, includeMode=True
                )
        target_path = os.path.join(self.targetdir, exe.target_name)
        self._CopyFile(
            exe.base,
            target_path,
            copyDependentFiles=False,
            includeMode=True,
        )
        if not os.access(target_path, os.W_OK):
            mode = os.stat(target_path).st_mode
            os.chmod(target_path, mode | stat.S_IWUSR)

        # Copy icon
        if exe.icon is not None:
            if sys.platform == "win32":
                try:
                    winutil.AddIcon(target_path, exe.icon)
                except RuntimeError as exc:
                    print("*** WARNING ***", exc)
                except OSError as exc:
                    if "\\WindowsApps\\" in sys.base_prefix:
                        print(
                            "*** WARNING *** Because of restrictions on "
                            "Microsoft Store apps, Python scripts may not "
                            "have full write access to built executable.\n"
                            "You will need to install the full installer.\n"
                            "The following error was returned:"
                        )
                        print(exc)
                    else:
                        raise
            else:
                target_icon = os.path.join(
                    self.targetdir, os.path.basename(exe.icon)
                )
                self._CopyFile(exe.icon, target_icon, copyDependentFiles=False)

        if self.metadata is not None and sys.platform == "win32":
            self._AddVersionResource(exe)

    def _GetDefaultBinExcludes(self):
        """Return the file names of libraries that need not be included because
        they would normally be expected to be found on the target system or
        because they are part of a package which requires independent
        installation anyway."""
        if sys.platform == "win32":
            return ["comctl32.dll", "oci.dll", "cx_Logging.pyd"]
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
                python_shared_libs += [
                    "python%s.dll" % sys.version_info[0],
                    "python%s%s.dll" % sys.version_info[:2],
                ]
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
            systemDir = winutil.GetSystemDir()
            windowsDir = winutil.GetWindowsDir()
            return [windowsDir, systemDir, os.path.join(windowsDir, "WinSxS")]
        if sys.platform == "darwin":
            return ["/lib", "/usr/lib", "/System/Library/Frameworks"]
        return [
            "/lib",
            "/lib32",
            "/lib64",
            "/usr/lib",
            "/usr/lib32",
            "/usr/lib64",
        ]

    def _GetDependentFiles(self, path, darwinFile: DarwinFile = None) -> List:
        """Return the file's dependencies using platform-specific tools (the
        imagehlp library on Windows, otool on Mac OS X and ldd on Linux);
        limit this list by the exclusion lists as needed"""
        path = os.path.normcase(path)
        dependentFiles = self.dependentFiles.get(path, [])
        if not dependentFiles:
            if sys.platform == "win32":
                if path.endswith((".exe", ".dll", ".pyd")):
                    origPath = os.environ["PATH"]
                    os.environ["PATH"] = (
                        origPath + os.pathsep + os.pathsep.join(sys.path)
                    )
                    try:
                        dependentFiles = winutil.GetDependentFiles(path)
                    except winutil.BindError as exc:
                        # Sometimes this gets called when path is not actually a
                        # library See issue 88
                        print("error during GetDependentFiles() of ", end="")
                        print(f"{path!r}: {exc!s}")
                    os.environ["PATH"] = origPath
                    dependentFiles.extend(get_pe_dependencies(path))
            elif sys.platform == "darwin":
                # if darwinFile is None (which means that _GetDependentFiles is being called
                # outside of _CopyFile -- e.g., one of the preliminary calls in _FreezeExecutable),
                # create a temporary DarwinFile object for the path, just so we can read
                # its dependencies
                if darwinFile is None:
                    darwinFile = DarwinFile(
                        originalFilePath=path, referencingFile=None
                    )
                dependentFiles = darwinFile.getDependentFilePaths()

                # cache the MachOReferences to the dependencies, so they can be
                # called up later in _CopyFile if copying a dependency without
                # an explicit reference provided (to assist in resolving @rpaths)
                for reference in darwinFile.getMachOReferenceList():
                    if reference.isResolved():
                        self.darwinTracker.cacheReferenceTo(
                            sourcePath=reference.resolvedReferencePath,
                            machOReference=reference,
                        )
            else:
                if not os.access(path, os.X_OK):
                    self.dependentFiles[path] = []
                    return []
                command = f"ldd {path!r}"
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
                        filename = parts[0]
                        if filename not in self.linkerWarnings:
                            self.linkerWarnings[filename] = None
                            print("WARNING: cannot find %s" % filename)
                        continue
                    if dependentFile.startswith("("):
                        continue
                    pos = dependentFile.find(" (")
                    if pos >= 0:
                        dependentFile = dependentFile[:pos].strip()
                    if dependentFile:
                        dependentFiles.append(dependentFile)
            self.dependentFiles[path] = dependentFiles
        return dependentFiles

    def _GetModuleFinder(self) -> ModuleFinder:
        finder = ModuleFinder(
            self.includeFiles,
            self.excludes,
            self.path,
            self.replacePaths,
            self.zipIncludeAllPackages,
            self.zipExcludePackages,
            self.zipIncludePackages,
            self.constantsModule,
            self.zipIncludes,
        )
        finder.SetOptimizeFlag(self.optimize_flag)
        for name in self.includes:
            finder.IncludeModule(name)
        for name in self.packages:
            finder.IncludePackage(name)
        return finder

    def _PrintReport(self, filename, modules):
        print("writing zip file %s\n" % filename)
        print("  {:<25} {}".format("Name", "File"))
        print("  {:<25} {}".format("----", "----"))
        for module in modules:
            if module.path:
                print("P", end="")
            else:
                print("m", end="")
            print(" {:<25} {}\n".format(module.name, module.file or ""))

    def _RemoveVersionNumbers(self, filename):
        tweaked = False
        parts = filename.split(".")
        while parts:
            if not parts[-1].isdigit():
                break
            parts.pop(-1)
            tweaked = True
        if tweaked:
            filename = ".".join(parts)
        return filename

    def _ShouldCopyFile(self, path):
        """Return true if the file should be copied to the target machine. This
        is done by checking the binPathIncludes, binPathExcludes,
        binIncludes and binExcludes configuration variables using first the
        full file name, then just the base file name, then the file name
        without any version numbers.

        Files are included unless specifically excluded but inclusions take
        precedence over exclusions."""

        path = os.path.normcase(path)
        dirname, filename = os.path.split(path)

        # check the full path
        if path in self.binIncludes:
            return True
        if path in self.binExcludes:
            return False

        # check the file name by itself (with any included version numbers)
        if filename in self.binIncludes:
            return True
        if filename in self.binExcludes:
            return False

        # check the file name by itself (version numbers removed)
        name = self._RemoveVersionNumbers(filename)
        if name in self.binIncludes:
            return True
        if name in self.binExcludes:
            return False

        # check the path for inclusion/exclusion
        for binpath in self.binPathIncludes:
            if dirname.startswith(binpath):
                return True
        for binpath in self.binPathExcludes:
            if dirname.startswith(binpath):
                return False

        return True

    def _VerifyConfiguration(self):
        # starts external component
        if sys.platform == "linux":
            self.patchelf = Patchelf()

        # starts in a clean directory
        if self.targetdir is None:
            self.targetdir = os.path.abspath("dist")
        if os.path.isdir(self.targetdir):

            def onerror(*args):
                raise ConfigError("the build directory cannot be cleaned")

            shutil.rmtree(self.targetdir, onerror=onerror)

        # verify and normalize names and paths
        filenames = list(self.binIncludes or [])
        filenames += self._GetDefaultBinIncludes()
        self.binIncludes = [os.path.normcase(name) for name in filenames]

        filenames = list(self.binExcludes or [])
        filenames += self._GetDefaultBinExcludes()
        self.binExcludes = [os.path.normcase(name) for name in filenames]

        paths = list(self.binPathIncludes or [])
        paths += [path for path in self.path if os.path.isdir(path)]
        self.binPathIncludes = [os.path.normcase(name) for name in paths]

        paths = list(self.binPathExcludes or [])
        paths += self._GetDefaultBinPathExcludes()
        self.binPathExcludes = [os.path.normcase(name) for name in paths]

        # control runtime files
        self.runtime_files = set()
        self.runtime_files_to_dup = set()
        if sys.platform == "win32":
            if self.include_msvcr:
                self.runtime_files.update(winmsvcr.FILES)
                self.runtime_files_to_dup.update(winmsvcr.FILES_TO_DUPLICATE)
            else:
                # just put on the exclusion list
                self.binExcludes.extend(winmsvcr.FILES)

        for source, target in self.includeFiles + self.zipIncludes:
            if not os.path.exists(source):
                raise ConfigError(f"cannot find file/directory named {source}")
            if os.path.isabs(target):
                raise ConfigError("target file/directory cannot be absolute")

        if self.zipIncludePackages is None and self.zipExcludePackages is None:
            self.zipIncludePackages = []
            self.zipExcludePackages = ["*"]
        else:
            self.zipIncludePackages = list(self.zipIncludePackages or [])
            self.zipExcludePackages = list(self.zipExcludePackages or [])
        self.zipExcludeAllPackages = "*" in self.zipExcludePackages
        self.zipIncludeAllPackages = "*" in self.zipIncludePackages
        if self.zipExcludeAllPackages and self.zipIncludeAllPackages:
            raise ConfigError(
                "all packages cannot be included and excluded "
                "from the zip file at the same time"
            )
        for name in self.zipIncludePackages:
            if name in self.zipExcludePackages:
                raise ConfigError(
                    f"package {name!r} cannot be both included and "
                    "excluded from zip file"
                )

    def _WriteModules(self, filename, finder):
        self.constantsModule.create(finder)
        modules = [
            m for m in finder.modules if m.name not in self.excludeModules
        ]
        modules.sort(key=lambda m: m.name)

        if not self.silent:
            self._PrintReport(filename, modules)
        finder.ReportMissingModules()

        targetdir = os.path.dirname(filename)
        self._CreateDirectory(targetdir)

        # Prepare zip file
        if self.compress:
            compress_type = zipfile.ZIP_DEFLATED
        else:
            compress_type = zipfile.ZIP_STORED
        outFile = zipfile.PyZipFile(filename, "w", compress_type)

        filesToCopy = []
        ignorePatterns = shutil.ignore_patterns(
            "*.py", "*.pyc", "*.pyo", "__pycache__"
        )
        for module in modules:

            # determine if the module should be written to the file system;
            # a number of packages make the assumption that files that they
            # require will be found in a location relative to where
            # they are located on disk; these packages will fail with strange
            # errors when they are written to a zip file instead
            include_in_file_system = module.in_file_system

            # if the module refers to a package, check to see if this package
            # should be included in the zip file or should be written to the
            # file system; if the package should be written to the file system,
            # any non-Python files are copied at this point if the target
            # directory does not already exist
            if (
                include_in_file_system
                and module.path is not None
                and module.file is not None
            ):
                parts = module.name.split(".")
                targetPackageDir = os.path.join(targetdir, *parts)
                sourcePackageDir = os.path.dirname(module.file)
                if not os.path.exists(targetPackageDir):
                    if not self.silent:
                        print("Copying data from package", module.name + "...")
                    shutil.copytree(
                        sourcePackageDir,
                        targetPackageDir,
                        ignore=ignorePatterns,
                    )

            # if an extension module is found in a package that is to be
            # included in a zip file, copy the actual file to the build
            # directory because shared libraries cannot be loaded from a
            # zip file
            if (
                module.code is None
                and module.file is not None
                and not include_in_file_system
            ):
                parts = module.name.split(".")[:-1]
                parts.append(os.path.basename(module.file))
                target = os.path.join(targetdir, ".".join(parts))
                filesToCopy.append((module, target))

            # starting with Python 3.3 the pyc file format contains the source
            # size; it is not actually used for anything except determining if
            # the file is up to date so we can safely set this value to zero
            if module.code is not None:
                if module.file is not None and os.path.exists(module.file):
                    st = os.stat(module.file)
                    mtime = int(st.st_mtime)
                    size = st.st_size & 0xFFFFFFFF
                else:
                    mtime = int(time.time())
                    size = 0
                if sys.version_info[:2] < (3, 7):
                    header = MAGIC_NUMBER + struct.pack("<ii", mtime, size)
                else:
                    header = MAGIC_NUMBER + struct.pack("<iii", 0, mtime, size)
                data = header + marshal.dumps(module.code)

            # if the module should be written to the file system, do so
            if include_in_file_system and module.file is not None:
                parts = module.name.split(".")
                if module.code is None:
                    parts.pop()
                    parts.append(os.path.basename(module.file))
                    target_name = os.path.join(targetdir, *parts)
                    self._CopyFile(
                        module.file,
                        target_name,
                        copyDependentFiles=True,
                    )
                else:
                    if module.path is not None:
                        parts.append("__init__")
                    target_name = os.path.join(targetdir, *parts) + ".pyc"
                    with open(target_name, "wb") as fp:
                        fp.write(data)

            # otherwise, write to the zip file
            elif module.code is not None:
                zipTime = time.localtime(mtime)[:6]
                filename = "/".join(module.name.split("."))
                if module.path:
                    filename += "/__init__"
                zinfo = zipfile.ZipInfo(filename + ".pyc", zipTime)
                zinfo.compress_type = compress_type
                outFile.writestr(zinfo, data)

        # put the distribution files metadata in the zip file
        dist_cachedir = finder.dist_cachedir.name
        for dirpath, _, filenames in os.walk(dist_cachedir):
            basedir = dirpath[len(dist_cachedir) + 1 :].replace("\\", "/")
            for name in filenames:
                outFile.write(os.path.join(dirpath, name), f"{basedir}/{name}")

        # write any files to the zip file that were requested specially
        for source_filename, target_filename in finder.zip_includes:
            if os.path.isdir(source_filename):
                for dirPath, _, filenames in os.walk(source_filename):
                    basePath = dirPath[len(source_filename) :]
                    targetPath = target_filename + basePath.replace("\\", "/")
                    for name in filenames:
                        outFile.write(
                            os.path.join(dirPath, name),
                            targetPath + "/" + name,
                        )
            else:
                outFile.write(source_filename, target_filename)

        outFile.close()

        # Copy Python extension modules from the list built above.
        origPath = os.environ["PATH"]
        for module, target in filesToCopy:
            try:
                if module.parent is not None:
                    path = os.pathsep.join([origPath] + module.parent.path)
                    os.environ["PATH"] = path
                self._CopyFile(
                    module.file,
                    target,
                    copyDependentFiles=True,
                )
            finally:
                os.environ["PATH"] = origPath

    def Freeze(self):
        self.excludeModules = {}
        self.dependentFiles = {}  # type: Dict[Any, List]
        self.files_copied = set()
        self.linkerWarnings = {}

        self.darwinTracker = None  # type: Optional[DarwinFileTracker]
        if sys.platform == "darwin":
            self.darwinTracker = DarwinFileTracker()

        self.finder: ModuleFinder = self._GetModuleFinder()

        # Add the executables to target
        for executable in self.executables:
            self._FreezeExecutable(executable)

        # Write the modules
        targetdir = self.targetdir
        ziptargetdir = os.path.join(targetdir, "lib")
        filename = os.path.join(ziptargetdir, "library.zip")
        self._WriteModules(filename, self.finder)

        for source_filename, target_filename in self.finder.include_files:
            if os.path.isdir(source_filename):
                # Copy directories by recursing into them.
                # Can't use shutil.copytree because we may need dependencies
                for path, dirnames, filenames in os.walk(source_filename):
                    short_path = path[len(source_filename) + 1 :]
                    if ".svn" in dirnames:
                        dirnames.remove(".svn")
                    if "CVS" in dirnames:
                        dirnames.remove("CVS")
                    fulltargetdir = os.path.join(
                        targetdir, target_filename, short_path
                    )
                    self._CreateDirectory(fulltargetdir)
                    for filename in filenames:
                        source_path = os.path.join(path, filename)
                        target_path = os.path.join(fulltargetdir, filename)
                        self._CopyFile(
                            source_path,
                            target_path,
                            copyDependentFiles=True,
                        )
            else:
                # Copy regular files.
                fullname = os.path.join(targetdir, target_filename)
                self._CopyFile(
                    source_filename,
                    fullname,
                    copyDependentFiles=True,
                )
        # do a final pass to clean up dependency references in Mach-O files.
        if sys.platform == "darwin":
            self.darwinTracker.finalizeReferences()
