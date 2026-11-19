<p align="center">
  <img src="assets/logo.png" width="160" alt="Pallas VM">
</p>

<h1 align="center">Pallas VM</h1>

<p align="center"><b>A JIT-free virtual machine runtime that runs full aarch64 Linux inside an iOS app sandbox</b></p>

<p align="center">English · <a href="README.zh-CN.md">简体中文</a></p>

<p align="center">
  <img alt="QEMU" src="https://img.shields.io/badge/QEMU-10.0.12-orange">
  <img alt="TCG" src="https://img.shields.io/badge/TCG-iOS--TCI-blue">
  <img alt="Guest" src="https://img.shields.io/badge/guest-Linux%206.12%20aarch64-black">
  <img alt="Resume" src="https://img.shields.io/badge/resume-~1.6%20s-green">
</p>

Pallas VM is the execution layer of [Pallas Agent](https://github.com/deemo0666/pallasai). The shell, Python, npm and git that the agent runs on an iPhone all run in this virtual machine, inside the app's own process. No jailbreak, no remote server, no JIT entitlement.

iOS forbids ordinary apps from generating executable code (W^X, no `MAP_JIT`). Pallas VM puts QEMU's TCG front end in front of the **iOS-TCI threaded-code back end**: guest instructions become TCG IR, then chains of addresses of precompiled gadgets that an indirect-branch loop executes. Only data is ever written.

## Performance

Measured on iPhone, interpreter only, no JIT.

| | Stock setup | Pallas VM |
|---|---|---|
| Cold boot to first command | 28 s | **11 s** |
| Launch from saved machine | — | **~1.6 s** |
| `rg` / `grep` / `find` over the workspace | in guest, over 9p | **native in host, identical output** |
| Rust programs using SIMD hash tables | crash at random | **correct** (TCTI fix) |

Profiling showed instruction translation is under 5% of the cost; 9p round trips and boot work dominate. Every change below attacks one of those.

## Compared with iSH

[iSH](https://github.com/ish-app/ish) pioneered Linux on iOS without JIT, and its threaded-code ("gadget") interpreter is the same family of technique. The two make opposite architectural choices:

| | iSH | Pallas VM |
|---|---|---|
| Emulation model | User-mode: runs Linux binaries, translates syscalls to XNU | Full system: a real Linux kernel on an emulated `virt` machine |
| Guest ISA | 32-bit x86 (i386) | **64-bit aarch64**, the same ISA as the iPhone |
| Kernel | Reimplemented syscall layer | **Unmodified Linux 6.12** |
| Userland | Alpine i386 (musl) | **Ubuntu 24.04 arm64 (glibc)**: official arm64 builds of Node.js, Python, Rust tools run as is |
| 64-bit programs | Not supported | Supported |
| Kernel features (namespaces, cgroups, ext4, 9p, bubblewrap sandboxing) | Only what the syscall layer reimplements | Whatever Linux has |
| Start-up | No kernel to boot | Whole-machine snapshot, ~1.6 s |
| Integration | Standalone terminal app | Embedded runtime driven by an agent over QMP and loopback services |

User-mode emulation is lighter and has no boot at all; full-system emulation buys compatibility: a real kernel means real semantics, and an arm64 guest means the modern toolchain ecosystem works without 32-bit x86 ports.

## What we changed, layer by layer

### TCG interpreter (QEMU)
- **Vector-compare fix in iOS-TCI.** Upstream swaps the gadgets for the LT/LE and LTU/LEU pairs in `INDEX_op_cmp_vec`, so a guest `cmlt v, #0` is true for zero elements. Rust's SwissTable `HashMap` then silently loses entries and ripgrep crashes. Fixed at source level ([`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch)) and, equivalently, by swapping 4 entries (8 bytes) in the compiled gadget branch table ([`qemu/tcti_fix.py`](qemu/tcti_fix.py)).
- **192 MiB translation-block cache**, so hot code is translated once per session instead of being evicted.
- **2 vCPUs**, matched to the phone's performance cores.

### Machine and boot
- **Whole-machine snapshot.** The host saves the machine over QMP (~115 MB) at the point "guest service listening, workspace not mounted" and resumes it with `loadvm` on every launch.
- **Snapshot validity graph.** A snapshot is used only if kernel, initramfs and tools disk (by content hash), QEMU and the host framework (by linker identity), system disk (size and time) and the state file (checksum) all match. Otherwise: cold boot and re-save; a failed load resets in place.
- **Read-only root at every permission level**, `/tmp` and `/run` in RAM, so a snapshot can never disagree with its disk.
- **Self-healing port forwarding.** iOS reclaims slirp's hostfwd sockets in the background; the host rebuilds them over QMP (`hostfwd_remove/add`) without restarting the guest.

### Kernel and initramfs
- **Custom initramfs**: busybox plus exactly 15 modules (virtio-mmio, blk, net, ext4, 9p, fw_cfg…), loaded in a fixed order. Shipped **uncompressed**, so the emulated kernel never spends time decompressing; the vendor one carried ~20 MB of unused drivers.
- **One-time launch key over fw_cfg**: read once, then the `qemu_fw_cfg` driver is unloaded and the key handed over by file descriptor. Never on the command line, in the environment or on disk.
- **RNG reseed on resume**, so repeated restores never share entropy state.

### Userland
- **Precompiled caches on a read-only tools disk**: V8 code cache for the guest service and npm, `.pyc` for Python's standard library, CA bundle and ripgrep, all built at image time by booting the same kernel.
- **npm and the guest service bundled with esbuild** into single files, with lazy loading for rarely used modules.
- **Sparse home disk** with uninitialised inode tables (`noinit_itable`), so it grows only with what is written.
- **bubblewrap sandbox with every capability dropped**: commands cannot remount root, load modules or read other processes.

### Host integration
- **Native offload**: `rg`, `grep`, `find` and `sha*sum` are recognised and run in the host process on ripgrep's own libraries, skipping 9p entirely.
- **Host-side image decoding**: image attachments are decoded with ImageIO on the host, never inside the guest.
- The guest service accepts connections only from the slirp gateway `10.0.2.2`, and only with the current launch key.

## Architecture

```
┌──────────────────────── Pallas Agent (iOS process) ───────────────────────┐
│  Swift host ── dlopen ──▶ qemu-aarch64-softmmu.framework                   │
│     │                       qemu_init(argv) / qemu_main_loop()            │
│     ├── QMP (JSON over pipe): snapshots, port forwarding, state           │
│     ├── slirp hostfwd: guest services ⇄ loopback ports                    │
│     ├── native offload: rg / grep / find on the host                      │
│     └── fw_cfg: one-time launch key                                       │
│                                                                           │
│   ┌──────────────────── virt machine · 2 × vCPU ──────────────────┐        │
│   │ Linux 6.12 virt ── virtio-mmio: blk ×3 · net · 9p · fw_cfg    │        │
│   │ initramfs (busybox, uncompressed) → read-only ext4 root       │        │
│   │ (Ubuntu 24.04) · bubblewrap (--cap-drop ALL) · tmpfs · /root  │        │
│   └───────────────────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────────────────┘
```

## Component boundary

- QEMU is a separate dynamic framework. The host loads it with `dlopen` and calls only `qemu_init(argc, argv)` and `qemu_main_loop()`.
- The two communicate only through a QMP pipe and network port forwarding. The host calls no QEMU internals, and none of its code is compiled into QEMU.
- Programs in the guest reach the host only over the network and 9p.

Pallas Agent's application code is not in this repository and is not covered by the licenses below.

## Repository contents

| Where | What |
|---|---|
| [Releases](../../releases) | Unmodified mirrors of each component's upstream source archive, with `SHA256SUMS` |
| [`qemu/`](qemu) | Source patch and binary patcher for the TCTI fix; the license notice shipped in the app, `UTM-Licenses.plist` |
| [`qemu/utm-patches/`](qemu/utm-patches) | UTM 5.0.8's build script, dependency list and patches ([v5.0.8 tag](https://github.com/utmapp/UTM/tree/v5.0.8)) |
| [`guest/`](guest) | Names and exact versions of the Ubuntu source packages in the guest |
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

## Author and license

Pallas VM is designed and built by [deemo0666](https://github.com/deemo0666) for [Pallas Agent](https://github.com/deemo0666/pallasai): the TCTI fix, the snapshot-resume path, the native offload and the guest pipeline described above.

If you use any of it, keep the copyright notices and credit **"Pallas VM by deemo0666 (Pallas Agent)"** with a link to this repository. Our QEMU patch is GPL-2.0, our scripts are MIT, and this documentation is CC BY 4.0; see [LICENSE](LICENSE). The Pallas name and logo are not licensed.

## Requesting source

This repository covers Pallas Agent 0.5.1 (17) and later, until a newer release appears here. If a component's source is missing or does not match the binary in the app, please [open an issue](../../issues). We will provide it for at least three years from each release.

## Logo

`assets/logo.png` is the Pallas Agent logo. You may use it to refer or link to Pallas Agent. It is not part of any open-source component above, and their licenses do not apply to it.
