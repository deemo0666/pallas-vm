<p align="center">
  <img src="assets/logo.png" width="160" alt="Pallas Agent">
</p>

<h1 align="center">Pallas Agent · Open-Source Component Sources</h1>

<p align="center"><a href="README.md">简体中文</a> · English</p>

Pallas Agent is a local personal agent for iPhone. The app carries a small Linux machine, run by QEMU, for executing commands and working with files. Some of the open-source software in that Linux machine and in QEMU is under copyleft licenses such as the GPL and LGPL. This repository provides the **complete corresponding source** for those components, **every change we made**, and build instructions.

Pallas Agent's own code is not in this repository and is not covered by these licenses. The app talks to QEMU only through command-line arguments and the QMP protocol (see "Component boundary" below).

## Contents

| Where | What |
|---|---|
| [Releases](../../releases) | Unmodified mirrors of each component's upstream source archive, with `SHA256SUMS` |
| [`qemu/`](qemu) | Our change to QEMU: `qemu-tcti-compare.patch` and the equivalent binary patcher `tcti_fix.py`; the license notice shipped in the app, `UTM-Licenses.plist` |
| [`qemu/utm-patches/`](qemu/utm-patches) | The build script, dependency list and patches UTM 5.0.8 was released with, from UTM's [v5.0.8 tag](https://github.com/utmapp/UTM/tree/v5.0.8) |
| [`guest/`](guest) | The Linux guest's kernel, busybox and Ubuntu package list |
| [`scripts/`](scripts) | A script that re-downloads every Ubuntu source package from Launchpad |
| [`licenses/`](licenses) | GPL-2.0 and LGPL-2.1 texts and QEMU's license notes |

## 1. QEMU and the libraries it links

The bundled QEMU comes from the [UTM SE 5.0.8](https://github.com/utmapp/UTM/releases/tag/v5.0.8) release: QEMU 10.0.12 with the iOS-TCI interpreter, which needs no JIT. We change only 8 bytes in `qemu-aarch64-softmmu`, fixing swapped conditions in the vector-compare gadgets; without it, Rust programs in the guest (ripgrep, for one) crash at random. The source patch is [`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch).

| Framework in the app | Upstream source | License |
|---|---|---|
| qemu-aarch64-softmmu | qemu-10.0.12-utm ([utmapp/qemu@6601422](https://github.com/utmapp/qemu/tree/6601422e1fff2da1376faafb1e4c2c5cdb2d8003)) + UTM patches + this repo's patch | GPL-2.0 |
| crypto.1.1 / ssl.1.1 | openssl-1.1.1b | OpenSSL / SSLeay |
| gio / glib / gmodule / gobject | glib-2.83.0 | LGPL-2.1+ |
| gstreamer / gstbase | gstreamer-1.19.1 | LGPL-2.1+ |
| gstapp | gst-plugins-base-1.19.1 | LGPL-2.1+ |
| spice-server | spice-0.14.3, spice-protocol-0.14.4 | LGPL-2.1+ |
| iconv | libiconv-1.16 | LGPL-2.1+ |
| intl | gettext-0.22.5 | LGPL-2.1+ |
| slirp | libslirp-v4.9.1 | BSD-3-Clause |
| pixman | pixman-0.38.0 | MIT |
| ffi | libffi-3.5.0 | MIT |
| jpeg | libjpeg-turbo-1.5.3 | IJG / BSD-3-Clause |
| opus | opus-1.3 | BSD-3-Clause |
| zstd | zstd-1.5.2 | BSD-3-Clause / GPL-2.0 |
| epoxy | utmapp/libepoxy@bf98587 | MIT |
| virglrenderer | utmapp/virglrenderer@451505a | MIT |
| vulkan | osy/Vulkan-Loader@6df8690 | Apache-2.0 |
| (statically linked into QEMU) | utmapp/libucontext@9b1d8f0 | ISC |

### Building

Install Xcode and the tools UTM's build needs (see UTM's [Documentation/Dependencies.md](https://github.com/utmapp/UTM/blob/v5.0.8/Documentation/Dependencies.md)), then download `UTM-v5.0.8.tar.gz` and the source archives from Releases:

```sh
tar xzf UTM-v5.0.8.tar.gz && cd UTM-5.0.8
# Append this repo's fix to UTM's QEMU patch; the build script applies both
cat ../qemu/qemu-tcti-compare.patch >> patches/qemu-10.0.12-utm.patch
# With the archives in place the script does not download them again
mkdir -p build-iOS-TCI-arm64 && cp ../*.tar.* build-iOS-TCI-arm64/
./scripts/build_dependencies.sh -p ios-tci -a arm64
```

The frameworks land in `sysroot-iOS-TCI-arm64/Frameworks/`. To replace the QEMU in the app, put your `qemu-aarch64-softmmu.framework` into the app bundle's `Frameworks/` and re-sign. The app calls it only through the `qemu_init` and `qemu_main_loop` entry points.

## 2. The Linux guest

| Part | Source | License |
|---|---|---|
| Kernel | Alpine 3.22.8 `linux-virt` 6.12.115-0: `linux-6.12.115.tar.xz` plus Alpine's [`main/linux-lts`](https://gitlab.alpinelinux.org/alpine/aports/-/tree/v3.22.8/main/linux-lts) config and patches | GPL-2.0 |
| busybox in the initramfs | Alpine 3.22.8 busybox 1.37.0-r20: `busybox-1.37.0.tar.bz2` plus [`main/busybox`](https://gitlab.alpinelinux.org/alpine/aports/-/tree/v3.22.8/main/busybox) | GPL-2.0 |
| musl in the initramfs | Alpine 3.22.8 | MIT |
| System disk | Ubuntu Base 24.04.5 arm64 plus bubblewrap, git, curl and their dependencies | Per package, see `/usr/share/doc/*/copyright` |

The system disk draws on 78 Ubuntu source packages, listed with exact versions in [`guest/source-packages.txt`](guest/source-packages.txt). Each package's `.dsc`, original source and Debian changes are packed as one `ubuntu-<name>_<version>.tar` in Releases, or run `python3 scripts/fetch-ubuntu-sources.py` to fetch them again from Launchpad.

The guest also carries Node.js, Python, ripgrep and similar components under permissive licenses (MIT, PSF, Apache); their license texts ship inside the guest.

## Component boundary

- QEMU is a separate dynamic framework. The app loads it with `dlopen` and calls only `qemu_init(argc, argv)` and `qemu_main_loop()`.
- The app and QEMU communicate only through a QMP (JSON command) pipe and network port forwarding. The app calls no QEMU internals and none of its code is compiled into QEMU.
- Programs in the Linux guest run inside the virtual machine and reach the app only over the network and 9p file sharing.

## Requesting source

This repository covers Pallas Agent 0.5.1 (17) and later, until a newer release appears here. If a component's source is missing or does not match the binary in the app, please [open an issue](../../issues). We will provide it for at least three years from each release.

## Logo

`assets/logo.png` is the Pallas Agent logo. You may use it to refer or link to Pallas Agent. It is not part of any open-source component above, and their licenses do not apply to it.
