[English](../en/labs.md)

# AMD GPU Driver 与 GPU Architecture 实验

这组实验把已经跑通的 MI300X 协同仿真 baseline 变成一套由真实源码和可复核
artifact 驱动的长期学习环境。所有命令均从仓库根目录执行，并按顺序完成；后续实验
默认已经理解前面建立的证据链。

## 学习顺序

1. **PCI / BAR / MMIO**——先确认 Linux 如何发现设备，以及寄存器访问如何跨过
   QEMU-gem5 边界。
2. **amdgpu / KFD 初始化**——沿 Guest 中的真实驱动路径，从 probe 跟到 DRM、
   KFD 和 ROCm agent。
3. **VRAM / GTT / GART / GPUVM**——区分内存域、地址转换和页表状态。
4. **Ring / Queue / Doorbell**——区分内核 PM4 管理队列和用户 AQL 计算队列。
5. **PM4**——跟踪队列管理与同步 packet，并识别模型中的部分语义。
6. **SDMA**——跟踪 copy packet、地址转换、fence 和 trap。
7. **Fence / IH / MSI-X**——分别验证 polling 与 interrupt 完成路径。
8. **HIP 端到端 dispatch 与 gem5 debug**——串起完整链路，并练习可重复的
   first-failure 定位流程。

## 阅读和运行约定

### 层级标签

- `[REAL AMD]` 表示真实 Linux amdgpu/KFD 或 ROCm 软件栈的行为与源码。匹配的
  Guest 软件包由 `configs/cosim/guest.lock` 固定，但完整驱动源码没有 vendored 到
  本仓库。因此这里只给出 canonical 源码路径和函数名，不虚构本地行号；实际阅读时
  必须与 `configs/cosim/guest.lock` 中的版本对应。
- `[GEM5]` 表示 `gem5/` submodule 中 GPU 模型实现的行为。
- `[COSIM]` 表示 QEMU/KVM + vfio-user 集成、共享内存传输、启动策略或兼容性
  workaround；它不能作为物理 MI300X 行为的证明。

### 实测证据 baseline

以下证据记录于 2026-08-23，路径均相对于仓库，位于
`artifacts/amd-gpu-learning-env/tests/`：

| Run | 权威结果 | 关键现象 |
|---|---|---|
| `phase3-driver-002` | `phase3-verdict.json`: `PASS`，原因 `driver_rocm_probe_pass` | BAR0 16 GiB、BAR2 2 MiB、BAR4 8 KiB、BAR5 512 KiB；amdgpu 已绑定；存在 `/dev/kfd`、render node 和 `gfx942` |
| `phase4-baseline-vector-add-i0` | `verdict.json` 与 `dispatch-verdict.json`: `PASS` | polling 模式；Task 2；grid 4352；workgroup 0–16；HSA 与 kernel 均完成 |
| `phase4-interrupt-vector-add-i1` | `verdict.json` 与 `interrupt-verdict.json`: `PASS` | signal 1→0，随后是 IH cookie、IH write pointer，再到同一 gem5 tick 的 vfio-user IRQ vector 0 |

通用 artifact auditor 会把 `phase3-driver-002` 报为缺少 program identity，因为
Phase 3 是 driver probe，不是 operator test；该阶段应以专用的
`phase3-verdict.json` 为准。两个 Phase 4 run 是完整 operator artifact，使用相同的
gem5 source commit `4c1f90498f89e15a3797cb50e9b534164bc57536` 和 binary
SHA-256 `a395b7efdaef1067223bf1e3d82780f0bdde190bee99735b12e10c377e1777a1`。

`artifacts/` 已被 `.gitignore` 忽略，这些日志与 verdict 只保存在本地，不会进入
Git。清理 workspace 前需要显式复制或归档。

### 模型边界和已知限制

所有实验都必须保留以下边界说明：

- `[COSIM]` PCI function 是 synthetic vfio-user endpoint，不是物理 MI300X PCIe
  endpoint；reset、电源、固件、RAS 和错误行为并不等价于硬件。
- `[GEM5]` 实测 baseline 实例化 40 个 CU 和 16 GiB VRAM；Guest driver 从
  discovery topology 报告 `active_cu_number 320`。320 是 driver-visible topology，
  不是 gem5 实际建模的 CU 数量。
- `[COSIM]` 此路径禁用或省略 PSP、SMU、RAS、DPM、audio、VCN 和 JPEG。驱动
  参数为 `ip_block_mask=0x67 ppfeaturemask=0 dpm=0 audio=0 ras_enable=0
  discovery=2`。
- `[GEM5]` PM4、cache maintenance 以及 QEMU↔gem5 memory coherence 只实现
  部分语义；`ACQUIRE_MEM` 与 `SET_RESOURCES` 尤其不具备完整硬件语义，共享
  内存也不是完整 coherence protocol。
- `[COSIM]` 缺失 GART PTE 时可能回退到 physical address 0。这是危险的 keep-alive
  workaround，可能丢数据或访问 Guest RAM，绝不能描述为安全的硬件 sink。
