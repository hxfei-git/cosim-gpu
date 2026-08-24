# 快速入门

[English](../en/getting-started.md) | [项目 README](../../README.zh.md) | [学习实验](labs.md)

本文给出从全新 checkout 到 HIP 程序通过 Guest Linux、amdgpu/KFD/ROCm、QEMU vfio-user 和 gem5 MI300X 模型完成分类验证的公开可复现路径。请只使用这里列出的仓库 wrapper；它们统一管理源码锁、构建 provenance、运行范围内的资源、证据契约和清理规则，而临时命令不具备这些保证。

## 支持的工作流

```text
submodule
  -> Host/构建 preflight
  -> 锁定的 QEMU + gem5 + m5 + Guest 构建
  -> 运行 preflight
  -> 每个 HIP 程序使用一个全新的 QEMU/gem5 会话
  -> verdict + matrix + provenance + 已验证清理
```

“编译成功”只是中间结果。验收要求完整运行链路通过，并且分类器生成 `PASS`，provenance 与清理证据也必须一致。

不要用直接的 Docker、SCons、Packer 或 QEMU 命令构建或启动本系统，也不要自行发明固定的 container、socket 或共享内存路径。Wrapper 会生成 run ID 和资源 manifest，使并发或中断的运行仍可准确归属。

## Host 与 WSL 要求

### 平台与资源

`cosim_preflight.sh` 会强制检查以下 Host 基线：

| 要求 | 必须满足的状态 |
|---|---|
| 操作系统与架构 | x86_64 Linux |
| CPU | 至少 2 个在线 Host CPU |
| 内存 | Host 总内存至少 12 GiB |
| 工作区 | 仓库所在文件系统至少有 80 GiB 可用空间 |
| 虚拟化 | `/dev/kvm` 存在，并且当前进程可读写 |
| 运行时存储 | `/dev/shm` 与 `/tmp` 存在且可写 |
| 容器 | Docker daemon 可访问，并报告 amd64/x86_64 |
| 网络 | 构建 profile 能访问 GitHub、QEMU 下载站点和 GHCR |

原生 Linux 是最简单的 Host。WSL 必须是 WSL 2，并且需要向 Linux 暴露可用的 `/dev/kvm`。在 BIOS/UEFI 中启用 CPU 虚拟化、启用嵌套虚拟化、重启 Windows 和重启 WSL 都属于 Host 所有者操作，仓库无法执行。本项目不要求也不会自动修改 `.wslconfig`。

默认启动使用 8 GiB Guest 内存、4 个 Guest CPU，以及一个包含 16 GiB 建模 VRAM 和 40 个建模 CU 的 GPU。这些是 Guest/模型参数，不能替代上面的 Host 最低要求。

### 依赖与访问权限 wrapper

先执行审计。审计为只读操作，会报告已安装软件包、WSL/原生 Linux、资源、Docker、KVM 和账户组状态：

```bash
./scripts/cosim_host_setup.sh audit --for-user "$USER"
```

在使用 systemd 的 Debian/Ubuntu Host 上，安装前先让 wrapper 打印确切的提权计划：

```bash
./scripts/cosim_host_setup.sh plan --for-user "$USER"
```

安装动作必须以 root 身份运行。它会安装仓库固定的软件包集合并启用 Docker，但默认不修改组成员关系：

```bash
sudo ./scripts/cosim_host_setup.sh install --for-user "$USER"
```

只有 Host 所有者明确接受两项组权限时，才在 `plan` 和 `install` 动作中增加 `--grant-runtime-groups`。`kvm` 组授予硬件虚拟化访问权限；`docker` 组是更强的信任边界：其成员可以要求 daemon 挂载或修改 Host 资源，因此该权限实际上等价于 root。

```bash
./scripts/cosim_host_setup.sh plan --for-user "$USER" \
    --grant-runtime-groups
sudo ./scripts/cosim_host_setup.sh install --for-user "$USER" \
    --grant-runtime-groups
```

Setup wrapper 从不读取凭据，不修改 sudoers、WSL/Windows 配置，也不保存代理值。任何 sudo 认证都由 Host 的正常提权机制在脚本外处理。

如果 Docker 由 Docker Desktop 等外部 Host 集成提供，不要直接运行基于 systemd 的安装动作。应使用 `audit`，有意识地配置相应 provider，并且仅在 preflight 可以访问其 amd64 daemon 后继续。

### 刷新组权限

把账户加入 `docker` 或 `kvm` 不会更新已经存在的 shell、终端、IDE 或正在运行的 Codex 进程。

