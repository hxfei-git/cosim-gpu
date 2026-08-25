# gem5 MI300X 模型参考

当 `cosim-gpu-debug` 证据指向 gem5 GPU 行为时使用本文，包括 PM4、SDMA、
interrupt、HSA queue、VMID/PASID、translation、TLB/PWC 或 cache maintenance。

## 架构

```
Guest ROCm driver
  <-> QEMU vfio-user-pci
  <-> gem5 MI300X GPU 模型
      AMDGPUDevice
      PM4PacketProcessor
      SDMAEngine
      HSAPacketProcessor
      GPUDynInst
      Ruby memory system
```

## 关键源码

| 区域 | 文件 | 用途 |
|---|---|---|
| Device | `gem5/src/dev/amdgpu/amdgpu_device.cc/hh` | BAR、VMID 分配、PASID 映射、interrupt |
| VM | `gem5/src/dev/amdgpu/amdgpu_vm.cc/hh` | GPU page table、GART、translation、TLB/PWC invalidation |
| PM4 | `gem5/src/dev/amdgpu/pm4_packet_processor.cc/hh` | `MAP_PROCESS`、`RUN_LIST`、`INDIRECT_BUFFER`、`RELEASE_MEM`、`WRITE_DATA` |
| Interrupt | `gem5/src/dev/amdgpu/interrupt_handler.cc/hh` | CP_EOP 与 TRAP interrupt cookie |
| SDMA | `gem5/src/dev/amdgpu/sdma_engine.cc/hh` | copy operation |
| HSA | `gem5/src/dev/hsa/hsa_packet_processor.cc/hh` | AQL dispatch、barrier、signal |
| Scheduler | `gem5/src/dev/hsa/hw_scheduler.cc/hh` | queue 管理与 `RUN_LIST` |
| Compute | `gem5/src/gpu-compute/gpu_command_processor.cc/hh` | kernel launch 与 kernarg 处理 |
| TLB | `gem5/src/arch/amdgpu/vega/tlb.cc` | TLB 与 page-walk-cache 行为 |
| Ruby | `gem5/src/mem/ruby/**` | GPU cache hierarchy 与 maintenance callback |

## PM4 命令流

```
Guest doorbell write
  -> PM4PacketProcessor::process()
  -> MAP_PROCESS 分配 VMID 并绑定 PASID
  -> RUN_LIST 提交 command buffer
  -> INDIRECT_BUFFER 跟随 command buffer chain
  -> WRITE_DATA 写入常量或 signal
  -> RELEASE_MEM 写 completion data，并按 packet 提交 CP_EOP interrupt
```

当前 `RELEASE_MEM` 没有实现完整 cache flush/ordering 语义；`ACQUIRE_MEM` 的 cache
maintenance 也不完整。不要根据 packet 名称推断硬件等价行为，具体缺口与历史证据见
`discovery-log.md` 第 4 项。

## Interrupt 处理

| 来源 | 用途 | VMID 敏感性 |
|---|---|---|
| CP_EOP | command processor completion | 取运行中 queue 的 VMID |
| TRAP | exception 或 page fault | 当前模型中不依赖 VMID |

`interrupt_handler.cc::prepareInterruptCookie()` 从当前 context 设置 PASID 与 VMID。
Guest amdgpu 使用该 cookie 把 interrupt 路由给所属 process。

## VMID 与 PASID

- VMID 选择 GPU page-table context；VMID 0 保留给 kernel 或 driver 路径。
- PASID 标识 process address space。
- `MAP_PROCESS` 把 PASID 绑定到 VMID。
- `pasidFromVMID()` 返回已映射的 PASID，未知时返回 0。
- 当前测试集中在单个 user VMID。

完整语义见 `vmid-pasid-architecture.md`。

## MAP_QUEUES VMID assertion

若启动或 Driver 初始化伴随 QEMU `error_setv`、vfio-user abort、socket close 或
gem5 container 消失，检查：

```bash
grep -n 'assert.*vmid\|assert(queue_vmid)\|MAPQueues' \
  gem5/src/dev/amdgpu/pm4_packet_processor.cc
```

若 `MAP_QUEUES` 附近存在 `assert(queue_vmid)`，阅读 `vmid-assert-lesson.md` 和
`examples/vmid-assert-crash.md`。在这种模式中，gem5 先退出，QEMU 随后报告 socket
failure。

当前 fallback：

```cpp
const uint16_t queue_vmid =
    pkt->vmid ? pkt->vmid : gpuDevice->lastVMID();
```

## Translation 与 cache checkpoint

- TLB 与 PWC invalidation 是不同的模型责任。
- 当前模型的 PWC entry 没有按 VMID 标记。
- CP 写入可能绕过 CU scalar load 使用的 GL2 路径。
- 为保证 kernarg 正确，shader 执行前可能需要 GL2 与 SQC maintenance。

当 log 出现 translation、kernarg、SQC、GL2、PWC 或 TLB 症状时，阅读
`cache-coherence-checkpoints.md` 与 `discovery-log.md`。

## 已知限制

1. 常规测试路径只覆盖单个 user VMID。
2. 通过 Driver 参数禁用了 power management。
3. 不支持 device-side `printf`，使用时可能 hang。
4. Packet processor 可能对部分 HSA packet 执行 fake completion。
5. 从 `fix-hsa-signal-ih-completion-2` cherry-pick 可能引入 MAP_QUEUES VMID
   assertion；必须按当前源码重新验证，不能仅沿用历史结论。