- `[COSIM]` CP_EOP interrupt cookie 会把低位 user VMID clamp 到驱动的 compute
  VMID 范围；当前只模拟少量真实 IH source。

### 支持的命令模式

以下运行只使用仓库 wrapper，每个 output directory 必须是全新且为空：

```bash
LAB_RUN_ID="lab-example-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim vector_add
```

不要用临时拼出的 Docker、QEMU、SCons、socket 或 `/dev/shm` 命令替代 wrapper。
runner 会记录 program identity、Guest 生效环境、source snapshot、binary provenance、
原始日志、verdict、matrix row 和 scoped cleanup 结果。

## Lab 1：PCI / BAR / MMIO

### 原理

PCI enumeration 先建立设备身份和地址窗口，随后 amdgpu 才能访问设备。BAR0 暴露
VRAM，BAR2 承载 doorbell，BAR4 包含 MSI-X table/PBA，BAR5 承载 MMIO register。
MMIO 是控制流；VRAM access 和 doorbell write 走不同路径。

### 三层边界

- `[REAL AMD]` Linux 枚举 PCI display function、分配 BAR、绑定 amdgpu，并在初始化
  期间通过 BAR5 读写寄存器。
- `[GEM5]` `AMDGPUDevice` 实现建模寄存器并分派 MMIO、doorbell 和 framebuffer
  access。
- `[COSIM]` `MI300XVfioUser` 为 QEMU 原生 `vfio-user-pci` client 合成 PCI config
  space 和 BAR region；实测 BAR layout 属于本 endpoint/configuration。

### 数据流

```text
Guest PCI enumeration
  -> QEMU vfio-user-pci
  -> MI300XVfioUser config/BAR callback
  -> AMDGPUDevice MMIO、doorbell 或 VRAM 路径
  -> 建模 GPU block
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c`：
  `amdgpu_pci_probe`；`drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`：
  `amdgpu_device_init`。按 `configs/cosim/guest.lock` 中
  `AMDGPU_DKMS_VERSION` 核对源码版本。
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_device.cc`：
  `AMDGPUDevice::readMMIO`、`writeMMIO`、`writeDoorbell`、`writeFrame`。
- `[COSIM]` `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`：
  `MI300XVfioUser::initVfuContext`、`setupBars`、`handleMmioAccess`、
  `handleDoorbellAccess`。

### 运行方法

```bash
LAB_RUN_ID="lab01-pci-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim,AMDGPUDevice vector_add
```

用 fresh runner 结果检查 regression，再与保留的 `phase3-driver-002` probe 对比
device startup。

### Debug 方法

- `MI300XCosim` 显示 vfio-user connection、BAR callback 和 transport event。
- `AMDGPUDevice` 显示建模的 MMIO 与 doorbell routing。
- 从第一个缺失边界开始查：PCI device、预期 BAR、vfio client connection、对应的
  `AMDGPUDevice` access。较晚出现的 QEMU socket error 可能只是 gem5 先失败的
  次生现象。

### 正常现象

Phase 3 实测 BAR0 `[size=16G]`、BAR2 `[size=2M]`、BAR4 `[size=8K]`、BAR5
`[size=512K]`，MSI-X enabled 且有 256 vectors，并且 amdgpu 已绑定。fresh operator
run 仍须只有一个 `[PASS] vector_add`，且 verdict 为 PASS。

### 可修改实验点

- 在 disposable run 中修改 `--vram-size`，观察 BAR0 aperture；对比 baseline 前恢复
  16 GiB。
- 只对一个已知 BAR5 register 或一个 doorbell offset 添加被动 tracing；不要从 Guest
  随机写 MMIO。
- 对比 MMIO、doorbell 和 VRAM access，证明三者并非同一 transport path。

### 验收 artifact

必须包含 `preflight.json`、`verdict.json`、`matrix.tsv`、`gem5.log`、`qemu.log`、
`patch/source-snapshot.txt`、`patch/binary-provenance.txt` 和
`cleanup-status.txt`。参考 BAR 证据为
`phase3-driver-002/guest-probe-output.txt:20-23,39-41`，其权威结果是
`phase3-driver-002/phase3-verdict.json`。

### 恢复方法

runner 正常会做 scoped cleanup。若运行中断且 manifest 仍存在，先看 dry run，再确认
同一 scope：

```bash
./scripts/cosim_cleanup.sh --run-id "$LAB_RUN_ID" \
    --manifest "/tmp/cosim-${LAB_RUN_ID}.session/resources.manifest"
./scripts/cosim_cleanup.sh --run-id "$LAB_RUN_ID" \
    --manifest "/tmp/cosim-${LAB_RUN_ID}.session/resources.manifest" --confirm
