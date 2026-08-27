---
name: cosim-gpu-debug
description: 定位 cosim 中的 gem5、QEMU 或 Guest crash、hang、timeout、GPUVM fault 与错误结果时使用；只加载当前症状相关的日志和源码。
---

# cosim-gpu 调试

目标是找出三侧中的第一个可靠失败点，而不是从最后一条报错倒推。先固定一个
run artifact，按时间查看 `gem5.log`、`qemu.log` 和其中的 Guest kernel/service
输出：

```bash
ARTIFACT_DIR=artifacts/vector_add/replace-with-run-id
rg -n -i -g '*.log' \
    'fatal|panic|assert|abort|timeout|fault|translation|PTE|GPUVM|VMID|PASID|PM4|SDMA|doorbell|signal|interrupt|Broken pipe|socket|error_setv' \
    "$ARTIFACT_DIR"
```

保留失败前最后一个正常状态和第一条错误即可；不要先展开整个 artifact 或固定生成
额外汇总表。

## 判断首个失败组件

- gem5 先出现 `fatal`、`panic`、assertion、translation fault 或容器退出：
  先查 gem5。随后出现的 QEMU vfio-user socket EOF、broken pipe、`error_setv`
  或 abort 通常是 gem5 endpoint 消失后的次生错误。
- gem5 仍运行且 vfio-user/MMIO 请求正常，但 QEMU 自己先 abort 或 KVM 退出：
  查 QEMU 命令行、trace 和同一时刻的 socket 请求。
- 两侧仍运行，而 `cosim-gpu-setup.service`、amdgpu/KFD、`rocminfo` 或 HIP
  首先失败：查 Guest 的 service log 与 `dmesg`；部分初始化的 Guest 不原地重载
  driver。
- timeout 时先判断进度是否仍变化。仍有 dispatch/workgroup/completion 表示规模或
  timeout budget 问题；queue、signal 和 CU 状态都不再变化才继续查等待链。

## 关键状态链

沿当前症状所在的链路向前找第一个缺口：

1. PCI/BAR/MMIO：vfio-user callback 是否到达 `AMDGPUDevice::writeMMIO`。
2. PM4/process：`MAP_PROCESS` 是否建立 PASID→VMID/page-table base，
   `MAP_QUEUES` 是否注册 MQD、queue 和 doorbell。
3. AQL/doorbell：BAR2 write 是否路由到正确 queue，read/write/dispatch pointer
   是否推进，packet header 与 kernel object 是否有效。
4. 执行：command processor 是否提交 packet，dispatcher/CU 是否开始并完成
   workgroup。
5. 地址转换：区分 VMID0 GART 与 user GPUVM；记录 faulting GPU VA、VMID/PASID、
   page-table base、PTE、system bit，以及失败发生在 TLB/PWC 前后。
6. 完成：completion signal 是否递减；interrupt 模式下再检查 CP_EOP cookie、IH
   ring entry/write pointer、MSI-X raise 和 Guest KFD 唤醒。signal 更新与 IRQ
   唤醒是两个检查点。

MMIO、doorbell、queue、signal、VMID/PASID、GPU VA/PTE 和 interrupt cookie 都应
使用同一次 run 中的实际身份，不能用相邻 run 的数值拼接。

## gem5 源码导航

| 主题 | 当前源码与关键入口 |
| --- | --- |
| vfio-user、BAR、IRQ | `gem5/src/dev/amdgpu/mi300x_vfio_user.cc`：`handleMmioAccess`、`handleDoorbellAccess`、`sendIrqRaise` |
| 设备路由、VMID | `gem5/src/dev/amdgpu/amdgpu_device.cc`：`writeMMIO`、`writeDoorbell`、`allocateVMID`、`mapDoorbellToVMID`、`intrPost` |
| PM4/process/queue | `gem5/src/dev/amdgpu/pm4_packet_processor.cc`：`process`、`mapProcess`、`mapQueues` |
| AQL queue | `gem5/src/dev/hsa/hw_scheduler.cc` 与 `hsa_packet_processor.cc`：`HWScheduler::write`、`getCommandsFromHost`、`processPkt`、`finishPkt` |
| Dispatch/CU | `gem5/src/gpu-compute/gpu_command_processor.cc` 与 `dispatcher.cc`：`submitDispatchPkt`、`dispatchPkt`、`GPUDispatcher::dispatch` |
| GPUVM/PTE | `gem5/src/dev/amdgpu/amdgpu_vm.cc`、`gem5/src/arch/amdgpu/vega/pagetable_walker.cc`、`tlb.cc` |
| SDMA | `gem5/src/dev/amdgpu/sdma_engine.cc` |
| Signal/IH | `gem5/src/gpu-compute/gpu_command_processor.cc` 的 `sendCompletionSignal`、`updateHsaSignalData`，以及 `gem5/src/dev/amdgpu/interrupt_handler.cc` |

## 定向日志

只开启与缺失状态相邻的 flags：

| 现象 | 建议 flags |
| --- | --- |
| vfio-user、BAR、MMIO、IRQ | `MI300XCosim,AMDGPUDevice` |
| PM4、process、MQD | `PM4PacketProcessor,AMDGPUDevice` |
| AQL queue 与 dispatch | `HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo` |
| GPUVM、PTE、TLB | `AMDGPUMem,GPUPTWalker,GPUTLB` |
| SDMA | `SDMAEngine,SDMAData` |

通过 launcher 或 runner 的 `--gem5-debug` 传入这些 flag；只有 socket/protocol
仍无法判断时再加 `--qemu-trace`。历史故障只在当前签名匹配时查
`docs/调试参考.md`，并先核对当前顶层与 gem5 revision；不要把历史单一修复方案
直接套到新问题。