- 原生 Linux：完整退出登录会话，再重新登录。
- WSL：关闭 Linux shell，在 Windows PowerShell 中运行 `wsl --shutdown`，然后重新启动发行版。这是必须由用户完成的 Windows 边界。
- 不要只根据账户组列表推断当前访问权限；后续 preflight 会检查实际启动协同仿真的当前进程权限。

打开全新会话后执行：

```bash
./scripts/cosim_host_setup.sh verify --for-user "$USER"
```

然后执行下面的 `host` preflight。两项检查互为补充：setup verify 检查软件包与账户配置；preflight 检查当前进程的 KVM/Docker 权限和资源限制。

### 网络与代理

Host preflight 只记录常见大小写代理环境变量是否设置，并隐藏其值与凭据。Build preflight 还会探测 GitHub、`download.qemu.org` 和 GHCR。如果必须使用代理，请通过正常 Host 策略配置 shell 与 Docker daemon，然后重新运行 preflight。不要把代理凭据写入仓库文件、示例命令历史或准备共享的 artifact。

## 初始化锁定源码

在仓库根目录执行：

```bash
git submodule update --init --recursive
git submodule status --recursive
```

`gem5/` 与 `gem5-resources/` 都必须匹配顶层 commit 记录的 gitlink。不要为了修复本地构建而随意更新 submodule；这会改变实验身份，必须作为源码变更单独审核。

## 运行 preflight

使用仓库相对的 artifact 目录。Preflight 会拒绝 `artifacts/` 之外的输出路径，并按需写入人类可读证据和 JSON。

```bash
./scripts/cosim_preflight.sh host \
    --output-dir artifacts/preflight/host

./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build
```

| Profile | 检查内容 |
|---|---|
| `host` | Linux/x86_64、CPU、内存、磁盘、KVM、Docker、临时存储、代理状态和下载端点 |
| `build` | 全部 Host 检查，以及锁定的 submodule、编译器/工具和 QEMU 开发库 |
| `run` | 运行时 Host 检查，以及锁定的 QEMU provenance/功能、gem5、m5/Guest 资源、磁盘镜像和陈旧资源安全性 |

需要机器可读的 preflight 证据时增加 `--json`。任何必需项为 `FAIL` 或 `UNKNOWN` 时返回码为 1；参数错误返回 2。必需 preflight 失败后不要继续构建；应根据其检查 ID 和修复提示处理 Host，再重新运行同一 profile。

## 可复现构建

[`configs/cosim/toolchain.lock`](../../configs/cosim/toolchain.lock) 锁定 QEMU 10.1.5、官方源码身份和签名密钥。Build wrapper 将其安装到 `.local/cosim/qemu/10.1.5/`；启动时优先使用这个仓库本地二进制，而不是 Host `PATH` 中的副本。Guest 输入由 [`configs/cosim/guest.lock`](../../configs/cosim/guest.lock) 独立锁定。

### 构建动作

| 动作 | 行为 |
|---|---|
| `status` | 只读报告 QEMU、gem5、m5 和 Guest 的路径、metadata、hash 与就绪状态 |
| `qemu` | 验证 lock，并增量构建仓库本地 QEMU 10.1.5 |
| `gem5` | 构建仓库 Docker image 和带 provenance 的 `VEGA_X86/gem5.opt` |
| `m5` | 确保 gem5 已构建，构建 x86 m5 工具并暂存到 Guest 文件中 |
| `guest` | 确保 QEMU 与 m5 已就绪，再构建并验证锁定的 Guest 镜像与内核 |
| `all` | 执行直至 `guest` 的完整依赖链 |

标准完整构建为：

```bash
./scripts/cosim_build.sh status
./scripts/cosim_build.sh all
./scripts/cosim_build.sh status
```

聚焦某一构建时，将 `all` 替换为 `qemu`、`gem5`、`m5` 或 `guest`。`--force` 会重新执行所选的正常增量路径，但不会删除 build tree，例如：

```bash
./scripts/cosim_build.sh gem5 --force
```

QEMU、gem5 和 m5 默认各使用 4 个构建 job。只通过 wrapper 调用时的 `QEMU_BUILD_JOBS`、`GEM5_BUILD_JOBS` 与 `M5_BUILD_JOBS` 调整；Host 内存紧张时首先减少 job 数。

日常环境搭建不要调用 `lock-qemu-source`：tracked lock 已包含接受的源码 SHA-256。Hash 不匹配属于 provenance 失败，不能据此在本地替换 lock。

构建完成后必须执行运行 preflight：

```bash
./scripts/cosim_preflight.sh run \
    --output-dir artifacts/preflight/run
```

只有必需检查全部通过，并且 `status` 能标识锁定的本地 QEMU、标准 gem5 二进制、暂存的 m5、Guest 镜像和 Guest 内核时，才能继续。