```

不要手工删除通用 socket、container 或 `/dev/shm` 名称。

## Lab 2：amdgpu / KFD 初始化

### 原理

amdgpu 初始化 DRM 和 GPU IP block，KFD 向 ROCm 暴露计算队列和内存管理。只看到
PCI function 不算成功；可用 baseline 还要求 amdgpu binding、`/dev/kfd`、DRM
render node 和 `gfx942` HSA agent。

### 三层边界

- `[REAL AMD]` Guest 运行固定版本的 AMD DKMS driver 与 ROCm userspace。probe、
  IP-block bring-up、DRM、KFD 和 ROCm enumeration 都是真实软件路径。
- `[GEM5]` 模型提供这些路径需要的 ROM/register response 和 GMC、IH、GFX、SDMA
  行为。
- `[COSIM]` boot service 注入 ROM、链接 discovery firmware，并在 module load 时
  禁用不支持的 block。物理 MI300X 通常具有这里省略的 PSP、SMU、电源、固件和
  RAS 行为。

### 数据流

```text
PCI probe -> ROM 与 IP discovery -> amdgpu_device_init
  -> enabled IP blocks -> DRM nodes -> KFD node/topology
  -> ROCr enumeration -> gfx942 HSA agent
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_drv.c`：
  `amdgpu_pci_probe`；`drivers/gpu/drm/amd/amdgpu/amdgpu_kms.c`：
  `amdgpu_driver_load_kms`；`drivers/gpu/drm/amd/amdgpu/amdgpu_device.c`：
  `amdgpu_device_init`、`amdgpu_device_ip_early_init`、
  `amdgpu_device_ip_init`、`amdgpu_device_ip_hw_init`；
  `drivers/gpu/drm/amd/amdkfd/kfd_device.c`：`kgd2kfd_probe`、
  `kgd2kfd_device_init`。
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_device.cc`：
  `AMDGPUDevice::readROM`、`readMMIO`、`writeMMIO`。
- `[COSIM]`
  `gem5-resources/src/x86-ubuntu-gpu-ml/files/cosim-gpu-setup.sh`：ROM、
  discovery、module parameter 与 node-count policy。

### 运行方法

```bash
LAB_RUN_ID="lab02-driver-init-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug AMDGPUDevice,PM4PacketProcessor,SDMAEngine vector_add
```

该 operator run 故意超过“module loaded”的标准：初始化后的 driver/ROCm 必须真正
执行并校验 HIP kernel。

### Debug 方法

- `AMDGPUDevice` 定位 driver stall 前最后一次 modeled register access。
- `PM4PacketProcessor` 和 `SDMAEngine` 区分 GFX/KIQ 与 SDMA ring-test 进度，避免
  把一切归类为 module-load timeout。
- 在 `qemu.log` 中标出第一个失败边界：ROM/discovery、IP block、DRM、KFD、ROCm
  agent、compile 或 operator。`hw_init` 部分失败后不要 unload amdgpu；cleanup 后用
  fresh session 重来。

### 正常现象

Phase 3 记录 amdgpu 6.14.14 绑定到 `1002:74a0`，存在 `/dev/kfd`、render nodes、
`gfx942`，可用 VRAM 16383 MiB、GART aperture 512 MiB、KFD device creation，最终
出现 `[COSIM_PHASE3_VERDICT] PASS`。driver discovery 报 320 active CUs，而实测
gem5 只实例化 40 CUs。

### 可修改实验点

- 跟踪每个 enabled IP block 的耗时和第一次 register access。
- 在 disposable Guest image 中一次只改一个 module parameter，记录第一个失败的
  IP block；不要把 PSP/SMU-disabled 的结果等同于物理硬件。
- 对比 driver-visible topology 与 `--num-cus` model parameter。

### 验收 artifact

除标准 runner artifact 外，必须有明确的 driver/ROCm probe。参考
`phase3-driver-002/guest-probe-output.txt:61-97`（binding/node）、`:407-443`
（IP/VRAM/GART）、`:453-463`（KFD/topology）和 `:562`（stage PASS）。Phase 3
使用 `phase3-verdict.json`，不要用通用 operator auditor 结论。

### 恢复方法

由 runner 清理 run-scoped resource。任何部分 driver initialization 后都丢弃本次
Guest session 并重新启动，不用 `rmmod amdgpu` 尝试恢复；自动清理中断时使用精确
run manifest 调用 `cosim_cleanup.sh`。

## Lab 3：VRAM / GTT / GART / GPUVM

### 原理

VRAM 与 GTT 是 allocation domain，GART 和 per-process GPUVM page table 是地址
转换结构。VMID 0 使用 kernel mapping，用户队列使用 VMID/PASID context 与多级
page walk。分配成功但 translation 或 visibility 错误，仍然不通过。

### 三层边界

- `[REAL AMD]` amdgpu/KFD 分配 BO、建立 mapping、更新 page table 并 invalidate
  translation state。
- `[GEM5]` `AMDGPUVM`、Vega walker/TLB、memory manager 与 GPU memory system
  执行建模的 translation/access。
