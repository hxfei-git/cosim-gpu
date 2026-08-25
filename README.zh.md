# cosim-gpu

[English](README.md)

`cosim-gpu` 通过 vfio-user，把 QEMU 中运行的 KVM/Q35 Guest 连接到 gem5 的 MI300X GPU 模型。项目支持的流程由仓库脚本统一管理，并以证据为准：初始化锁定的源码、执行 preflight、通过 wrapper 构建，再在全新的协同仿真会话中验证真实 HIP 程序。

```text
Guest Linux 中的 HIP 程序
  -> ROCm / KFD / amdgpu
  -> PCI BAR、MMIO、队列与 GPU 虚拟内存
  -> QEMU vfio-user 传输
  -> gem5 MI300X GPU 模型
  -> 结果与分类后的证据返回 Host
```

默认单 GPU 配置包含 16 GiB VRAM、40 个建模 CU 和 8 GiB Guest 内存。QEMU 不从 Host 的 `PATH` 获取；[`configs/cosim/toolchain.lock`](configs/cosim/toolchain.lock) 锁定仓库本地的 QEMU 10.1.5 工具链。

## 快速开始

Host 必须是 x86_64 Linux（原生 Linux 或 WSL 2），并且 KVM 可用、Docker daemon 可访问。Docker 组权限等价于 root；授权前必须确认这一信任边界。资源要求、组权限刷新、WSL、代理检查和恢复方法见完整的[入门指南](docs/zh/getting-started.md)。

```bash
git submodule update --init --recursive

./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build

./scripts/cosim_build.sh all
./scripts/cosim_build.sh status

./scripts/cosim_preflight.sh run \
    --output-dir artifacts/preflight/run

GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
```

`run_cosim_tests.sh` 会创建新的 QEMU/gem5 会话，在 Guest 中构建精确暂存的测试、执行测试、分类证据，并清理该次运行自己拥有的资源。命令会打印 artifact 目录。完成标准是以下证据一致，而不仅是“编译成功”：

- `verdict.json` 报告 `"outcome": "PASS"` 和验收原因。
- `matrix.tsv` 记录程序、实际中断模式、会话、结果、退出码和 artifact 路径。
- `patch/source-snapshot.txt` 与 `patch/binary-provenance.txt` 标识实际使用的源码树和二进制。
- `cleanup-status.txt` 报告清理验证通过。

单程序 smoke test 通过后再使用 `./scripts/run_cosim_tests.sh --all`；其中每个算子仍使用全新会话。

## 可复现入口

| 任务 | 支持的入口 |
|---|---|
| 只读 Host/构建/运行检查 | `./scripts/cosim_preflight.sh host\|build\|run` |
| 锁定的 QEMU、gem5、m5 或 Guest 构建 | `./scripts/cosim_build.sh qemu\|gem5\|m5\|guest\|all` |
| 构建和 provenance 状态 | `./scripts/cosim_build.sh status` |
| 交互式协同仿真 | `./scripts/cosim_launch.sh` |
| 全新会话的分类测试 | `./scripts/run_cosim_tests.sh <program>` |
| 查看运行范围内的清理清单（dry-run） | `./scripts/cosim_cleanup.sh --run-id <id>` |
| 有 ownership gate 的中断恢复 | [先校验并停止精确 launcher process group，再执行 manifest cleanup](docs/zh/getting-started.md#manifest-scoped-cleanup) |

不要用手写 Docker、SCons、Packer 或 QEMU 命令替换这些入口。Wrapper 会强制使用锁定输入、运行范围内的资源名和 manifest，并执行 provenance、证据归档与清理检查。
Cleanup inventory 只读，不授权清理 live run。中断恢复必须遵循链接中的
`launcher.pid` ownership 与 process-group exit gate，随后才允许 exact-manifest
cleanup。

## 仓库结构

- `gem5/` — 仿真器和 MI300X GPU 模型 submodule。
- `gem5-resources/` — Guest 镜像配方、内核、ROCm 内容和 workload submodule。
- `configs/cosim/` — lockfile 与协同仿真配置。
- `scripts/` — preflight、构建、启动、测试、分类、审计和清理入口。
- `tests/kernels/` — HIP 集成程序；`tests/common/` 提供共享辅助代码。
- `docs/en/` 与 `docs/zh/` — 成对维护的中英文文档。

## 文档与实验

- [入门指南](docs/zh/getting-started.md) — Host 设置、可复现构建、启动、验证、证据和清理。
- [学习实验](docs/zh/labs.md) — 面向源码的 PCI/BAR/MMIO、内存转换、队列、PM4、SDMA、中断与 HIP Dispatch 实验。
- [系统架构](docs/zh/architecture.md) — 传输、内存共享、GPUVM/GART、DMA 和 MSI-X 数据流。
- [参考与调试](docs/zh/reference.md) — 参数、源码地图、已知限制和诊断特征。

| 学习主题 | 中文 | English |
|---|---|---|
| PCI / BAR / MMIO | [中文](docs/zh/labs.md#lab-pci-bar-mmio) | [English](docs/en/labs.md#lab-pci-bar-mmio) |
| amdgpu / KFD 初始化 | [中文](docs/zh/labs.md#lab-amdgpu-kfd-init) | [English](docs/en/labs.md#lab-amdgpu-kfd-init) |
| VRAM / GTT / GART / GPUVM | [中文](docs/zh/labs.md#lab-vram-gtt-gart-gpuvm) | [English](docs/en/labs.md#lab-vram-gtt-gart-gpuvm) |
| Ring / Queue / Doorbell | [中文](docs/zh/labs.md#lab-ring-queue-doorbell) | [English](docs/en/labs.md#lab-ring-queue-doorbell) |
| PM4 | [中文](docs/zh/labs.md#lab-pm4) | [English](docs/en/labs.md#lab-pm4) |
| SDMA | [中文](docs/zh/labs.md#lab-sdma) | [English](docs/en/labs.md#lab-sdma) |
| Fence / IH / MSI-X | [中文](docs/zh/labs.md#lab-fence-ih-msix) | [English](docs/en/labs.md#lab-fence-ih-msix) |
| HIP → KFD/amdgpu → GPU Dispatch | [中文](docs/zh/labs.md#lab-hip-dispatch) | [English](docs/en/labs.md#lab-hip-dispatch) |
| gem5 GPU 模型与调试 | [中文](docs/zh/labs.md#lab-gem5-debug) | [English](docs/en/labs.md#lab-gem5-debug) |

## 本地检查点

2026-08-24 的本地检查点验证了锁定构建以及全新会话中的 Guest driver/ROCm/HIP 链路。生成的 `artifacts/` 按设计被 Git 忽略，不能替代在另一台 Host 上重新得到 `verdict.json`。

## 许可证

见 [LICENSE](LICENSE)。
