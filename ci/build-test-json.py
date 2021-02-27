import json
import os
import subprocess
import sys
import sysconfig

FILENAME = os.path.join(os.path.dirname(sys.argv[0]), "build-test.json")
IS_MINGW = sysconfig.get_platform() == "mingw"
IS_LINUX = sys.platform == "linux"
HOSTTYPE = (
    os.environ.get("HOSTTYPE")
    or (sysconfig.get_config_var("HOST_GNU_TYPE") or "-").split("-")[0]
)

if len(sys.argv) != 3:
    sys.exit(1)

with open(FILENAME) as fp:
    data = json.load(fp)

name = sys.argv[1]
func = sys.argv[2]
test_data = data.get(name, {})

# verify if platform to run is in use
platform = test_data.get("platform", [])
if isinstance(platform, str):
    platform = [platform]
if platform:
    if IS_MINGW:
        platform_in_use = "mingw"
    else:
        platform_in_use = sys.platform
    platform_support = {"darwin", "linux", "mingw", "win32"}
    platform_yes = {plat for plat in platform if not plat.startswith("!")}
    if platform_yes:
        platform_support = platform_yes
    platform_not = {plat[1:] for plat in platform if plat.startswith("!")}
    if platform_not:
        platform_support -= platform_not
    if platform_in_use not in platform_support:
        sys.exit()

# process requirements
if func == "req":
    requires = test_data.get("requirements", [])
    if isinstance(requires, str):
        requires = [requires]
    requires = (
        "pip,setuptools,wheel,importlib-metadata".split(",") + requires
    )
    out = []
    pkg = []
    for req in requires:
        if ";" in req:
            require = req.replace(" ", "")
        else:
            require = req
        if IS_LINUX and require.startswith("wxPython"):
            output = subprocess.check_output(
                [
                    "pip",
                    "install",
                    "-f",
                    "https://extras.wxpython.org/wxPython4/extras/linux/gtk3/ubuntu-18.04",
                    "wxPython",
                ]
            )
        elif IS_MINGW and ";sys_platform!='mingw'" in require:
            continue
        elif not IS_MINGW and ";sys_platform=='mingw'" in require:
            continue
        else:
            if IS_MINGW:
                # TODO: use regex
                package = require.split(";")[0].split("<")[0].split("=")[0]
                packages = [f"python-{package}", package]
                if package != package.lower():
                    packages.append(f"python-{package.lower()}")
                    packages.append(package.lower())
                installed = False
                for package in packages:
                    try:
                        output = subprocess.check_output(
                            [
                                "pacman",
                                "--noconfirm",
                                "-S",
                                "--needed",
                                f"mingw-w64-{HOSTTYPE}-{package}",
                            ]
                        )
                    except subprocess.CalledProcessError:
                        pass
                    else:
                        installed = True
                        break
                if installed:
                    pkg.append(package)
                    continue
            output = subprocess.check_output(
                ["python", "-m", "pip", "install", "--upgrade", require]
            )
            pkg.append(require)
        out.append(output.decode())
    print("\n".join(out), file=sys.stderr)
    print(" ".join(pkg))

else:  # app number
    test_app = test_data.get("test_app", [f"test_{name}"])
    if isinstance(test_app, str):
        test_app = [test_app]
    line = int(func or 0)
    #for app in test_app[:]:
    #    if IS_MINGW and app.startswith("gui:"):
    #        test_app.remove(app)
    if line < len(test_app):
        print(test_app[line])