- `[COSIM]` Guest RAM 和 VRAM 是独立 shared mapping；QEMU write 可能绕过 gem5
  cache，因此 fallback PTE read 和显式 invalidation 是兼容机制，不是硬件一致性。

### 数据流

```text
HIP allocation -> KFD/amdgpu BO 与 GPUVA mapping
  -> VMID page table 或 VMID-0 GART translation
  -> Guest RAM (GTT) 或 shared VRAM
  -> CP/SDMA/CU access -> completion 与 result copy
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_gart.c`：
  `amdgpu_gart_init`、`amdgpu_gart_bind`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_vm.c`：`amdgpu_vm_init`、
  `amdgpu_vm_bo_map`、`amdgpu_vm_update_range`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_amdkfd_gpuvm.c`：
  `amdgpu_amdkfd_gpuvm_alloc_memory_of_gpu`。
- `[GEM5]` `gem5/src/dev/amdgpu/amdgpu_vm.cc`：
  `AMDGPUVM::writeMMIOGfx940`、`invalidateTLBs`、
  `GARTTranslationGen::translate`、`MMHUBTranslationGen::translate`、
  `UserTranslationGen::translate`；`amdgpu_device.cc`：
  `AMDGPUDevice::writeFrame`。
- `[COSIM]` `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`：
  `MI300XVfioUser::setupSharedMemory` 与 DMA mapping callback。

### 运行方法

```bash
LAB_RUN_ID="lab03-gpuvm-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug AMDGPUMem,AMDGPUDevice,GPUTLB,GPUCommandProc vector_add
```

### Debug 方法

- `AMDGPUMem` 跟踪 modeled GPU-memory request。
- `AMDGPUDevice` 显示 GART setup、aperture 和 translation warning。
- `GPUTLB` 跟踪 user translation/invalidation，`GPUCommandProc` 把 kernarg、code、
  data address 关联到具体 dispatch。
- 按 VMID、原 GPUVA、PTE、translated paddr、memory domain 和最后一次 invalidation
  建表。即使进程没有退出，`paddr=0` fallback 也必须标成 correctness warning。

### 正常现象

参考 probe 显示 BAR-visible 16 GiB VRAM、可用 VRAM 16383 MiB、约 3970 MiB GTT，
以及 PTB 位于 VRAM 的 512 MiB enabled GART。fresh test 必须完成 H2D、Task 2
dispatch、D2H 和精确 vector comparison，不能有 translation fault 或 silent wrong
result。

### 可修改实验点

- 修改 `vector_add` length，使 buffer 先跨一个、再跨多个 4 KiB page。
- 对比 fresh single run 与 `--repeat` run，暴露 stale TLB/PWC state。
- 只在 disposable run 改 `--vram-size`，记录 aperture、page-table placement 和失败点。
- 对单一 GPUVA 追踪 GART/User translation；不得把 physical-zero fallback 当作修复。

### 验收 artifact

保留标准 runner artifact，并在 `gem5.log` 中保留 GPUVA/PTE/VMID 证据。参考内存
事实位于 `phase3-driver-002/guest-probe-output.txt:98-105,433-444`。通过条件是
PASS verdict 和正确数据，不是“成功分配”或“没有 crash”。

### 恢复方法

translation/coherence 失败后必须 fresh session；残留页表和 cache 会让原地重试的
结论不明确。先保存失败 artifact，再做 manifest-scoped cleanup。

## Lab 4：Ring / Queue / Doorbell

### 原理

kernel PM4 queue 建立 process/queue state，用户 HSA queue 保存 AQL packet。
doorbell 只通知新的 write position，不携带 command 本体。queue identity、doorbell
offset、VMID/PASID 和 pointer movement 必须一致。

### 三层边界

- `[REAL AMD]` amdgpu 管理硬件 ring，KFD 为 ROCr 建立 process queue 并映射
  doorbell。
- `[GEM5]` PM4 queue handling 建立 descriptor；`HWScheduler` 与
  `HSAPacketProcessor` 获取并调度 AQL packet。
- `[COSIM]` BAR2 doorbell write 穿过 vfio-user callback；routing 由 gem5 建模，
  不是物理 doorbell hardware。

### 数据流

