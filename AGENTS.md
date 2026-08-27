# cosim-gpu 仓库指南

## 适用范围

本仓库在 Host 上运行 QEMU+KVM，在 Docker 中运行 gem5 MI300X GPU 模型，并通过
标准 vfio-user Unix socket 向 Guest Linux 暴露 `gfx942`/MI300X 设备。Guest
运行固定版本的 amdgpu/KFD/ROCm 软件栈；gem5 与 cosim transport 不是物理 MI300X
完整行为。

## 仓库与 submodule

- `gem5/` 是模拟器和 GPU 模型 submodule。
- `gem5-resources/` 是 Guest 镜像配方、内核和 workload 资源 submodule。
- `scripts/` 保存 Host 侧检查、构建、启动、测试和清理入口。
- `tests/kernels/` 保存 HIP 算子，`tests/common/` 保存共享测试辅助代码。
- `docs/` 保存中文项目文档。

初始化使用：

```bash
git submodule update --init --recursive
```

不要在顶层提交尚未提交的 submodule 工作树作为指针更新；需要更新 gitlink 时，先在
对应 submodule 中提交，再更新顶层指针。

## 稳定运行架构

规范 QEMU 由仓库锁定并构建到
`.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64`；它不是仓库内的 QEMU
submodule。gem5 规范二进制是 `gem5/build/VEGA_X86/gem5.opt`，运行镜像由
`scripts/Dockerfile.run` 构建。

每次运行使用独立 run ID，资源包括：

- `/tmp/gem5-mi300x-<run-id>.sock`；
- `/dev/shm/cosim-guest-ram-<run-id>`；
- `/dev/shm/mi300x-vram-<run-id>`；
- `/tmp/cosim-<run-id>.session/guest-overlay.qcow2`；
- `gem5-cosim-<run-id>` Docker container 和对应 resource manifest。

BAR 布局为 0+1=VRAM、2+3=Doorbell、4=MSI-X、5=MMIO。Guest 启动时
`cosim-gpu-setup.service` 发布 ROM/discovery 数据并使用以下参数加载 amdgpu：

```text
ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0 discovery=2
```

部分 `hw_init` 后不要在同一 Guest 原地卸载或重载 amdgpu，也不要为制造成功结果
手工写 `/dev/mem`；结束该 run 后使用全新会话。

## 真实命令入口

只读检查：

```bash
./scripts/cosim_preflight.sh host
./scripts/cosim_preflight.sh build
./scripts/cosim_preflight.sh run
```

构建与状态：

```bash
./scripts/cosim_build.sh status
./scripts/cosim_build.sh all
```

`cosim_build.sh` 还接受 `lock-qemu-source`、`qemu`、`gem5`、`m5`、
`guest` 和可选 `--force`。

交互启动、测试和按 run 清理：

```bash
./scripts/cosim_launch.sh
./scripts/cosim_launch.sh --gem5-debug MI300XCosim
./scripts/run_cosim_tests.sh vector_add
./scripts/run_cosim_tests.sh --all
./scripts/cosim_cleanup.sh --run-id replace-with-run-id
```

参数以各脚本的 `--help` 为准。不要直接调用 Docker/SCons/Packer/QEMU Makefile
代替构建 wrapper，也不要做无 run ID 或无 manifest 归属的宽泛清理。

轻量合同检查包括：

```bash
shellcheck scripts/*.sh tests/run_tests.sh tests/test_modprobe_params.sh tests/scripts/*.sh
python3 -B -m unittest discover -s tests/unit -v
python3 -B scripts/test_docs_contract.py
```

## Skill 路由

仅当请求直接匹配下列项目专属操作时读取对应 skill；普通源码编辑、设计讨论、文档、
Git 或审查任务直接检查仓库并使用模型自身能力，不套用 skill：

- 构建 QEMU、gem5、m5 或 Guest：`.agents/skills/cosim-gpu-build/SKILL.md`
- 启动/清理会话或与运行中 Guest 交互：`.agents/skills/cosim-gpu-run/SKILL.md`
- 运行 `tests/kernels` 算子：`.agents/skills/cosim-gpu-test/SKILL.md`
- 定位 cosim crash、hang、timeout 或 GPU 路径故障：`.agents/skills/cosim-gpu-debug/SKILL.md`
- 离线修改 Guest raw disk image：`.agents/skills/cosim-gpu-disk-image-edit/SKILL.md`

## 语言与文档

- 代理生成的说明、文档、注释和用户交互使用中文；命令、路径、代码标识符、协议字段
  与工具原始输出可以保留原文。
- 根目录只保留 `README.zh.md`；正式文档直接放在单层 `docs/` 下，使用中文
  文件名和中文内容，不维护重复的英文副本。
- `docs/文档索引.md` 是统一文档入口。新增、删除或重命名正式文档时同步更新索引
  和所有本地链接。
- 文档或代理规则变化后运行 `python3 -B scripts/test_docs_contract.py`。
