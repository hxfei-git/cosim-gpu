# cosim-gpu 仓库与代理指南

## 适用范围

本仓库编排 QEMU 与 gem5，以实现 MI300X GPU 协同仿真。可复用代理工作流由仓库直接跟踪在 `.agents/skills/` 下，不在 `.claude/commands` 中维护项目专用命令。

## P0：AMD GPU 学习实验平台

在安全、正确性和用户当前明确任务之后，下列目标是本项目的最高内部优先级。本仓库不只是构建和 regression 工程，还要建设为一台“可暂停、可加日志、可修改硬件模型”的 AMD GPU 学习实验平台：Guest 运行固定版本的真实 amdgpu/KFD/ROCm 软件栈，QEMU 与 gem5 提供可观察、可重放、可修改的GPU 设备与执行模型。

默认学习与实验目标是本仓库当前锁定的 `gfx942`/MI300X cosim 配置。除非用户另行明确要求，不得把
购买、部署或使用物理 AMD GPU 当作学习路线的前置条件。既有基础资料中的 RDNA 3/GFX11 内容只是概念或对比样例；Host/Runtime/Driver/Queue/Packet/Doorbell/Completion 等通用关系可以迁移，WGP/SIMD 组织、寄存器、MQD/HQD、ISA 和 IP block 细节必须以当前锁定的 Guest 软件与 gem5 源码/运行证据重新核对，不得直接套用。

当前学习与实验建设必须优先贯通两条主线：

1. **启动初始化**：PCI 枚举、BAR/MMIO、ROM/discovery、`amdgpu_pci_probe`、
   IP block 初始化、GART/GPUVM、ring/IH/SDMA/GFX、KFD device/topology，
   直到 ROCr/HIP 枚举 `gfx942` agent。
2. **Packet dispatch**：HIP launch 经 CLR/ROCr 构造并发布 AQL packet，KFD/amdgpu
   建立 process/queue/VMID/PASID/MQD 状态，doorbell 经 vfio-user 到达 gem5，
   再经 HSA packet processor、command processor、dispatcher/CU 执行，最后通过
   completion signal 及 polling 或 IH/MSI-X 路径返回正确结果。

这两条主线是当前的优先锚点，不是学习范围的上限。新增 GPUVM、cache、
PM4、SDMA、firmware 边界、调度、同步、故障恢复或其他主题时，应将其
连接到可观察的初始化、提交、执行或完成状态转换，而不是只增加孤立的
概念说明或大段原始日志。

面向这一 P0 目标的文档、脚本和 Lab 应当：

- 让每个关键状态转换都能定位到实际源码、函数、packet/queue/object 身份和
  有界的运行证据；优先生成易读的 timeline/table，不要让学习者盲读整份大日志。
- 同时标明哪些是真实 AMD 软件行为、哪些是 gem5 建模、哪些是 cosim-gpu
  特有 transport、instrumentation 或 workaround，并禁止将后两者宣称为物理
  MI300X 的完整行为。
- 为每个实验给出前置状态、操作、预期状态转换、源码阅读点、可修改点、
  失败边界和恢复方法；一次只改一个变量，并能用同一 target 重放对比。
- 优先补齐与 Guest 版本一致的 CLR、ROCr 和 amdgpu/KFD 源码/调试映射，
  但保留已验证的二进制 baseline，使源码实验可随时回退。
- 构建成功、Guest 枚举或单独 `[PASS]` 都不是学习主线的完成标准；
  必须证明目标路径中的实际状态转换和最终正确结果。
- 不得为追求更广的 regression、更多 workload 或更复杂的证据合同，持续推迟
  两条主线的可观察性、源码导航和可修改实验；但已有 baseline 的正确性、
  provenance 与安全清理保证不得被削弱。

## 项目结构与模块组织