```text
KFD MAP_PROCESS / MAP_QUEUES -> VMID、PASID、MQD、doorbell mapping
ROCr 写 AQL packet -> 更新 queue write pointer -> 敲 BAR2 doorbell
MI300XVfioUser -> AMDGPUDevice -> HWScheduler -> HSAPacketProcessor fetch
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_ring.c`：
  `amdgpu_ring_init`、`amdgpu_ring_alloc`、`amdgpu_ring_commit`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_doorbell.c`；
  `drivers/gpu/drm/amd/amdkfd/kfd_chardev.c`：`kfd_ioctl_create_queue`；
  `drivers/gpu/drm/amd/amdkfd/kfd_process_queue_manager.c`：
  `pqm_create_queue`。
- `[GEM5]` `gem5/src/dev/amdgpu/pm4_packet_processor.cc`：
  `PM4PacketProcessor::mapProcess`、`mapQueues`、`processMQD`；
  `gem5/src/dev/hsa/hw_scheduler.cc`：`HWScheduler::registerNewQueue`、`write`；
  `hsa_packet_processor.cc`：`setDeviceQueueDesc`。
- `[COSIM]` `MI300XVfioUser::handleDoorbellAccess` 转发到
  `AMDGPUDevice::writeDoorbell`，后者用 `mapDoorbellToVMID` 与 queue type routing。

### 运行方法

```bash
LAB_RUN_ID="lab04-queues-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug MI300XCosim,AMDGPUDevice,PM4PacketProcessor,HSAPacketProcessor vector_add
```

### Debug 方法

- `MI300XCosim` 与 `AMDGPUDevice` 标明每次 BAR2 write 和选中的 queue。
- `PM4PacketProcessor` 显示 MAP_PROCESS/MAP_QUEUES 与 MQD handling。
- `HSAPacketProcessor` 显示 AQL read/write/dispatch index 和 packet completion。
- 按 queue/doorbell 建表，包含 VMID、PASID、rptr、wptr、dispatch pointer 和最终 empty
  state；doorbell 本身不能证明有执行进度。

### 正常现象

driver 初始化 KIQ、compute 和 SDMA ring。vector addition 的 AQL queue 前进到 Task 2，
17 个 workgroup（WG 0–16）全部 fetch/dispatch，rptr 追上 wptr，测试只产生一个
PASS marker。

### 可修改实验点

- 修改 grid size，生成少于、等于和多于 40 个 workgroup。
- 用 `--repeat` 检查每个 fresh session 的 queue ID/pointer 都是新状态。
- 添加被动 queue/doorbell correlation 字段，避免没有 queue identity 的全局日志。

### 验收 artifact

除标准 runner evidence 外，还要从原始 `gem5.log` 生成 queue table。baseline AQL
packet 位于 `phase4-baseline-vector-add-i0/gem5.log:1450`，first WG 位于 `:1478`，
last WG 位于 `:1718`，queue completion 位于 `:3395-3403`。

### 恢复方法

stuck queue 不可安全复用。保存 pointer state 和第一个失败 packet 后结束 session，
由 runner 或精确 manifest cleanup 只清理本 run resource。

## Lab 5：PM4

### 原理

这里的 PM4 是内核用于 process/queue setup、indirect buffer、write、wait 和 completion
的命令语言。PM4 建立队列后，HIP kernel 本身通过 AQL dispatch packet 到达；把每个
HIP dispatch 都描述成 PM4 dispatch packet 是不准确的。

### 三层边界

- `[REAL AMD]` KFD 与 ASIC ring code 按真实 cache、ordering、queue 语义构造 PM4
  packet。
- `[GEM5]` `PM4PacketProcessor` 解码并执行受支持的子集。
- `[COSIM]` 部分 packet 被跳过或近似实现以推动 Guest driver；这些是模型限制，
  不是合法的 AMD 硬件替代语义。

### 数据流

```text
KFD packet manager -> PM4 ring -> doorbell
  -> PM4PacketProcessor::process/decodeHeader
  -> MAP_PROCESS / MAP_QUEUES / RUN_LIST / IB / WRITE_DATA / RELEASE_MEM
  -> queue state、memory write、wait 或 completion
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdkfd/kfd_packet_manager.c`：
  `pm_send_runlist`、`pm_send_set_resources`；canonical PM4 layout 位于
  `drivers/gpu/drm/amd/amdkfd/kfd_pm4_headers.h` 与
  `drivers/gpu/drm/amd/amdkfd/kfd_pm4_opcodes.h`；ASIC
  ring emission 位于 `drivers/gpu/drm/amd/amdgpu/gfx_v9_4_3.c`。
- `[GEM5]` `gem5/src/dev/amdgpu/pm4_defines.hh`；
  `pm4_packet_processor.cc`：`process`、`decodeHeader`、`mapProcess`、`mapQueues`、
  `runList`、`indirectBuffer`、`writeData`、`waitRegMem`、`releaseMem`。
- `[COSIM]` 当前 `IT_ACQUIRE_MEM` 与 `IT_SET_RESOURCES` 只推进 rptr，没有完整
  hardware semantics；unsupported opcode 会 warning 后跳过。

### 运行方法

```bash
LAB_RUN_ID="lab05-pm4-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug PM4PacketProcessor,AMDGPUDevice vector_add
```

### Debug 方法

- `PM4PacketProcessor` 是 packet decoder 的权威 debug flag。
- `AMDGPUDevice` 补充 doorbell、VMID/PASID 和 destination routing context。
- 按 queue/opcode 计数，找第一个缺少 input、rptr advance、memory effect 或 completion
  的 packet。保留 unknown opcode，不得把它归为 NOP。

### 正常现象

MAP_PROCESS 建立 process translation context，MAP_QUEUES 消费 MQD，管理序列最终
形成可用 AQL queue。受支持 packet 正常推进 rptr 而不 panic。PASS 并不能证明
ACQUIRE_MEM 或 cache flush 与硬件完全等价。

### 可修改实验点

- 为 driver initialization 和一次 HIP run 生成 opcode/count timeline。
- 对比 WRITE_DATA 与 RELEASE_MEM 在 system memory/VRAM 的 destination。
- 在独立 gem5 branch 中实现或 instrument 一项缺失 cache-maintenance 语义，只通过
  `cosim_build.sh` rebuild，并复跑相同 artifact matrix；不得静默把 unknown opcode
  变成 success。

### 验收 artifact

除 runner evidence 外，生成包含 source log line、queue、opcode、input address、output
effect 和 status 的 packet table。通过要求 operator PASS，且 causal path 上没有未解释
unsupported packet；即使 PASS 也要记录 partial semantics。

### 恢复方法

cleanup 前保存第一个 unsupported packet 及其周边原始日志。只用
`./scripts/cosim_build.sh gem5` rebuild，fresh runner session 重试，放弃的 run 做
scoped cleanup。

## Lab 6：SDMA

### 原理

SDMA 通过独立 queue 执行异步 copy 与 memory operation。packet 仍依赖正确的 VM
translation、memory-domain routing、rptr writeback、fence 以及可选 trap。

### 三层边界

- `[REAL AMD]` driver 初始化 SDMA ring 并发出 ASIC packet，ROCm 可用它进行 copy
  与 queue operation。
- `[GEM5]` `SDMAEngine` 解码并执行受支持的 packet 子集。
- `[COSIM]` 模型使用 1000-tick SDMA delay，使 ring test 在 Guest timeout 内完成，
  并通过 cosim path 访问 shared memory/VRAM；这不是物理 SDMA timing。

### 数据流

```text
hipMemcpy 或 driver ring test -> SDMA packet ring -> SDMA doorbell
  -> decode -> VM/GART translation -> copy/write
  -> rptr 与 fence -> optional TRAP/IH -> waiter
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/sdma_v4_4_2.c`：
  `sdma_v4_4_2_ring_emit_ib`、`sdma_v4_4_2_ring_emit_fence`、
  `sdma_v4_4_2_ring_test_ring`。
- `[GEM5]` `gem5/src/dev/amdgpu/sdma_engine.cc`：
  `SDMAEngine::decodeNext`、`decodeHeader`、`translate`、`copy`、`fence`、`trap`；
  `sdma_engine.hh` 定义 cosim `sdma_delay`。
- `[COSIM]` SDMA 针对 shared Guest RAM 翻译 Guest address，并把 device address
  routing 到 modeled/shared VRAM；部分 GART shadow update 用于补偿绕过 gem5 的
  BAR0 write。

### 运行方法

```bash
LAB_RUN_ID="lab06-sdma-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug SDMAEngine,SDMAData,AMDGPUMem vector_add
```

### Debug 方法

- `SDMAEngine` 显示 queue state、opcode decode、fence 和 trap。
- `SDMAData` 显示 packet data movement；日志量大，只用于 bounded run。
- `AMDGPUMem` 显示最终 modeled memory request。关联 source/destination、VMID、
  translated address、bytes、rptr、fence value 和 data checksum。

### 正常现象

Phase 3 记录 SDMA ring 已初始化，vector test 的 H2D/D2H copy 返回正确结果。required
path 上没有 SDMA ring-test `-110` 或 unsupported packet，rptr 前进，completion fence
可观察。

### 可修改实验点

- 修改 vector length，覆盖 small、unaligned 与 multi-page copy。
- 添加分别校验 H2D、D2H、D2D 的仓库 HIP test，并且只用
  `run_cosim_tests.sh` 运行。
- 在独立 branch 比较 1000-tick delay 与更大值，分类 timeout，但不声称有性能等价性。

### 验收 artifact

除标准 artifact 外，保留包含 queue、opcode、source、destination、bytes、translation、
fence 和 result checksum 的 bounded SDMA table。参考 ring creation 位于
`phase3-driver-002/guest-probe-output.txt:536-551`。正确 copy data 和 PASS verdict
都是强制条件。

### 恢复方法

SDMA stall 时保存 queue pointer、last decoded packet 和 pending DMA callback state。
结束 session 后 fresh rerun，不复用部分推进的 ring；只通过 runner 或精确 manifest
清理。

## Lab 7：Fence / IH / MSI-X

### 原理

memory completion value 与 interrupt 是两件事。polling 等待 signal value 改变；
interrupt mode 还要写 IH entry 与 wptr、raise MSI-X，并由 KFD 唤醒所属 process。
两条路径必须独立验证。

### 三层边界

- `[REAL AMD]` amdgpu fence、IH、IRQ dispatch 与 KFD event 构成真实软件完成路径。
- `[GEM5]` command/SDMA completion 更新 HSA signal，建模 IH 生成 cookie 与 ring
  entry。
- `[COSIM]` vfio-user 通过 eventfd-backed MSI-X vector 送入 QEMU/KVM；模型会 clamp
  部分 VMID，且只实现 CP_EOP/TRAP IH source。

### 数据流

```text
Polling (HSA=0): GPU completion -> signal 1->0 -> host 观察到新值

