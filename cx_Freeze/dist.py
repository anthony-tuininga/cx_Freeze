import distutils.command.bdist_rpm
import distutils.command.build
import distutils.command.install
import distutils.core
import distutils.dir_util
import distutils.dist
import distutils.errors
import distutils.log
import distutils.util
import distutils.version
import os
import sys
import warnings

from .common import normalize_to_list
from .freezer import Freezer
from .module import ConstantsModule

if sys.platform == "win32":
    from .windist import bdist_msi
elif sys.platform == "darwin":
    from .macdist import bdist_dmg, bdist_mac

__all__ = [
    "bdist_rpm",
    "build",
    "build_exe",
    "install",
    "install_exe",
    "setup",
]


class Distribution(distutils.dist.Distribution):
    def __init__(self, attrs):
        self.executables = []
        distutils.dist.Distribution.__init__(self, attrs)


class bdist_rpm(distutils.command.bdist_rpm.bdist_rpm):
    def finalize_options(self):
        distutils.command.bdist_rpm.bdist_rpm.finalize_options(self)
        self.use_rpm_opt_flags = 1

    def _make_spec_file(self):
        contents = distutils.command.bdist_rpm.bdist_rpm._make_spec_file(self)
        contents.append("%define __prelink_undo_cmd %{nil}")
        return [c for c in contents if c != "BuildArch: noarch"]


class build(distutils.command.build.build):
    user_options = distutils.command.build.build.user_options + [
        ("build-exe=", None, "build directory for executables")
    ]

    def get_sub_commands(self):
        sub_commands = distutils.command.build.build.get_sub_commands(self)
        if self.distribution.executables:
            sub_commands.append("build_exe")
        return sub_commands

    def initialize_options(self):
        distutils.command.build.build.initialize_options(self)
        self.build_exe = None

    def finalize_options(self):
        distutils.command.build.build.finalize_options(self)
        if self.build_exe is None:
            platform = distutils.util.get_platform()
            ver_major, ver_minor = sys.version_info[0:2]
            dir_name = f"exe.{platform}-{ver_major}.{ver_minor}"
            self.build_exe = os.path.join(self.build_base, dir_name)


