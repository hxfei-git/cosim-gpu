---
name: cosim-gpu-build
description: 构建或检查 cosim-gpu 的锁定 QEMU、gem5、m5 与 Guest 产物时使用；不适用于普通源码编辑或仅运行既有产物。
---

# cosim-gpu 构建

协同仿真组件统一由 `scripts/cosim_build.sh` 构建。不要绕过它直接调用
Docker、SCons、Packer、QEMU Makefile 或 Guest 镜像配方。

## 入口

```bash
./scripts/cosim_preflight.sh build
./scripts/cosim_build.sh status
./scripts/cosim_build.sh qemu
./scripts/cosim_build.sh gem5
./scripts/cosim_build.sh m5
./scripts/cosim_build.sh guest
./scripts/cosim_build.sh all
```

`all` 构建 Guest 及其 QEMU、gem5、m5 前置项；`--force` 重新执行正常增量
构建，但不会删除构建树。只有更新空的 QEMU source lock 时才使用
`lock-qemu-source`；该动作只打印经过签名校验的 digest，不改写 lock。

`scripts/run_mi300x_fs.sh` 仍是 standalone full-system 兼容入口：
`build-gem5` 和 `build-disk` 会委托给 `cosim_build.sh`，而
`build-app` 构建 gem5-resources 示例程序。QEMU+gem5 协同仿真组件仍以
`cosim_build.sh` 的产物和 metadata 为准。

## 依赖与资源

`cosim_preflight.sh build` 会检查 Linux、可访问的 amd64 Docker daemon、KVM、
源码锁和 QEMU 构建库。当前 host gate 要求至少 12 GiB 总内存和工作区至少
80 GiB 可用空间；Guest 构建还必须能读写 `/dev/kvm`。

gem5 通过 `scripts/Dockerfile.run` 构建。该文件基于锁定 digest 的
`ghcr.io/gem5/gpu-fs`，补充 Python 3.12 与 json-c，并生成
`gem5-build:local`、`gem5-run:local` 两个 tag。

gem5 因 OOM 失败时降低并行度后重试同一 wrapper：

```bash
GEM5_BUILD_JOBS=2 ./scripts/cosim_build.sh gem5
```

仍不足时改为 `GEM5_BUILD_JOBS=1`。QEMU 和 m5 分别使用
`QEMU_BUILD_JOBS`、`M5_BUILD_JOBS`。

## 产物位置

| 组件 | 规范产物 |
| --- | --- |
| QEMU | `.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64` 与同目录 `qemu-img` |
| QEMU metadata/log | `.local/cosim/build/qemu-10.1.5/.cosim-build-meta` 及该目录构建日志 |
| gem5 | `gem5/build/VEGA_X86/gem5.opt` 与同目录 `.cosim-build-meta` |
| gem5 构建日志 | `.local/cosim/gem5-docker-build.log`、`.local/cosim/gem5-build.log` |
| m5 | `gem5/util/m5/build/x86/out/m5`，并复制到 `gem5-resources/src/x86-ubuntu-gpu-ml/files/m5` |
| Guest | `gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70` 与 `vmlinux-rocm70` |
| Guest metadata | `.local/cosim/build/guest/.cosim-build-meta` 与 `.cosim-content-seal` |
| Guest 构建日志 | `artifacts/amd-gpu-learning-env/build/guest/<attempt-id>/` |

QEMU 10.1.5 的源码由 lock 固定并下载到 `.local/cosim/src/`，在仓库本地前缀
构建；它不是本仓库的 submodule，也不是规范流程中的系统 QEMU 依赖。非 strict
launcher 虽可回退到 `PATH`，正式产物检查以仓库本地 QEMU 为准。