Interrupt (HSA=1): GPU completion -> signal 1->0 -> IH cookie
  -> IH ring write -> IH wptr update -> vfio-user MSI-X vector 0
  -> Guest amdgpu/KFD waiter
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdgpu/amdgpu_fence.c`：
  `amdgpu_fence_process`、`amdgpu_fence_driver_init_ring`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_ih.c`：`amdgpu_ih_process`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_irq.c`：`amdgpu_irq_dispatch`；
  `drivers/gpu/drm/amd/amdkfd/kfd_events.c`：`kfd_signal_event_interrupt`。
- `[GEM5]` `gem5/src/gpu-compute/gpu_command_processor.cc`：
  `GPUCommandProcessor::updateHsaSignalData`、`sendCompletionSignal`；
  `gem5/src/dev/amdgpu/interrupt_handler.cc`：`prepareInterruptCookie`、
  `AMDGPUInterruptHandler::prepareInterruptCookie`、`submitInterruptCookie`、
  `submitWritePointer`、`intrPost`。
- `[COSIM]` `MI300XVfioUser::sendIrqRaise` 在 `AMDGPUDevice::intrPost` 后通过
  vfio-user 转发 vector。

### 运行方法

polling 与 interrupt 必须分别使用 fresh session：

```bash
POLL_RUN_ID="lab07-poll-$(date +%Y%m%d-%H%M%S)"
COSIM_RUN_ID="$POLL_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${POLL_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add