class build_exe(distutils.core.Command):
    description = "build executables from Python scripts"
    user_options = [
        ("build-exe=", "b", "directory for built executables"),
        (
            "optimize=",
            "O",
            'optimization level: -O1 for "python -O", '
            '-O2 for "python -OO" and -O0 to disable [default: -O0]',
        ),
        ("excludes=", "e", "comma-separated list of modules to exclude"),
        ("includes=", "i", "comma-separated list of modules to include"),
        ("packages=", "p", "comma-separated list of packages to include"),
        ("namespace-packages=", None, "[DEPRECATED]"),
        (
            "replace-paths=",
            None,
            "comma-separated list of paths to replace in included modules",
        ),
        ("path=", None, "comma-separated list of paths to search"),
        ("no-compress", None, "create a zipfile with no compression"),
        ("constants=", None, "comma-separated list of constants to include"),
        (
            "include-files=",
            "f",
            "list of tuples of additional files to include in distribution",
        ),
        (
            "include-msvcr=",
            None,
            "include the Microsoft Visual C runtime files",
        ),
        (
            "zip-includes=",
            None,
            "list of tuples of additional files to include in zip file",
        ),
        (
            "bin-includes",
            None,
            "list of names of files to include when determining dependencies",
        ),
        (
            "bin-excludes",
            None,
            "list of names of files to exclude when determining dependencies",
        ),
        (
            "bin-path-includes",
            None,
            "list of paths from which to include files when determining "
            "dependencies",
        ),
        (
            "bin-path-excludes",
            None,
            "list of paths from which to exclude files when determining "
            "dependencies",
        ),
        (
            "zip-include-packages=",
            None,
            "comma-separated list of packages to include in the zip file "
            "(or * for all) [default: none]",
        ),
        (
            "zip-exclude-packages=",
            None,
            "comma-separated list of packages to exclude from the zip file "
            "and place in the file system instead (or * for all) "
            "[default: *]",
        ),
        ("silent", "s", "suppress all output except warnings (equivalent to --silent-level=1)"),
        (
            "silent-level=",
            None,
            "suppress output from build_exe command.  "
            "level 0: get all messages; [default]"
            "level 1: suppress information messages, but still get warnings; (equivalent to --silent)"
            "level 2: suppress missing missing-module warnings "
            "level 3: suppress all warning messages"
        ),
    ]
    boolean_options = ["no-compress", "include_msvcr", "silent"]

    def add_to_path(self, name):
        source_dir = getattr(self, name.lower())
        if source_dir is not None:
            sys.path.insert(0, source_dir)

    def build_extension(self, name, moduleName=None):
        if moduleName is None:
            moduleName = name
        source_dir = getattr(self, name.lower())
        if source_dir is None:
            return
        orig_dir = os.getcwd()
        script_args = ["build"]
        command = self.distribution.get_command_obj("build")
        if command.compiler is not None:
            script_args.append("--compiler=%s" % command.compiler)
        os.chdir(source_dir)
        distutils.log.info("building '%s' extension in '%s'", name, source_dir)
        distribution = distutils.core.run_setup("setup.py", script_args)
        modules = [m for m in distribution.ext_modules if m.name == moduleName]
        if not modules:
            message_format = "no module named '%s' in '%s'"
            raise distutils.errors.DistutilsSetupError(
                message_format % (moduleName, source_dir)
            )
        command = distribution.get_command_obj("build_ext")
        command.ensure_finalized()
        if command.compiler is None:
            command.run()
        else:
            command.build_extensions()
        dir_name = os.path.join(source_dir, command.build_lib)
        os.chdir(orig_dir)
        if dir_name not in sys.path:
            sys.path.insert(0, dir_name)
        return os.path.join(
            source_dir, command.build_lib, command.get_ext_filename(moduleName)
        )

    def initialize_options(self):
        self.list_options = [
            "excludes",
            "includes",
            "packages",
            "namespace_packages",
            "replace_paths",
            "constants",
            "include_files",
            "zip_includes",
            "bin_excludes",
            "bin_includes",
            "bin_path_includes",
            "bin_path_excludes",
            "zip_include_packages",
            "zip_exclude_packages",
        ]

        for option in self.list_options:
            setattr(self, option, [])

        self.zip_exclude_packages = "*"
        self.optimize = 0
        self.build_exe = None
        self.no_compress = False
        self.path = None
        self.include_msvcr = None
        self.silent = None
        self.silent_level = None

    def finalize_options(self):
        self.set_undefined_options("build", ("build_exe", "build_exe"))
        self.optimize = int(self.optimize)

        self.silent_setting = 0  # the degree of silencing, set from either the silent or silent-level
                                 # option, as appropriate
        if self.silent is not None and self.silent:
            self.silent_setting = 1

        if self.silent_level is None: pass
        elif self.silent_level is False: self.silent_setting = 0
        elif self.silent_level is True: self.silent_setting = 1
        elif isinstance(self.silent_level, int): self.silent_setting = self.silent_level
        elif isinstance(self.silent_level, str):
            try: self.silent_setting = int(self.silent_level)
            except ValueError: self.silent_setting = 1
        else: self.silent_setting = 1

        # Make sure all options of multiple values are lists
        for option in self.list_options:
            setattr(self, option, normalize_to_list(getattr(self, option)))

    def run(self):
        metadata = self.distribution.metadata
        constants_module = ConstantsModule(
            metadata.version, constants=self.constants
        )
        if self.namespace_packages:
            warnings.warn(
                "namespace-packages is obsolete and will be removed in the next version"
            )
        freezer = Freezer(
            self.distribution.executables,
            constants_module,
            self.includes,
            self.excludes,
            self.packages,
            self.replace_paths,
            (not self.no_compress),
            self.optimize,
            self.path,
            self.build_exe,
            includeMSVCR=self.include_msvcr,
            includeFiles=self.include_files,
            binIncludes=self.bin_includes,
            binExcludes=self.bin_excludes,
            zipIncludes=self.zip_includes,
            silent=self.silent_setting,
            binPathIncludes=self.bin_path_includes,
            binPathExcludes=self.bin_path_excludes,
            metadata=metadata,
            zipIncludePackages=self.zip_include_packages,
            zipExcludePackages=self.zip_exclude_packages,
        )

        # keep freezer around so that its data case be used in bdist_mac phase
        self.freezer = freezer
        freezer.Freeze()

    def set_source_location(self, name, *pathParts):
        env_name = "%s_BASE" % name.upper()
        attr_name = name.lower()
        source_dir = getattr(self, attr_name)
        if source_dir is None:
            base_dir = os.environ.get(env_name)
            if base_dir is None:
                return
            source_dir = os.path.join(base_dir, *pathParts)
            if os.path.isdir(source_dir):
                setattr(self, attr_name, source_dir)


