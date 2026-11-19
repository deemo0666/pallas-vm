<p align="center">
  <img src="assets/logo.png" width="160" alt="Pallas VM">
</p>

<h1 align="center">Pallas VM</h1>

<p align="center"><b>在 iOS 应用沙盒里，无 JIT 运行完整 aarch64 Linux 的虚拟机运行时</b></p>

<p align="center"><a href="README.md">English</a> · 简体中文</p>

<p align="center">
  <img alt="QEMU" src="https://img.shields.io/badge/QEMU-10.0.12-orange">
  <img alt="TCG" src="https://img.shields.io/badge/TCG-iOS--TCI-blue">
  <img alt="Guest" src="https://img.shields.io/badge/guest-Linux%206.12%20aarch64-black">
  <img alt="Resume" src="https://img.shields.io/badge/resume-~1.6%20s-green">
</p>

Pallas VM 是 [Pallas Agent](https://github.com/deemo0666/pallasai) 的执行层：智能体在 iPhone 上跑的 shell、Python、npm、git，都在这台 App 进程内的虚拟机里完成。不越狱、不借助外部服务器，也不需要 JIT 授权。

iOS 禁止普通 App 生成可执行代码（W^X、没有 `MAP_JIT`）。Pallas VM 在 QEMU 的 TCG 前端之后接 **iOS-TCI 线程代码后端**：客机指令先翻成 TCG IR，再编排成预编译 gadget 的地址链，由间接跳转循环执行，全程只写数据。

## 性能

均在 iPhone 实测，纯解释执行，无 JIT。

| | 原始配置 | Pallas VM |
|---|---|---|
| 冷启动到能执行命令 | 28 s | **11 s** |
| 从保存的整机状态启动 | — | **约 1.6 s** |
| 在工作区里跑 `rg` / `grep` / `find` | 客机内，经 9p | **宿主原生执行，输出一致** |
| 用 SIMD 哈希表的 Rust 程序 | 随机崩溃 | **正确**（TCTI 修复） |

性能剖析显示指令翻译只占不到 5%，大头是 9p 往返和启动开销。下面每一项改动都针对其中之一。

## 与 iSH 的对比

[iSH](https://github.com/ish-app/ish) 是在 iOS 上无 JIT 跑 Linux 的先驱，它的线程代码（gadget）解释器跟我们用的是同一类技术。两者在架构上做了相反的选择：

| | iSH | Pallas VM |
|---|---|---|
| 模拟方式 | 用户态：直接跑 Linux 程序，把系统调用翻译给 XNU | 整机：在模拟的 `virt` 机器上跑真正的 Linux 内核 |
| 客机指令集 | 32 位 x86（i386） | **64 位 aarch64**，与 iPhone 同架构 |
| 内核 | 自己重写的系统调用层 | **原版 Linux 6.12** |
| 用户态 | Alpine i386（musl） | **Ubuntu 24.04 arm64（glibc）**：Node.js、Python、Rust 工具的官方 arm64 构建直接可用 |
| 64 位程序 | 不支持 | 支持 |
| 内核特性（namespace、cgroup、ext4、9p、bubblewrap 沙箱） | 只有系统调用层重新实现的部分 | Linux 有的都有 |
| 启动 | 没有内核要启动 | 整机快照，约 1.6 s |
| 集成方式 | 独立的终端 App | 嵌入式运行时，由智能体经 QMP 和回环服务驱动 |

用户态模拟更轻，没有启动过程；整机模拟换来的是兼容性：真内核就有真语义，arm64 客机让现代工具链生态不用依赖 32 位 x86 移植就能直接用。

## 逐层改动

### TCG 解释器（QEMU）
- **修复 iOS-TCI 的向量比较。** 上游在 `INDEX_op_cmp_vec` 里把 LT/LE、LTU/LEU 两对条件的 gadget 写反，客机的 `cmlt v, #0` 对零元素给出真，Rust 的 SwissTable `HashMap` 因此悄悄丢元素，ripgrep 随机崩溃。修复有源码补丁（[`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch)），也有等价的二进制修补：在已编译的 gadget 分支表里交换 4 个表项、共 8 字节（[`qemu/tcti_fix.py`](qemu/tcti_fix.py)）。
- **192 MiB 翻译块缓存**，热代码一次会话只翻译一次，不会被挤出。
- **2 个 vCPU**，对应手机的性能核。

### 机器与启动
- **整机快照。** 在「guest 服务已监听、工作区未挂载」这一刻经 QMP 保存整机（约 115 MB），之后每次启动用 `loadvm` 恢复。
- **快照有效性校验。** 内核、initramfs、工具盘按内容哈希，QEMU 和宿主框架按链接标识，系统盘按尺寸和时间，状态文件按校验和，全部一致才用快照；否则冷启动并重存，载入失败当场复位。
- **所有权限档下根文件系统都只读**，`/tmp`、`/run` 在内存里，快照和磁盘永远一致。
- **端口转发自愈。** iOS 在后台回收 slirp 的 hostfwd 套接字后，宿主经 QMP（`hostfwd_remove/add`）原地重建，客机不重启。

### 内核与 initramfs
- **定制 initramfs**：只有 busybox 和固定顺序加载的 15 个模块（virtio-mmio、blk、net、ext4、9p、fw_cfg 等），并且**不压缩**，被模拟的内核不用花时间解压；原厂 initramfs 带了约 20 MB 用不上的驱动。
- **经 fw_cfg 传一次性启动密钥**：读一次就卸载 `qemu_fw_cfg` 驱动，再通过文件描述符交出，不出现在命令行、环境变量或磁盘上。
- **恢复时重置内核随机数**，多次恢复不会共享熵状态。

### 用户态
- **只读工具盘上的预编译缓存**：guest 服务和 npm 的 V8 代码缓存、Python 标准库的 `.pyc`、CA 证书、ripgrep，都在制作镜像时用同一个内核启动生成。
- **npm 和 guest 服务用 esbuild 打成单文件**，少用的模块延迟加载。
- **稀疏 home 盘**，inode 表不预先初始化（`noinit_itable`），写多少长多少。
- **bubblewrap 沙箱丢弃全部 capability**：命令不能重挂根、加载模块或读其他进程。

### 宿主集成
- **原生卸载**：`rg`、`grep`、`find`、`sha*sum` 被识别后在宿主进程里用 ripgrep 自己的库执行，完全绕开 9p。
- **图片在宿主解码**：附件图片用 ImageIO 在宿主处理，不进客机。
- guest 服务只接受来自 slirp 网关 `10.0.2.2`、带当前启动密钥的连接。

## 架构

```
┌──────────────────────── Pallas Agent（iOS 进程） ────────────────────────┐
│  Swift 宿主 ── dlopen ──▶ qemu-aarch64-softmmu.framework                   │
│     │                       qemu_init(argv) / qemu_main_loop()            │
│     ├── QMP（JSON over pipe）：快照、端口转发、状态                          │
│     ├── slirp hostfwd：guest 服务 ⇄ 回环端口                               │
│     ├── 原生卸载：rg / grep / find 在宿主执行                              │
│     └── fw_cfg：一次性启动密钥                                             │
│                                                                           │
│   ┌──────────────────── virt 机器 · 2 × vCPU ─────────────────────┐        │
│   │ Linux 6.12 virt ── virtio-mmio: blk ×3 · net · 9p · fw_cfg    │        │
│   │ initramfs（busybox，未压缩）→ 只读 ext4 根（Ubuntu 24.04）       │        │
│   │ bubblewrap 沙箱（--cap-drop ALL）· tmpfs /tmp · 独立 /root 盘   │        │
│   └───────────────────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────────────────┘
```

## 组件边界

- QEMU 编成独立的动态框架，宿主用 `dlopen` 加载，只调用 `qemu_init(argc, argv)` 和 `qemu_main_loop()`。
- 两者之间只通过 QMP 管道和网络端口转发通信。宿主不调用 QEMU 的内部函数，也不把自己的代码编进 QEMU。
- 客机里的程序只经网络和 9p 与宿主交互。

Pallas Agent 的应用代码不在本仓库，也不受下列许可证约束。

## 仓库内容

| 位置 | 内容 |
|---|---|
| [Releases](../../releases) | 每个组件的上游源码包原样镜像，`SHA256SUMS` 可用来核对 |
| [`qemu/`](qemu) | TCTI 修复的源码补丁与二进制修补脚本；随 App 分发的许可清单 `UTM-Licenses.plist` |
| [`qemu/utm-patches/`](qemu/utm-patches) | UTM 5.0.8 的构建脚本、依赖清单和补丁（[v5.0.8 标签](https://github.com/utmapp/UTM/tree/v5.0.8)） |
| [`guest/`](guest) | 客机所用 Ubuntu 源码包的名称与确切版本 |
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

## 作者与许可

Pallas VM 由 [maao514](https://github.com/deemo0666) 为 [Pallas Agent](https://github.com/deemo0666/pallasai) 设计和实现，包括上文的 TCTI 修复、快照恢复、原生分流和客户机构建流程。

使用其中任何部分，请保留版权声明，并注明 **「Pallas VM by maao514 (Pallas Agent)」** 和本仓库链接。我们的 QEMU 补丁是 GPL-2.0，脚本是 MIT，文档是 CC BY 4.0，详见 [LICENSE](LICENSE)。Pallas 名称和 logo 不在授权范围内。

## 索取源码

本仓库对应 Pallas Agent 0.5.1 (17) 及之后的版本，直到 Releases 里出现新的版本。哪个组件缺了源码，或者对不上 App 里的二进制，请[提 issue](../../issues)。自发布之日起三年内，我们都会补上。

## Logo

`assets/logo.png` 是 Pallas Agent 的 logo，可以用来介绍或链接 Pallas Agent。它不属于上面任何开源组件，也不适用这些组件的许可证。