`gem5/` 包含仿真器和 GPU 模型，`gem5-resources/` 包含 Guest 镜像、内核及
workload；二者均为 Git submodule。使用 `git submodule update --init --recursive`
初始化。顶层编排脚本位于 `scripts/`，SST 配置位于 `configs/sst/`，HIP 测试位于
`tests/kernels/`，共享测试辅助代码位于 `tests/common/`，中文项目文档直接位于
`docs/` 并使用中文文件名。更新顶层仓库中的 submodule 指针前，必须先在对应
submodule 中提交修改。

## 语言与文档规则

- 所有由代理生成的自然语言内容必须使用中文，包括 `AGENTS.md`、说明文档、代码注释、提交说明、审查意见、进度汇报及与用户的交互。
- 根目录仅保留中文 `README.zh.md`；项目文档直接放在 `docs/` 下，使用中文文件名和中文内容，不维护重复的英文副本。`docs/文档索引.md` 是统一文档入口。
- 命令、路径、代码标识符、协议字段、工具原始输出及上游固定语言内容可以保留原文；相关解释必须使用中文。
- 文档或代理规则变化后运行 `python3 -B scripts/test_docs_contract.py`，验证单层目录、链接、Lab、故障 playbook、公开命令与关键技能路由。

## 技能路径

运行下列工作流前必须加载对应技能。非平凡测试、调试、审查或迭代实现先加载
flow plan，再进入相应领域技能；普通构建、启动、文档或仓库维护直接使用对应技能：

- 非平凡测试、调试、审查或迭代任务：`.agents/skills/cosim-gpu-flow-plan/SKILL.md`
- QEMU、gem5、m5、Guest 构建与 provenance：`.agents/skills/cosim-gpu-build/SKILL.md`
- 启动 QEMU+gem5 协同仿真：`.agents/skills/cosim-gpu-launch/SKILL.md`
- Guest 串口交互：`.agents/skills/cosim-gpu-guest/SKILL.md`
- GPU 程序执行、分类与矩阵：`.agents/skills/cosim-gpu-test/SKILL.md`
- crash、hang、timeout、GPUVM 等故障：`.agents/skills/cosim-gpu-debug/SKILL.md`
- HSA signal、HIP wait、KFD、PASID/VMID：`.agents/skills/cosim-gpu-rocm-stack/SKILL.md`
- 使用 `guestmount` 编辑 Guest 磁盘镜像：`.agents/skills/cosim-gpu-disk-image-edit/SKILL.md`
- 大型日志、artifact、矩阵和既有证据汇总：`.agents/skills/cosim-gpu-info-gathering/SKILL.md`
- 审查编排与 PR 证据：`.agents/skills/cosim-gpu-review/SKILL.md`
- 委派独立 AI 审查：`.agents/skills/cosim-gpu-codex-review/SKILL.md`
- 已规划任务的迭代闭环：`.agents/skills/cosim-gpu-rlcr-loop/SKILL.md`
- Git、submodule、提交拆分、规则与生成物卫生：`.agents/skills/cosim-gpu-repo-maintenance/SKILL.md`

不得在 `.claude/commands` 下添加新的项目专用命令实现。可复用工作流应添加到
`.agents/skills/`，随后更新上述映射。

## 构建、启动与测试入口

```bash
./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build
./scripts/cosim_build.sh all
./scripts/cosim_build.sh status
```

- `./scripts/cosim_build.sh all` 是 QEMU、gem5、m5 和 Guest 的唯一构建入口；使用 `qemu`、`gem5`、`m5`、`guest` 或 `status` 执行聚焦动作。
- 所有构建必须委托给 `scripts/cosim_build.sh`；不得直接调用 Docker、SCons、Packer、QEMU make 或旧的 `run_mi300x_fs.sh`。构建失败时保留 wrapper artifact 并按构建技能分类，不得绕过 hash/provenance gate。
- `./scripts/cosim_preflight.sh host|build|run` 执行对应 profile 的只读前置检查。
- `./scripts/cosim_launch.sh` 用于交互式启动；需要 gem5 调试时使用 `./scripts/cosim_launch.sh --gem5-debug MI300XCosim`。
- `./scripts/run_cosim_tests.sh vector_add` 在全新会话中运行单个算子；单项通过后再使用 `--all`。这些运行要求 Linux、KVM、Docker、支持 `vfio-user-pci` 的 QEMU 以及已构建的 Guest 资源。
- 静态与合同回归包括 `shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh tests/scripts/*.sh`、顶层 `scripts/test_*_contract.sh`、`tests/scripts/test_*.sh`、`python3 -B -m unittest discover -s tests/unit -v` 和 `python3 -B scripts/test_docs_contract.py`。

