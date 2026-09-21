# Build-script tests

Run as an ordinary user:

```sh
python3 tests/test_build_script.py
bash -n build.sh
./build.sh --dry-run
```

The Python suite uses isolated directories and stub commands. It checks distro
family selection, installed-package queries, package-manager transaction flags,
missing dependencies, failure propagation, build ordering, paths with spaces,
and symlink creation/replacement without running real package transactions.
The CLI test deliberately skips root because the build script rejects root runs.

A real end-to-end invocation of `./build.sh` was also checked on Nobara 44 with
an existing dependency set and build directory. This does not substitute for
fresh-machine installation tests on the other distributions.

Package recipes follow the renderer's CMake dependencies, its bundled CEF
runtime libraries, the upstream Arch package recipe, and the official
[Void package definitions](https://github.com/void-linux/void-packages/tree/master/srcpkgs). Package-manager behavior
is documented by [DNF5](https://dnf5.readthedocs.io/en/latest/commands/install.8.html),
[pacman](https://man.archlinux.org/man/pacman.8.en),
[zypper](https://doc.opensuse.org/documentation/tumbleweed/zypper/) and
[XBPS](https://man.voidlinux.org/xbps-install).
