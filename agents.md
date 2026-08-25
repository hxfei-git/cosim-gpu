# cosim-gpu 代理规则

## 适用范围

本仓库包含 QEMU + gem5 MI300X GPU 协同仿真的源代码树。可复用的代理工作流不再存储于 `.claude/commands`；这些工作流已内置到仓库的 `.agents/skills/` 下（此前为 `gevico/cosim-gpu-skills` 子模块），并由本仓库直接跟踪。

`CLAUDE.md` 是指向本文件的符号映射，因此兼容 Claude 的工具会读取同一套主规则。

## 语言规则

- 所有由代理生成的自然语言内容必须使用中文，包括 `AGENTS.md`、`agents.md`、说明文档、代码注释、提交说明、审查意见、进度汇报以及与用户的交互。
- 与 `README.zh.md` 成对的根目录英文 `README.md`、`docs/en/` 下与 `docs/zh/` 成对维护的英文项目文档，以及双语索引 `docs/README.md` 是明确例外；英文对应内容使用英文，双语索引可以同时使用中英文。
- 命令、路径、代码标识符、协议字段、工具原始输出和上游要求使用固定语言的内容可以保留原文；如需解释，必须使用中文。
- 修改现有非中文代理规范时，应同步转换为中文，避免同一规则文件中出现相互冲突的语言要求。

## 技能路径

运行以下工作流前，必须加载对应技能；非平凡测试、调试、审查或迭代实现先加载
flow plan，再进入相应领域技能。普通构建、启动、文档或仓库维护按对应技能直接执行：

- 非平凡测试、调试、审查或迭代任务：`.agents/skills/cosim-gpu-flow-plan/SKILL.md`
- QEMU、gem5、m5、Guest 构建与 provenance：`.agents/skills/cosim-gpu-build/SKILL.md`
- 启动 QEMU+gem5 协同仿真：`.agents/skills/cosim-gpu-launch/SKILL.md`
- 客户机串口交互：`.agents/skills/cosim-gpu-guest/SKILL.md`
- GPU 程序执行、分类与矩阵：`.agents/skills/cosim-gpu-test/SKILL.md`
- crash、hang、timeout、GPUVM 等故障：`.agents/skills/cosim-gpu-debug/SKILL.md`
- HSA signal、HIP wait、KFD、PASID/VMID：`.agents/skills/cosim-gpu-rocm-stack/SKILL.md`
- 使用 `guestmount` 编辑客户机磁盘镜像：`.agents/skills/cosim-gpu-disk-image-edit/SKILL.md`
- 大型日志、artifact、矩阵和 prior evidence 汇总：`.agents/skills/cosim-gpu-info-gathering/SKILL.md`
- 审查编排与 PR 证据：`.agents/skills/cosim-gpu-review/SKILL.md`
- 委派独立 AI 审查：`.agents/skills/cosim-gpu-codex-review/SKILL.md`
- 已规划任务的迭代闭环：`.agents/skills/cosim-gpu-rlcr-loop/SKILL.md`
- Git、submodule、提交拆分、规则与生成物卫生：`.agents/skills/cosim-gpu-repo-maintenance/SKILL.md`

不得在 `.claude/commands` 下添加新的项目专用命令实现。可复用工作流应添加到 `.agents/skills/`，随后更新此映射。

## 构建

```bash
./scripts/cosim_preflight.sh build \
    --output-dir artifacts/preflight/build
./scripts/cosim_build.sh all
./scripts/cosim_build.sh status
```

聚焦 action 为 `qemu`、`gem5`、`m5` 和 `guest`。所有构建必须委托给
`scripts/cosim_build.sh`；不得直接调用 Docker、SCons、Packer、QEMU make 或旧的
`run_mi300x_fs.sh`。资源不足或构建失败时，保留 wrapper artifact 并按构建技能分类，
不要绕过 hash/provenance gate。

## 启动

```bash
./scripts/cosim_launch.sh
./scripts/cosim_launch.sh --gem5-debug MI300XCosim
```

客户机启动后，`cosim-gpu-setup.service` 会复制 ROM 并加载 `amdgpu`。若服务缺失或
失败，应保存当前 artifact、setup service log 与构建 provenance，按 manifest 清理后
修复固定的 Guest 构建输入，并使用全新会话复验。禁止为制造 PASS 手工写
`/dev/mem`，也禁止在部分 `hw_init` 后原地卸载或重载 `amdgpu`。

## 架构

系统 QEMU（Q35+KVM）通过 Unix 套接字上的标准 vfio-user 协议与 gem5 的 MI300X 模型通信。本项目不使用定制的 QEMU 源代码。客户机内存和 VRAM 使用包含安全 run ID 的独立 `/dev/shm/cosim-guest-ram-<run-id>` 与 `/dev/shm/mi300x-vram-<run-id>`；socket、container、overlay 和 manifest 同样属于该次运行。BAR 布局：0+1=VRAM、2+3=Doorbell、4=MSI-X、5=MMIO。

此协同仿真路径使用以下驱动参数：

```text
ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0 discovery=2
```

## 提交规则

- gem5：应用 pre-commit 钩子；使用 `MAINTAINERS.yaml` 中的标签。
- 顶层 cosim-gpu：没有项目专用钩子。
- 遵循 Conventional Commit 格式。Conventional Commit 类型、可选 scope、代码标识符、命令和路径可以保留原文，标题及其余提交说明必须使用中文。
- 每个提交正文必须包含 `摘要` 和 `具体修改` 两部分；`摘要` 说明修改目的或结果，`具体修改` 逐项列出实际变更。
- 使用 `git config user.name` 和 `git config user.email` 中的身份添加 `Signed-off-by` 并签署提交。

提交说明使用以下格式：

```text
<type>(<scope>): <中文标题>

摘要：
- <修改目的或结果概述>

具体修改：
- <具体变更一>
- <具体变更二>

Signed-off-by: <姓名> <邮箱>
```

## 修改完成与推送策略

- 完成并验证每一项范围明确的修改后，汇报结果；用户要求本地提交时，创建主题明确的签名提交。
- 本地提交、用户确认修改成功或要求继续下一阶段，都不构成远端推送授权。只有用户单独明确要求推送并确认目标 remote/branch 后才能推送。
- 提交中不得包含工作区内无关的修改。除非用户明确要求且目标已经核实，否则不得强制推送。

## 文档规则

- `docs/zh/` 与 `docs/en/` 下的语言专用项目文档必须成对维护；`docs/README.md` 是双语索引，不要求创建第二份副本。
- 第一行使用 `[English](../en/<file>.md)` 或 `[中文](../zh/<file>.md)`。
- 添加或修改文档时，必须同时更新两个语言版本。
- 文档或代理规则变化后运行 `python3 -B scripts/test_docs_contract.py`，验证配对、链接、Lab、故障 playbook、公开命令与关键技能路由。