## Guest 与运行时安全

Guest 启动后，`cosim-gpu-setup.service` 会复制 ROM 并加载 `amdgpu`。若服务缺失或
失败，保存当前 artifact、setup service log 与构建 provenance，按 manifest 清理，
修复固定的 Guest 构建输入，并使用全新会话复验。禁止为制造 PASS 手工写
`/dev/mem`，也禁止在部分 `hw_init` 后原地卸载或重载 `amdgpu`。

系统 QEMU（Q35+KVM）通过 Unix socket 上的标准 vfio-user 协议与 gem5 MI300X 模型
通信；项目不使用定制 QEMU 源码。Guest 内存和 VRAM 使用包含安全 run ID 的独立
`/dev/shm/cosim-guest-ram-<run-id>` 与 `/dev/shm/mi300x-vram-<run-id>`；socket、
container、overlay 和 manifest 同样归属于该次运行。BAR 布局为：0+1=VRAM、
2+3=Doorbell、4=MSI-X、5=MMIO。

此路径使用以下驱动参数：

```text
ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0 discovery=2
```

## 编码与测试约定

Bash、Python 和 HIP/C++ 使用四空格缩进。Shell 变量展开必须加引号；环境变量与
配置变量使用大写命名；Shell/Python 函数及 HIP 测试文件名使用小写 `snake_case`；
Python 类使用 `CamelCase`；C/C++ 左花括号与声明位于同一行。顶层没有全局格式化
工具，应保持相邻代码的既有风格。`gem5/` 内遵循其 pre-commit 钩子和
`MAINTAINERS.yaml` 标签要求。

算子测试添加为 `tests/kernels/<snake_case>.cpp`，Makefile 生成
`tests/build/<stem>`。复用 `tests/common/test_utils.h`，失败时返回非零值，并输出
标准 `[PASS]` 或 `[FAIL]` 摘要。本项目没有数值化覆盖率阈值；Guest 状态可能影响
后续测试，因此优先使用全新协同仿真会话。

## 提交、拉取请求与推送

- 遵循 Conventional Commit，例如 `fix(scripts): 修复陈旧套接字处理` 或 `docs: 更新启动指南`。类型、可选 scope、代码标识符、命令和路径可保留原文，标题及其余提交说明必须使用中文。
- 每个提交正文必须包含 `摘要` 与 `具体修改` 两部分，提交聚焦单一主题，并使用 `git commit -s` 签名。身份取自 `git config user.name` 和 `git config user.email`。
- 拉取请求以 `main` 为目标分支，使用中文说明修改动机、受影响路径、关联问题及准确的验证命令和结果；运行时故障注明 submodule 修订并附相关日志。
- 完成并验证范围明确的修改后汇报结果；只有用户要求本地提交时才创建主题明确的签名提交。
- 本地提交、用户确认修改成功或要求继续下一阶段均不构成推送授权。只有用户单独明确要求推送并确认目标 remote/branch 后才能推送；远端推送必须由用户单独明确授权。
- 提交不得包含工作区中的无关修改，未获明确授权且未核实目标时不得强制推送。

提交说明格式：

```text
<type>(<scope>): <中文标题>

摘要：
- <修改目的或结果概述>

具体修改：
- <具体变更一>
- <具体变更二>

Signed-off-by: <姓名> <邮箱>
```

生成的构建产物、日志、`artifacts/`、`m5out/` 和本地临时运行文件不得提交。