## 启动并检查 Guest

使用以下入口启动交互式架构/调试会话：

```bash
./scripts/cosim_launch.sh
```

Launcher 会分配唯一 run ID，创建运行范围内的 socket/container/共享内存名称，记录资源 manifest，使用 runtime image 启动 gem5，等待模型就绪，再以前台方式启动 QEMU/KVM。默认 artifact 目录是 `artifacts/standalone/<generated-run-id>`。

Guest 自动登录控制台最终应显示 `cosim-gpu-setup.service` 链路完成。Launcher 打印的观察命令包含 `rocm-smi` 和 `rocminfo`；预期身份是带 `gfx942` agent 的建模 AMD 设备。对于正式验收链路，还应看到 PCI 枚举、设备绑定 amdgpu、`/dev/kfd` 与 DRM 节点，并且 HIP 返回结果前没有致命的 GPUVM/PM4/SDMA/IH 错误。

按 `Ctrl-A X` 退出 QEMU，并让 launcher trap 归档日志和验证清理。交互启动适合观察，但不是分类测试，不能替代全新会话 runner。

### Driver 部分初始化

如果 PCI 枚举成功，但 amdgpu 初始化、KFD 或 ROCm 只出现一部分，应把该 Guest 视为已经污染的证据：

1. 保留打印的 run ID 与 artifact 目录。
2. 通过 launcher 退出，使其执行 manifest 范围内的清理。
3. 检查归档的 QEMU console、gem5 log、launcher category 和 cleanup status。
4. 每次重试都启动全新会话。