class install(distutils.command.install.install):
    user_options = distutils.command.install.install.user_options + [
        ("install-exe=", None, "installation directory for executables")
    ]

    def expand_dirs(self):
        distutils.command.install.install.expand_dirs(self)
        self._expand_attrs(["install_exe"])

    def get_sub_commands(self):
        sub_commands = distutils.command.install.install.get_sub_commands(self)
        if self.distribution.executables:
            sub_commands.append("install_exe")
        return [s for s in sub_commands if s != "install_egg_info"]

    def initialize_options(self):
        distutils.command.install.install.initialize_options(self)
        self.install_exe = None

    def finalize_options(self):
        if self.prefix is None and sys.platform == "win32":
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE,
                r"Software\Microsoft\Windows\CurrentVersion",
            )
            prefix = str(winreg.QueryValueEx(key, "ProgramFilesDir")[0])
            metadata = self.distribution.metadata
            self.prefix = f"{prefix}/{metadata.name}"
        distutils.command.install.install.finalize_options(self)
        self.convert_paths("exe")
        if self.root is not None:
            self.change_roots("exe")

    def select_scheme(self, name):
        distutils.command.install.install.select_scheme(self, name)
        if self.install_exe is None:
            if sys.platform == "win32":
                self.install_exe = "$base"
            else:
                metadata = self.distribution.metadata
                dir_name = f"{metadata.name}-{metadata.version}"
                self.install_exe = "$base/lib/%s" % dir_name


class install_exe(distutils.core.Command):
    description = "install executables built from Python scripts"
    user_options = [
        ("install-dir=", "d", "directory to install executables to"),
        ("build-dir=", "b", "build directory (where to install from)"),
        ("force", "f", "force installation (overwrite existing files)"),
        ("skip-build", None, "skip the build steps"),
    ]

    def initialize_options(self):
        self.install_dir = None
        self.force = 0
        self.build_dir = None
        self.skip_build = None

    def finalize_options(self):
        self.set_undefined_options("build", ("build_exe", "build_dir"))
        self.set_undefined_options(
            "install",
            ("install_exe", "install_dir"),
            ("force", "force"),
            ("skip_build", "skip_build"),
        )

    def run(self):
        if not self.skip_build:
            self.run_command("build_exe")
        self.outfiles = self.copy_tree(self.build_dir, self.install_dir)
        if sys.platform != "win32":
            base_dir = os.path.dirname(os.path.dirname(self.install_dir))
            bin_dir = os.path.join(base_dir, "bin")
            if not os.path.exists(bin_dir):
                os.makedirs(bin_dir)
            source_dir = os.path.join(
                "..", self.install_dir[len(base_dir) + 1 :]
            )
            for executable in self.distribution.executables:
                name = os.path.basename(executable.target_name)
                source = os.path.join(source_dir, name)
                target = os.path.join(bin_dir, name)
                if os.path.exists(target):
                    os.unlink(target)
                os.symlink(source, target)
                self.outfiles.append(target)

    def get_inputs(self):
        return self.distribution.executables or []

    def get_outputs(self):
        return self.outfiles or []


def _AddCommandClass(command_classes, name, cls):
    if name not in command_classes:
        command_classes[name] = cls


def setup(**attrs):
    attrs.setdefault("distclass", Distribution)
    command_classes = attrs.setdefault("cmdclass", {})
    if sys.platform == "win32":
        _AddCommandClass(command_classes, "bdist_msi", bdist_msi)
    elif sys.platform == "darwin":
        _AddCommandClass(command_classes, "bdist_dmg", bdist_dmg)
        _AddCommandClass(command_classes, "bdist_mac", bdist_mac)
    else:
        _AddCommandClass(command_classes, "bdist_rpm", bdist_rpm)
    _AddCommandClass(command_classes, "build", build)
    _AddCommandClass(command_classes, "build_exe", build_exe)
    _AddCommandClass(command_classes, "install", install)
    _AddCommandClass(command_classes, "install_exe", install_exe)
    distutils.core.setup(**attrs)
