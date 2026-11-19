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

iOS forbids ordinary apps from generating executable code (W^X, no `MAP_JIT`), so conventional dynamic binary translation is off the table. Pallas VM puts QEMU's TCG front end in front of the **iOS-TCI threaded-code back end**: guest instructions are translated to TCG IR, then laid out as chains of addresses of precompiled gadgets that an indirect-branch loop executes. Only data is ever written; no new machine code is produced.

This repository holds all of Pallas VM's low-level changes and the complete corresponding source of its GPL/LGPL components.

## Architecture

```
┌──────────────────────── Pallas Agent (iOS process) ───────────────────────┐
│  Swift host ── dlopen ──▶ qemu-aarch64-softmmu.framework                   │
│     │                       qemu_init(argv) / qemu_main_loop()            │
│     ├── QMP (JSON over pipe): snapshots, port forwarding, state           │
│     ├── slirp hostfwd: guest services ⇄ loopback ports                    │
│     └── fw_cfg: one-time launch key                                       │
│                                                                           │
│   ┌──────────────────── virt machine · 2 × vCPU ──────────────────┐        │
│   │ Linux 6.12 virt ── virtio-mmio: blk ×3 · net · 9p · fw_cfg    │        │
│   │ initramfs (busybox, uncompressed) → read-only ext4 root       │        │
│   │ (Ubuntu 24.04) · bubblewrap (--cap-drop ALL) · tmpfs · /root  │        │
│   └───────────────────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────────────────┘
```

## Low-level work

### 1. The TCTI vector-compare bug (an instruction-level fix)

Upstream `tcg/aarch64-tcti/tcg-target.c.inc` swaps the gadgets for the **LT/LE** and **LTU/LEU** condition pairs in `INDEX_op_cmp_vec`. As a result, a guest `cmlt v, #0` reports true for elements that are exactly zero.

This goes unnoticed until it meets a SIMD hash table. Rust's `HashMap` (SwissTable) matches control-byte groups with vector compares; with the compare wrong, it silently loses entries, and programs such as ripgrep crash at random.

We reproduced it reliably in the guest and traced it from the translated code back to the gadget selection table:
- At source level, [`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch) swaps `cmgt`/`cmge` and `cmhi`/`cmhs` in pairs.
- At binary level, swapping 4 entries in the compiled branch table, **8 bytes** in all ([`qemu/tcti_fix.py`](qemu/tcti_fix.py)), is equivalent to the source change and needs no rebuild of QEMU.

### 2. Whole-machine snapshots: 11 s cold boot → 1.6 s resume

Under pure interpretation, Linux first took 28 s to boot cold. These changes brought it to about 11 s:
- an initramfs holding only busybox and 15 required modules, left uncompressed so the emulated kernel never decompresses it;
- 2 vCPUs;
- a 192 MiB TCG translation cache.

Then, at the moment "guest service is listening, workspace not yet mounted", the host saves the whole machine over QMP as a snapshot of about 115 MB. Every later launch just runs `loadvm`, and commands run after about **1.6 s**.

A snapshot is used only when none of its inputs changed: kernel, initramfs and tools disk by content hash, QEMU and the host framework by linker identity, the system disk by size and time, and the state file by checksum. Any mismatch means a cold boot and a fresh save, and a failed load resets on the spot. To keep snapshots consistent, the root filesystem is mounted read-only at every permission level.

### 3. Launch key and isolation

- Every launch makes a random key. On a cold boot it enters through **fw_cfg**: the initramfs reads it once, unloads the `qemu_fw_cfg` driver right away, and hands the key to the service over a file descriptor. It never appears on the kernel command line, in the environment or in a file.
- On resume, the host trades the old key for a new one through `/resume` and reseeds the kernel RNG, so repeated resumes never share entropy state.
- The guest service accepts connections only from the slirp gateway `10.0.2.2`. Commands run inside bubblewrap with every capability dropped, so they cannot remount root, load modules or read other processes.

### 4. Host offload

Under TCTI, instruction translation is not the main cost (under 5%); the round-trip latency of 9p file access is. So file-walking commands such as `rg`, `grep`, `find` and `sha*sum` are recognised and run natively in the host process on ripgrep's libraries, with output identical to running them in the guest.

### 5. Self-healing networking

iOS reclaims slirp's hostfwd listening sockets in the background; afterwards the guest keeps running but nothing answers on its port. The host rebuilds the forward in place over QMP with `hostfwd_remove/add`, and the guest never restarts.

## Component boundary

- QEMU is a separate dynamic framework. The host loads it with `dlopen` and calls only two entry points: `qemu_init(argc, argv)` and `qemu_main_loop()`.
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

## Requesting source

This repository covers Pallas Agent 0.5.1 (17) and later, until a newer release appears here. If a component's source is missing or does not match the binary in the app, please [open an issue](../../issues). We will provide it for at least three years from each release.

## Logo

`assets/logo.png` is the Pallas Agent logo. You may use it to refer or link to Pallas Agent. It is not part of any open-source component above, and their licenses do not apply to it.