IRQ_RUN_ID="lab07-irq-$(date +%Y%m%d-%H%M%S)"
COSIM_RUN_ID="$IRQ_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${IRQ_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,AMDGPUDevice,MI300XCosim vector_add
```

host 或 build state 有变化时，在这对运行前先执行 `cosim_preflight.sh run`。

### Debug 方法

- polling flags：`HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo`。
- interrupt flags：
  `HSAPacketProcessor,GPUCommandProc,GPUDisp,AMDGPUDevice,MI300XCosim`。
- interrupt mode 必须在同一 signal/tick 找到有序链：signal transition、IH ring state、
  cookie write、wptr update、IRQ raise。记录 VMID/PASID；仅看到 vector 不能证明送到
  正确 process。

### 正常现象

polling mode 记录 `HSA_ENABLE_INTERRUPT=0`，完成 vector addition，wait 不依赖
MSI-X。interrupt mode 记录 `HSA_ENABLE_INTERRUPT=1`，并在实测 tick
`783006015986249` 依次看到 signal `1 -> 0`、IH cookie、IH wptr update、vfio-user
IRQ vector 0。两个 run 都只有一个 PASS marker 且 cleanup verified。

### 可修改实验点

- 每种模式都用 fresh session 重复，比较 completion/interrupt count。
- 在 `hipFree` 前加入第二个 GPU operation，压力测试 VMID/PASID routing。
- 对比 CP_EOP 与 SDMA TRAP source，保持各自 cookie/queue identity 独立。

### 验收 artifact

两个 run 都要求 `matrix.tsv` 记录生效 HSA value，且 `verdict.json` 为 PASS。参考
interrupt chain 位于
`phase4-interrupt-vector-add-i1/gem5.log:229167-229179`，摘要为
`interrupt-verdict.json`；polling mode 与 PASS marker 位于
`phase4-baseline-vector-add-i0/qemu.log:941,959`。

### 恢复方法

interrupt timeout 后保存 signal、IH、VMID/PASID 和 vfio evidence，再 fresh session。
不要在同一 live Guest 内切换 HSA mode 后比较结果；手工清理只能使用 run-specific
manifest。

## Lab 8：HIP 端到端 dispatch 与 gem5 debug

### 原理

最终实验验证完整功能链，而不是单一 subsystem。HIP/ROCr 分配内存并创建 AQL
dispatch；KFD/amdgpu 建立 process/queue；gem5 获取 packet、启动 workgroup、执行
kernel、更新 completion，最后把数据送回 Guest。

### 三层边界

- `[REAL AMD]` Guest 中的 HIP、ROCr、KFD、amdgpu 与 ABI 是 pinned image 的真实
  软件路径，但操作对象是 modeled device。
- `[GEM5]` HSA packet processing、command processor、dispatcher、shader、CU、
  instruction pipeline、TLB 与 memory system 对 GPU execution 建模。
- `[COSIM]` QEMU 执行 CPU/driver，vfio-user 携带 device operation，共享内存连接
  Guest RAM/VRAM。通过本实验只验证该配置，不代表所有物理 MI300X 行为。

### 数据流

```text
HIP API -> ROCr -> KFD ioctl 与 mapping -> PM4 process/queue setup
  -> user queue 中 AQL packet -> AQL doorbell -> HWScheduler
  -> HSAPacketProcessor -> GPUCommandProcessor -> GPUDispatcher
  -> Shader -> ComputeUnit execution -> HSA completion
  -> polling 或 IH/MSI-X -> hipDeviceSynchronize -> D2H -> result check
