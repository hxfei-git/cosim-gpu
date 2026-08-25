# 仓库指南

## 项目结构与模块组织

本仓库用于编排 QEMU 和 gem5，以实现 MI300X 协同仿真。`gem5/` 包含模拟器和 GPU 模型，`gem5-resources/` 包含客户机镜像、内核及工作负载；二者均为 Git 子模块。使用 `git submodule update --init --recursive` 初始化子模块。顶层编排脚本位于 `scripts/`，SST 配置位于 `configs/sst/`，HIP 测试位于 `tests/kernels/`，共享测试辅助代码位于 `tests/common/`，中英文文档分别位于 `docs/zh/` 和 `docs/en/`。更新顶层仓库中的子模块指针之前，必须先在对应子模块仓库中提交修改。

## 构建、测试与开发命令

- `./scripts/cosim_build.sh all` 是 QEMU、gem5、m5 和 Guest 的唯一构建入口；使用 `qemu`、`gem5`、`m5`、`guest` 或 `status` 执行聚焦动作。不得手写 Docker、SCons、Packer 或 QEMU 构建命令。
- `./scripts/cosim_preflight.sh host|build|run` 用于执行对应 profile 的只读前置检查；`./scripts/cosim_launch.sh` 用于交互式启动协同仿真。
- `./scripts/run_cosim_tests.sh vector_add` 用于在全新会话中运行单个算子；`--all` 用于运行完整集成测试套件。这些运行要求 Linux、KVM、Docker、支持 `vfio-user-pci` 的 QEMU，以及已经构建完成的客户机资源。
- `shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh tests/scripts/*.sh`、顶层 `scripts/test_*_contract.sh`、`tests/scripts/test_*.sh`、`python3 -B -m unittest discover -s tests/unit -v` 和 `python3 -B scripts/test_docs_contract.py` 用于静态、文档与合同回归。

## 编码风格与命名约定

Bash、Python 和 HIP/C++ 使用四空格缩进。Shell 变量展开必须加引号，环境变量和配置变量使用大写命名，Shell/Python 函数及 HIP 测试文件名使用小写 `snake_case`。Python 类使用 `CamelCase`；C/C++ 左花括号与声明保持在同一行。顶层仓库没有全局格式化工具，因此应保持相邻代码的既有风格。运行 `shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh` 进行检查。在 `gem5/` 内，遵循其 pre-commit 钩子和 `MAINTAINERS.yaml` 标签要求。

## 测试指南

算子测试应添加为 `tests/kernels/<snake_case>.cpp`；Makefile 会生成 `tests/build/<stem>`。复用 `tests/common/test_utils.h`，失败时返回非零值，并输出标准的 `[PASS]` 或 `[FAIL]` 摘要。本项目没有数值化覆盖率阈值。客户机状态可能影响后续测试，因此优先使用全新的协同仿真会话。

## 提交与拉取请求指南

遵循既有 Conventional Commit 风格，例如 `fix(scripts): 修复陈旧套接字处理` 或 `docs: 更新启动指南`。除 Conventional Commit 类型、可选 scope、代码标识符、命令和路径等必须保留原文的内容外，提交标题和正文必须使用中文。每个提交正文必须同时包含 `摘要` 和 `具体修改` 两部分，分别说明修改目的和实际变更。提交必须聚焦单一主题，并使用 `git commit -s` 签名。拉取请求应以 `main` 为目标分支，并使用中文说明修改动机、受影响路径、关联问题，以及准确的验证命令和结果。运行时故障需要注明子模块修订版本并附上相关日志。英文和中文项目文档必须成对更新。

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

## 代理专用说明

所有由代理生成的自然语言内容必须使用中文，包括 `AGENTS.md`、`agents.md`、说明文档、代码注释、提交说明、审查意见、进度汇报以及与用户的交互。与 `README.zh.md` 成对的根目录英文 `README.md`、`docs/en/` 下与 `docs/zh/` 成对维护的英文项目文档，以及双语索引 `docs/README.md` 是明确例外；英文对应内容必须使用英文，双语索引可同时使用中英文。命令、路径、代码标识符、协议字段、工具原始输出和上游要求使用固定语言的内容可以保留原文；如需解释，应使用中文。自动化贡献者还必须遵循 `agents.md`，并在规划、构建、启动、测试、调试、审查或仓库维护前加载 `.agents/skills/` 下匹配的工作流。生成的构建产物、日志和 `artifacts/` 不得提交；远端推送必须由用户单独明确授权。
