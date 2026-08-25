---
name: cosim-gpu-launch
description: 启动 QEMU+gem5 协同仿真环境时使用；通过标准 launcher 以 vfio-user 连接 Docker 内的 gem5 与 Host 上使用 KVM 的 QEMU，并保留 run-scoped manifest 与清理证据。
---

# Cosim 启动

启动 QEMU+gem5 协同仿真环境。

这是交互式 launcher，不是测试 harness。它适合手动 boot、交互检查、transport
检查和 standalone QEMU+gem5 会话；接受 `--artifact-dir` 并保存 launch/cleanup
证据，但不创建测试 matrix，也不执行测试 runner 的 source/binary provenance gate。
需要 case-managed acceptance evidence 时使用 `cosim-gpu-test`。

Standalone launcher 默认继承 `COSIM_STRICT_ACCEPTANCE=0`，允许使用 working
provenance 做可重放的交互/诊断启动。设置 `COSIM_STRICT_ACCEPTANCE=1` 只会让 run
preflight 增加 clean-tree 与 tracked-lock gate；它不会补齐 runner metadata、test
binary provenance、verdict 或 matrix，因此 standalone artifact 无论该值为何都不能
进入 strict v2 matrix。最终 accepted row 必须由 `cosim-gpu-test` 在 clean tree 上显式
以 `COSIM_STRICT_ACCEPTANCE=1` 新建。

## 快速入口

```bash
./scripts/cosim_preflight.sh run --output-dir artifacts/preflight/run
COSIM_RUN_ID=interactive-001 ./scripts/cosim_launch.sh \
    --artifact-dir artifacts/standalone/interactive-001
COSIM_RUN_ID=transport-debug-001 ./scripts/cosim_launch.sh \
    --artifact-dir artifacts/standalone/transport-debug-001 \
    --gem5-debug MI300XCosim
```

Guest 启动后由 `cosim-gpu-setup.service` 自动加载驱动。若服务失败，使用
`cosim-gpu-guest` 采集状态，但不得手工写 ROM 或原地卸载/重载驱动；保存 artifact
后按 manifest 清理，修复固定输入并启动全新会话。

## 自动执行边界

启动前以与 launcher 相同的 `COSIM_STRICT_ACCEPTANCE` 值执行
`scripts/cosim_preflight.sh run`。标准 launcher 已为本次 run
创建独立 socket、shared memory、overlay、container 与 manifest，并在退出时验证
清理。发现旧资源时先执行 `scripts/cosim_cleanup.sh --run-id <id>` 查看 dry-run；
只有 manifest 明确归属该 run 时，才使用同一命令加 `--confirm`。不得执行无范围
清理。只有扩大到仓库脚本之外的清理、删除无关文件或 full/cold rebuild 时才询问
用户。

## Transport

当前 launcher 只支持标准 vfio-user：Host QEMU 使用 `vfio-user-pci`，通过 run-scoped
Unix socket 连接 gem5 的 `libvfio-user` endpoint。本仓库没有第二套 legacy backend
启动入口。

## 架构

```
QEMU (Q35+KVM, host) ←Unix socket→ gem5 (MI300X GPU, Docker)
```

共享内存使用 run ID 命名：Guest RAM 为
`/dev/shm/cosim-guest-ram-<run-id>`，VRAM 为
`/dev/shm/mi300x-vram-<run-id>`。BAR 布局为 0+1=VRAM、2+3=Doorbell、
4=MSI-X、5=MMIO。

## Driver 参数

```
ip_block_mask=0x67  # 启用 common/GMC/IH/GFX/SDMA，禁用 PSP bit 3 与 SMU bit 4
ppfeaturemask=0     # 禁用 power play
dpm=0               # 禁用动态电源管理
audio=0             # 不建模 audio controller
ras_enable=0        # 禁用 RAS
discovery=2         # 使用 firmware-based discovery
```

## 故障路由

| 症状 | 路由 |
|---|---|
| gem5 container 在启动期间退出 | 使用 `cosim-gpu-debug` 比较同一 run 的 gem5、QEMU 与 Guest log，确定第一个失败组件 |
| Driver 解析 AtomBIOS 失败 | 使用 `cosim-gpu-guest` 检查 setup service、ROM 可见性与构建 provenance；禁止手工写 `/dev/mem` |
| KIQ 或 SDMA timeout `-110` | 保存 queue/ring/progress 证据；没有最终功能 PASS 时不能当作噪声忽略 |
| DRM client `-13` / `EPERM` | 检查固定 Guest image metadata、DKMS 内容与权限；需要改镜像时转入 `cosim-gpu-disk-image-edit` |

## 与其他技能的关系

- 启动前发现 binary 缺失或 provenance 过期时使用 `cosim-gpu-build`。
- 运行中交互使用 `cosim-gpu-guest`，双侧定位使用 `cosim-gpu-debug`。
- 自动执行与分类使用 `cosim-gpu-test`；该技能内部负责 launch 与 fresh-session
  isolation。
