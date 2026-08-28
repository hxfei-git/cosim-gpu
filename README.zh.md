# cosim-gpu

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

Host 必须是 x86_64 Linux（原生 Linux 或 WSL 2），并且 KVM 可用、Docker daemon 可访问。Docker 组权限等价于 root；授权前必须确认这一信任边界。资源、权限、WSL、代理和运行条件由下方 preflight 检查；参数与修复提示以当前 wrapper 的 `--help` 和实际输出为准。

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

| 任务                                | 支持的入口                                                                                                    |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------- |
| 只读 Host/构建/运行检查             | `./scripts/cosim_preflight.sh host\|build\|run`                                                               |
| 锁定的 QEMU、gem5、m5 或 Guest 构建 | `./scripts/cosim_build.sh qemu\|gem5\|m5\|guest\|all`                                                           |
| 构建和 provenance 状态              | `./scripts/cosim_build.sh status`                                                                           |
| 交互式协同仿真                      | `./scripts/cosim_launch.sh`                                                                                 |
| 全新会话的分类测试                  | `./scripts/run_cosim_tests.sh <program>`                                                                    |
| 查看运行范围内的清理清单（dry-run） | `./scripts/cosim_cleanup.sh --run-id <id>`                                                                  |
| 有 ownership gate 的中断恢复        | 先校验并停止精确 launcher process group，再执行 exact-manifest cleanup                                         |

不要用手写 Docker、SCons、Packer 或 QEMU 命令替换这些入口。Wrapper 会强制使用锁定输入、运行范围内的资源名和 manifest，并执行 provenance、证据归档与清理检查。
Cleanup inventory 只读，不授权清理 live run。中断恢复必须校验 `launcher.pid`
ownership，通过 process-group exit gate 后才允许 exact-manifest cleanup。

## 仓库结构

- `gem5/` — 仿真器和 MI300X GPU 模型 submodule。
- `gem5-resources/` — Guest 镜像配方、内核、ROCm 内容和 workload submodule。
- `configs/cosim/` — lockfile 与协同仿真配置。
- `scripts/` — preflight、构建、启动、测试、分类、审计和清理入口。
- `tests/kernels/` — HIP 集成程序；`tests/common/` 提供共享辅助代码。
- `docs/` — 当前学习路线、阶段一：系统架构、统一文档索引和后续正式学习文档。
- `docs/tmp/` — 仅用于原样保留旧资料的备份区，不参与当前文档导航。

## 文档

- [文档索引](docs/文档索引.md) — 当前正式文档的统一入口。
- [学习路线](docs/学习路线.md) — 记录转型目标、阶段任务、当前进度与双仓库动态调整。
- [阶段一：系统架构](docs/阶段一：系统架构.md) — Phase 1 的仓库结构、运行拓扑、数据流、artifact
  速查与 `[REAL AMD]`/`[GEM5]`/`[COSIM]` 边界。

`docs/tmp/` 只保存备份，不在 README、文档索引或文档合同中链接。后续正式学习文档由
用户按阶段生成，加入 `docs/` 后再同步更新索引与合同。

## 本地检查点

2026-08-24 的本地检查点验证了锁定构建以及全新会话中的 Guest driver/ROCm/HIP 链路。生成的 `artifacts/` 按设计被 Git 忽略，不能替代在另一台 Host 上重新得到 `verdict.json`。