```

### 对应源码与关键函数

- `[REAL AMD]` `drivers/gpu/drm/amd/amdkfd/kfd_chardev.c`：
  `kfd_ioctl_create_queue`；
  `drivers/gpu/drm/amd/amdkfd/kfd_process_queue_manager.c`：
  `pqm_create_queue`；
  `drivers/gpu/drm/amd/amdgpu/amdgpu_amdkfd_gpuvm.c`：
  `amdgpu_amdkfd_gpuvm_alloc_memory_of_gpu`。AQL ABI/ROCr 源码必须与
  `configs/cosim/guest.lock` 固定的 ROCm 版本匹配。
- `[GEM5]` `gem5/src/dev/hsa/hsa_packet_processor.cc`：
  `HSAPacketProcessor::getCommandsFromHost`、`processPkt`、`finishPkt`；
  `gem5/src/gpu-compute/gpu_command_processor.cc`：`submitDispatchPkt`、
  `dispatchKernelObject`、`dispatchPkt`、`sendCompletionSignal`；
  `dispatcher.cc`：`GPUDispatcher::dispatch`、`notifyWgCompl`；`shader.cc`：
  `Shader::dispatchWorkgroups`；`compute_unit.cc`：`ComputeUnit::dispWorkgroup`。
- `[COSIM]` `scripts/run_cosim_tests.sh` 固定 program identity、保存生效 HSA mode、
  启动 fresh session、分类结果，并归档源码/二进制 provenance 与原始日志。

### 运行方法

先复现已测 polling trace：

```bash
LAB_RUN_ID="lab08-dispatch-$(date +%Y%m%d-%H%M%S)"
./scripts/cosim_preflight.sh run \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}-preflight"
COSIM_RUN_ID="$LAB_RUN_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${LAB_RUN_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add
```

单次 PASS 后运行 fresh-session repetition matrix：

```bash
REPEAT_ID="lab08-repeat-$(date +%Y%m%d-%H%M%S)"
COSIM_RUN_ID="$REPEAT_ID" GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh --repeat 3 \
    --output-dir "artifacts/amd-gpu-learning-env/labs/${REPEAT_ID}" \
    --gem5-debug HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo vector_add
```

### Debug 方法

- 从实测 flags 开始：`HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo`。
- 只按证据增加一个维度：`GPUFetch`、`GPUExec`、`GPUSched`、`GPUTLB`、
  `AMDGPUMem`、`PM4PacketProcessor` 或 `SDMAEngine`。
- 分类第一个缺失 transition：AQL fetch、kernel-object/ABI decode、workgroup dispatch、
  execution progress、completion signal、interrupt/wait 或 D2H validation。读大段日志
  前先构造 compact dispatch、queue、signal、translation、cache table。
- timeout 不是 root cause。确认 progress 是否仍变化、所有 workgroup 是否完成，以及
  trace 是否覆盖最终 signal/wait object。

### 正常现象

实测 polling run 找到唯一 Task 2 AQL packet，workgroup size 256、grid size 4352，
正好 dispatch 17 个 workgroup（WG 0–16），记录 HSA completion 和
`Completed kernel 2`，最后返回正确 vector result 和一个 `[PASS] vector_add`。launch
前观察到两次 transient invalidate retry 且均恢复；这不能成为忽略持续
cache/coherence failure 的理由。

### 可修改实验点

- 修改 vector length 与 threads per block；运行前先预测 grid/workgroup count，再与
  trace 对比。
- 同一 binary 用 fresh session 重复，再加入 multi-operation test 暴露 stale
  PWC/TLB/SQC/GL2 state。
- 不改变 program identity，按 Lab 7 对比 polling 与 interrupt completion。
- 一次只增加一个窄范围 debug flag，并报告 object/filter coverage，使“未出现事件”
  仍有明确含义。

### 验收 artifact

每一行都要求 PASS `verdict.json`、匹配的 `matrix.tsv`、精确 program source/binary
hash、source snapshot、binary provenance、完整 gem5/QEMU log 和 verified cleanup。
参考 dispatch 摘要为 `phase4-baseline-vector-add-i0/dispatch-verdict.json`；原始锚点为
`gem5.log:1450`（Task/AQL）、`:1478-1718`（WG 0–16）、`:3395-3403`
（completion）。artifact 仅留本地；若 provenance 不完整，它本身不能作为证明。

### 恢复方法

cleanup 前保留完整失败 row。通过仓库 debug workflow 路由第一个失败组件，只做一个
bounded change，仅用 `cosim_build.sh` rebuild，并在 fresh runner session 中按相同
标准重试。匹配 provenance 的 PASS matrix 才能停止；不得覆盖失败 artifact directory。
