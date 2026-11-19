<p align="center">
  <img src="assets/logo.png" width="160" alt="Pallas Agent">
</p>

<h1 align="center">Pallas Agent · 开源组件源码</h1>

<p align="center">简体中文 · <a href="README.en.md">English</a></p>

Pallas Agent 是一款 iPhone 上的本地个人助理。App 内置一台小型 Linux，由 QEMU 运行，用来执行命令和处理文件。这台 Linux 和 QEMU 用到的开源软件，有些采用 GPL、LGPL 这类 copyleft 许可证。本仓库提供这些组件的**完整对应源码**、我们做过的**全部修改**，以及构建说明。

Pallas Agent 自己的代码不在本仓库，也不受这些许可证约束。App 与 QEMU 之间只通过命令行参数和 QMP 协议通信（见下文「组件边界」）。

## 内容

| 位置 | 内容 |
|---|---|
| [Releases](../../releases) | 每个组件的上游源码包原样镜像，`SHA256SUMS` 可用来核对 |
| [`qemu/`](qemu) | 我们对 QEMU 的修改：`qemu-tcti-compare.patch` 和等价的二进制修补脚本 `tcti_fix.py`；随 App 分发的许可清单 `UTM-Licenses.plist` |
| [`qemu/utm-patches/`](qemu/utm-patches) | UTM 5.0.8 发布时用的构建脚本、依赖清单和补丁，取自 UTM 的 [v5.0.8 标签](https://github.com/utmapp/UTM/tree/v5.0.8) |
| [`guest/`](guest) | Linux 客户机的内核、busybox 和 Ubuntu 软件包清单 |
| [`scripts/`](scripts) | 从 Launchpad 重新下载全部 Ubuntu 源码包的脚本 |
| [`licenses/`](licenses) | GPL-2.0、LGPL-2.1 原文和 QEMU 的许可说明 |

## 一、QEMU 和它链接的库

App 内置的 QEMU 取自 [UTM SE 5.0.8](https://github.com/utmapp/UTM/releases/tag/v5.0.8) 的发布包：QEMU 10.0.12，使用 iOS-TCI 解释器，不需要 JIT。我们只改了 `qemu-aarch64-softmmu` 里的 8 个字节，修正向量比较指令的条件互换问题，否则 Linux 里的 Rust 程序（比如 ripgrep）会随机崩溃。源码补丁见 [`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch)。

| App 内的框架 | 上游源码 | 许可证 |
|---|---|---|
| qemu-aarch64-softmmu | qemu-10.0.12-utm（[utmapp/qemu@6601422](https://github.com/utmapp/qemu/tree/6601422e1fff2da1376faafb1e4c2c5cdb2d8003)）+ UTM 补丁 + 本仓库补丁 | GPL-2.0 |
| crypto.1.1 / ssl.1.1 | openssl-1.1.1b | OpenSSL / SSLeay |
| gio / glib / gmodule / gobject | glib-2.83.0 | LGPL-2.1+ |
| gstreamer / gstbase | gstreamer-1.19.1 | LGPL-2.1+ |
| gstapp | gst-plugins-base-1.19.1 | LGPL-2.1+ |
| spice-server | spice-0.14.3、spice-protocol-0.14.4 | LGPL-2.1+ |
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
| （静态链接进 QEMU） | utmapp/libucontext@9b1d8f0 | ISC |

### 构建

在 macOS 上安装 Xcode 和 UTM 构建所需的工具（见 UTM 的 [Documentation/Dependencies.md](https://github.com/utmapp/UTM/blob/v5.0.8/Documentation/Dependencies.md)），然后在 Releases 下载 `UTM-v5.0.8.tar.gz` 和各组件源码包：

```sh
tar xzf UTM-v5.0.8.tar.gz && cd UTM-5.0.8
# 把本仓库的修正接在 UTM 的 QEMU 补丁后面，构建脚本会一起应用
cat ../qemu/qemu-tcti-compare.patch >> patches/qemu-10.0.12-utm.patch
# 预先放好源码包，脚本就不会再联网下载
mkdir -p build-iOS-TCI-arm64 && cp ../*.tar.* build-iOS-TCI-arm64/
./scripts/build_dependencies.sh -p ios-tci -a arm64
```

产物在 `sysroot-iOS-TCI-arm64/Frameworks/`。想替换 App 里的 QEMU，把编好的 `qemu-aarch64-softmmu.framework` 放进 App 包的 `Frameworks/` 并重新签名即可。App 只通过 `qemu_init` 和 `qemu_main_loop` 两个入口调用它。

## 二、Linux 客户机

| 部分 | 来源 | 许可证 |
|---|---|---|
| 内核 | Alpine 3.22.8 `linux-virt` 6.12.115-0：`linux-6.12.115.tar.xz` 加 Alpine 的 [`main/linux-lts`](https://gitlab.alpinelinux.org/alpine/aports/-/tree/v3.22.8/main/linux-lts) 配置和补丁 | GPL-2.0 |
| initramfs 里的 busybox | Alpine 3.22.8 busybox 1.37.0-r20：`busybox-1.37.0.tar.bz2` 加 [`main/busybox`](https://gitlab.alpinelinux.org/alpine/aports/-/tree/v3.22.8/main/busybox) | GPL-2.0 |
| initramfs 里的 musl | Alpine 3.22.8 | MIT |
| 系统盘 | Ubuntu Base 24.04.5 arm64，加 bubblewrap、git、curl 及其依赖 | 各包自带，见 `/usr/share/doc/*/copyright` |

系统盘共涉及 78 个 Ubuntu 源码包，名称和确切版本见 [`guest/source-packages.txt`](guest/source-packages.txt)。每个包的 `.dsc`、原始源码和 Debian 补丁都打成一个 `ubuntu-<包名>_<版本>.tar`，放在 Releases 里。也可以运行 `python3 scripts/fetch-ubuntu-sources.py` 直接从 Launchpad 重新下载。

Linux 客户机里另有 Node.js、Python、ripgrep 等组件，许可证都是 MIT、PSF、Apache 这类宽松许可证，许可文本随 App 一起分发在客户机里。

## 组件边界

- QEMU 编成独立的动态框架，App 用 `dlopen` 加载，只调用 `qemu_init(argc, argv)` 和 `qemu_main_loop()`。
- App 与 QEMU 之间只通过 QMP（JSON 命令）管道和网络端口转发通信，App 不调用 QEMU 的内部函数，也不把自己的代码编进 QEMU。
- Linux 里的程序运行在虚拟机中，跟 App 之间只有网络和 9p 文件共享。

## 索取源码

本仓库对应 Pallas Agent 0.5.1 (17) 及之后的版本，直到 Releases 里出现新的版本。哪个组件缺了源码，或者对不上 App 里的二进制，请[提 issue](../../issues)。自发布之日起三年内，我们都会补上。

## Logo

`assets/logo.png` 是 Pallas Agent 的 logo，可以用来介绍或链接 Pallas Agent。它不属于上面任何开源组件，也不适用这些组件的许可证。
