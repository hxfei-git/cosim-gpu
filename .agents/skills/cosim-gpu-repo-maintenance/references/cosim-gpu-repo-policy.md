# cosim GPU 仓库策略

本文记录 cosim-gpu 特有的仓库维护规则。通用 Git 操作规范保留在主技能中，不应
误写成项目独有行为。

## 仓库结构

顶层仓库包含以下内容：

| 路径 | 含义 |
|---|---|
| `.agents` | 直接随仓库维护的可复用 cosim GPU 技能 |
| `gem5` | 仿真器源码 submodule |
| `gem5-resources` | 资源与 Guest 镜像输入 submodule |

Superproject 使用 gitlink 记录 `gem5` 与 `gem5-resources` 的提交。`.agents` 是普通
顶层内容，不是独立 skill 仓库或 gitlink。Submodule 工作树 dirty 与 superproject
指针变化是两种不同状态。

## 规则与技能位置

顶层代理规则只有 `AGENTS.md` 一个入口。可复用工作流位于 `.agents/skills/` 并由
本仓库直接跟踪；不得在 `.claude/commands` 下新增项目专用命令实现。技能目录改名
时必须同步更新 `AGENTS.md` 与合同测试中的路由。

## 提交归属

- `.agents` 内的修改属于顶层仓库，直接提交到 cosim-gpu。
- `gem5` 或 `gem5-resources` 内的修改必须先在对应 submodule 中提交，再由顶层仓库记录 gitlink。
- 顶层脚本、文档、测试和忽略规则属于 superproject。
- 不得把 submodule 内部源码修改与顶层指针更新混成一个 submodule 提交；它们属于不同仓库。

## 项目提交规则

- gem5 应运行 pre-commit 钩子，并在适用时使用 `MAINTAINERS.yaml` 中的标签。
- 顶层 cosim-gpu 没有项目专用 hook。
- 除非 submodule 另有身份要求，使用顶层 Git 配置中的身份添加 `Signed-off-by`。

## 生成物

提交默认排除：

- `artifacts/`
- `m5out/`
- `local-cosim-runs/`
- `*.log`
- 测试 `.out`、`.strace`、`.gdb`、`.proc` 与 guest-run 文件
- 未明确提升为仓库脚本或正式文档的临时脚本和一次性命令记录

如需长期保留生成证据，优先在 `docs/` 中编写精简来源文档，或在任务 workspace
中保存 artifact 摘要。

## 文档布局

根目录只保留中文 `README.zh.md`。正式项目文档直接放在 `docs/` 下，使用中文
文件名和中文内容；`docs/文档索引.md` 是统一入口。不得重新引入 `docs/zh/`、
`docs/en/` 或重复的英文项目文档。

## 安全检查 submodule 指针

提交 submodule 指针前（`gem5` 或 `gem5-resources`；`.agents` 为 vendored 内容，
没有指针）运行：

```bash
git submodule status
git -C <submodule> status --short
git -C <submodule> log --oneline -1
git diff --submodule=short -- <submodule>
```

若 submodule 含本地提交，确认顶层指针记录预期的最终提交。若 submodule 工作树
dirty，先完成或拆分该工作，再提交指针。

## 拆分技能修改

`.agents` 技能修改应按读者可感知的行为划分提交边界：

- 路由或触发条件
- 共享审查或工作流合同
- 测试、构建或调试参考资料抽取
- 项目策略或仓库维护技能

拆分后确认最终工作树符合预期，并检查所有引用均指向现存文件。
