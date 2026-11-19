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

Pallas VM 是 [Pallas Agent](https://github.com/deemo0666/pallasai) 的执行层：智能体在 iPhone 上要跑的 shell、Python、npm、git，都在这台 App 进程内的虚拟机里完成。不越狱、不借助外部服务器，也不需要 JIT 授权。

iOS 不允许普通 App 生成可执行代码（W^X、没有 `MAP_JIT`），常规动态二进制翻译走不通。Pallas VM 在 QEMU 的 TCG 前端之后接 **iOS-TCI 线程代码后端**：客机指令先翻译成 TCG IR，再编排成预编译 gadget 的地址链，由一个间接跳转循环执行。全程只读写数据，不产生新的机器码。

本仓库收录 Pallas VM 的全部底层修改，以及它所含 GPL/LGPL 组件的完整对应源码。

## 架构

```
┌──────────────────────── Pallas Agent（iOS 进程） ────────────────────────┐
│  Swift 宿主 ── dlopen ──▶ qemu-aarch64-softmmu.framework                   │
│     │                       qemu_init(argv) / qemu_main_loop()            │
│     ├── QMP（JSON over pipe）：快照、端口转发、状态                          │
│     ├── slirp hostfwd：guest 服务 ⇄ 回环端口                               │
│     └── fw_cfg：一次性启动密钥                                             │
│                                                                           │
│   ┌──────────────── virt 机器 · 2 × vCPU─────────────────┐        │
│   │ Linux 6.12 virt ── virtio-mmio: blk ×3 · net · 9p · fw_cfg    │        │
│   │ initramfs（busybox，未压缩）→ 只读 ext4 根（Ubuntu 24.04）       │        │
│   │ bubblewrap 沙箱（--cap-drop ALL）· tmpfs /tmp · 独立 /root 盘   │        │
│   └───────────────────────────────────────────────────────────────┘        │
└───────────────────────────────────────────────────────────────────────────┘
```

## 底层工作

### 1. TCTI 向量比较 bug（指令级修复）

上游 `tcg/aarch64-tcti/tcg-target.c.inc` 在 `INDEX_op_cmp_vec` 里把 **LT/LE**、**LTU/LEU** 两对条件对应的 gadget 写反了。后果是客机执行 `cmlt v, #0` 时，值恰好为 0 的元素也被判成真。

这种错误平时看不出来，直到碰上 SIMD 哈希表：Rust `HashMap`（SwissTable）用向量比较匹配控制字节组，比较一出错就会悄悄丢元素，ripgrep 这类程序随机崩溃。

我们在客机里稳定复现后，从翻译结果回溯到 gadget 选择表，定位了这处错误：
- 源码层面：[`qemu/qemu-tcti-compare.patch`](qemu/qemu-tcti-compare.patch)，把 `cmgt`/`cmge`、`cmhi`/`cmhs` 两两互换；
- 二进制层面：在已编译的分支表里交换 4 个表项，共 **8 字节**（[`qemu/tcti_fix.py`](qemu/tcti_fix.py)），与改源码等价，不必重编整个 QEMU。

### 2. 整机快照：冷启动 11 s → 恢复 1.6 s

纯解释执行下，Linux 冷启动最初要 28 s。经过下面这些优化压到约 11 s：
- initramfs 只留 busybox 和 15 个必需模块，并且不压缩，免得在模拟器里解压；
- vCPU 增加到 2 个；
- TCG 翻译缓存调到 192 MiB。

之后，在「guest 服务已监听、工作区尚未挂载」这一刻，经 QMP 把整机状态存成约 115 MB 的快照，以后每次直接 `loadvm`，约 **1.6 s** 就能执行命令。

快照只在来源全部未变时才用：内核、initramfs、工具盘按内容哈希，QEMU 和宿主框架按链接标识，系统盘按尺寸和时间，状态文件按校验和。任何一项不符就冷启动并重存；载入失败当场复位。为保证快照一致，根文件系统在所有权限档下都只读挂载。

### 3. 启动密钥与隔离

- 每次启动生成随机密钥。冷启动时经 **fw_cfg** 传入，initramfs 读取一次后立即卸载 `qemu_fw_cfg` 驱动，再通过文件描述符交给服务；密钥不出现在内核命令行、环境变量或任何文件里。
- 从快照恢复时，用旧密钥通过 `/resume` 换新密钥，同时重置内核随机数种子，避免多次恢复得到相同的熵状态。
- guest 服务只接受来自 slirp 网关 `10.0.2.2` 的连接。命令运行在 bubblewrap 里并丢弃全部 capability，不能重挂根、加载模块或读别的进程。

### 4. 宿主卸载

TCTI 下的开销主要不在指令翻译（不到 5%），而在 9p 文件访问的往返延迟。所以 `rg`、`grep`、`find`、`sha*sum` 这些遍历文件的命令会被识别出来，改在宿主进程里用 ripgrep 的库原生执行，输出跟客机里运行完全一致。

### 5. 网络自愈

iOS 会在后台回收 slirp 的 hostfwd 监听套接字，回收后客机仍在运行，但端口没人应答。宿主经 QMP 的 `hostfwd_remove/add` 原地重建转发，客机不用重启。

## 组件边界

- QEMU 编成独立的动态框架，宿主用 `dlopen` 加载，只调用 `qemu_init(argc, argv)` 和 `qemu_main_loop()` 两个入口。
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

## 索取源码

本仓库对应 Pallas Agent 0.5.1 (17) 及之后的版本，直到 Releases 里出现新的版本。哪个组件缺了源码，或者对不上 App 里的二进制，请[提 issue](../../issues)。自发布之日起三年内，我们都会补上。

## Logo

`assets/logo.png` 是 Pallas Agent 的 logo，可以用来介绍或链接 Pallas Agent。它不属于上面任何开源组件，也不适用这些组件的许可证。