`hw_init` 失败后不要反复卸载/加载 amdgpu，也不要把手工修复后的 Guest 当成通过的 baseline。Driver 初始化会改变内核与设备状态，原地重试可能掩盖最初故障。选择调试实验前先阅读[已知问题与陷阱](reference.md#4-已知问题与陷阱)。

## 运行全新会话 HIP 测试

Test runner 接受 `tests/kernels/<stem>.cpp` 中精确的 stem，暂存该测试树，在 Guest 中编译，并要求恰好一个匹配的 `[PASS]` 标记且没有 `[FAIL]` 标记；随后它会分类原始证据并验证清理。

从仓库提供的 `vector_add` 程序开始：

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
```

空的 `GUEST_TEST_PREFIX` 同样表示 `HSA_ENABLE_INTERRUPT=0`。比较 runtime 行为时只能使用以下两个显式值之一：

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add

GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh vector_add
```

必须把两种模式记录为独立实验。模式 1 使用中断支持的 HSA signal，不能与模式 0 baseline 互换。`matrix.tsv` 必须包含从 Guest 观察到的实际值；`unknown` 或与预期不符都会使该行无效。

当前 runner timeout 为：等待 Guest 登录提示 240 秒、Guest 内程序执行 60 秒、Guest 编译加执行的 Host deadline 1,800 秒。只能通过 `run_cosim_tests.sh` 选项覆盖，并应把实际命令与证据一起记录。

Smoke test 通过后执行：

```bash
./scripts/run_cosim_tests.sh --repeat 3 vector_add
./scripts/run_cosim_tests.sh --all
```

`--repeat` 的每次迭代都会创建全新会话。`--all` 会发现排序后的 `tests/kernels/*.cpp` 集合，并为每个程序创建全新的子会话与 artifact 目录；当前集合为 `gemm`、`histogram`、`multi_gpu_verify`、`prefix_scan`、`reduction`、`transpose` 和 `vector_add`。后续源码变更可能改变该集合，因此目录和归档的 source snapshot 才是权威依据。

正式验收行不要使用 `--keep-alive`。它会有意保留 live session；关闭并清理该会话之前，无法满足“已验证清理”的验收条件。

## 证据与验收

Runner 会打印确切的 artifact 目录。自定义 `--output-dir` 只能位于仓库 `artifacts/` 下且必须为空；每一行都使用新目录。

| 证据 | 含义 |
|---|---|
| `verdict.json` | 权威分类结果、主要原因、全部原因、身份检查与证据完整性 |
| `matrix.tsv` | 程序、实际 HSA 中断值、run/session、结果、退出码、原因和 artifact 路径 |
| `runner-metadata.txt` | 精确程序/源码/二进制身份、预期环境、编译/测试退出码、标记与清理状态 |
| `patch/source-snapshot.txt` | 顶层 commit，以及暂存源码、runner、仓库 diff、untracked 文件清单/归档的 hash |
| `patch/binary-provenance.txt` | gem5 commit/二进制 hash 和精确测试二进制 hash |
| `patch/repo-status.txt`、`patch/repo.patch` | 该行使用的顶层 tracked 与未提交源码状态 |
| `patch/gem5-status.txt`、`patch/gem5.patch` | 该行使用的 gem5 submodule 状态 |
| `qemu.log`、`gem5.log` | 为诊断保留的完整 Guest console 与仿真器证据 |
| `cleanup-status.txt` | Manifest 范围内的资源清理结果 |

一行只有在以下条件全部满足时才可接受：runner 返回 0；`verdict.json` 为 `PASS` 且原因为 `all_acceptance_gates_passed`；`matrix.tsv` 一致；实际 HSA 值匹配目标模式；精确源码/二进制 provenance 存在；清理已验证。即使 HIP 输出看起来正确，缺少证据也属于失败。

`artifacts/` 下生成的构建/测试证据和 `.local/cosim/` 下的本地工具链均被 Git 忽略。评审需要持久证据时，应在 Git 之外保存或归档；不要把生成的镜像、二进制或日志加入源码 commit。

## Manifest 范围内的清理

Runner 与 launcher 正常退出时会清理本次运行自己的资源。如果进程被中断，使用 wrapper 打印的精确 run ID。先预览其拥有的资源：

```bash
RUN_ID=replace-with-the-printed-run-id
./scripts/cosim_cleanup.sh --run-id "$RUN_ID"
```

第一条清理命令只是 dry run。确认 manifest、run ID、路径和 container label 都正确后，再确认相同范围：

```bash
./scripts/cosim_cleanup.sh --run-id "$RUN_ID" --confirm
```

Cleanup wrapper 只接受经过验证的 `/tmp/cosim-<run-id>.session/resources.manifest` 所有权模型，并会验证删除结果。绝不能用宽泛的进程 kill、裸 container 删除、通配 socket 删除或递归删除来替代。如果不存在唯一且有效的 manifest，应停止并诊断所有权，不能猜测目标。

## 学习实验

模式 0 的 `vector_add` baseline 通过后，继续使用成对维护的 [AMD GPU Driver / Architecture 学习实验](labs.md)。Labs 基于真实仓库代码和分类运行，覆盖：

- PCI 配置、BAR 与 MMIO 传输。
- amdgpu 发现与初始化。
- VRAM、GTT、GART、GPUVM 与地址转换。
- Ring、Queue、Doorbell、PM4 与 SDMA。
- Fence、IH、MSI-X 与 HSA signal。
- HIP → ROCm/KFD/amdgpu → GPU Dispatch 链路。
- gem5 GPU 模型调试点与 cosim 特有 transport/workaround。

每个实验都会区分真实 AMD GPU 行为、gem5 建模和 cosim-gpu 特有实现。实验时同时打开[系统架构](architecture.md)的数据流图和[参考](reference.md)中的源码地图。

## 故障诊断顺序

定位第一个失败层，并保持原始 artifact 目录不变：

| 现象 | 首个动作 |
|---|---|
| Host、KVM、Docker、磁盘或网络失败 | 重新运行对应的 `cosim_preflight.sh` profile，并使用其检查 ID/修复提示 |
| QEMU/gem5/m5/Guest 构建失败 | 保留 wrapper 的 build log/provenance，检查 `cosim_build.sh status`，再只重试失败的构建动作 |
| 模型始终未就绪 | 检查该次运行的 `gem5.log`、launcher category 与 manifest；不要单独启动 QEMU |
| Guest 未到达登录 | 将 `qemu.log` 与 `gem5.log` 一起检查；保留相同 run ID 以便归属 |
| PCI 可见但 driver/KFD/ROCm 不完整 | 结束会话、保留证据、按 manifest 清理，并在全新会话重试 |
| HIP timeout、GPUVM、PM4、SDMA、fence 或 IH 失败 | 使用 `verdict.json` 原因和调试参考中的有界原始日志窗口；不能只根据 pass marker 接受结果 |
| 清理未验证 | 把该行视为失败，并且只用带精确 manifest 的 `cosim_cleanup.sh` |

错误特征与源码位置见[参考与调试](reference.md)。提取较小窗口前应保留完整原始日志；QEMU 退出可能只是 gem5 故障的次生现象。

## 本地检查点

2026-08-24，本工作区已有一个覆盖锁定构建、Guest driver/ROCm 枚举和分类后的全新会话 HIP baseline 的本地检查点。证据保留在被忽略的 `artifacts/` 下，没有提交到 Git。该日期只是上下文，不是 release 保证；每台 Host 都必须重新得到自己的 preflight、hash、verdict、matrix 和 cleanup 结果。

## 后续阅读

- [学习实验](labs.md)
- [系统架构](architecture.md)
- [参考与调试](reference.md)
- [English Getting Started](../en/getting-started.md)
