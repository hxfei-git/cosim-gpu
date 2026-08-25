# Cosim gem5 AMD GPU 模型发现记录

本文记录 2026 年 5 月协同仿真稳定性调试中发现的一致性与正确性问题。每一项说明
发现、证明它的证据和修复位置。这些内容只用于新调试会话的路由提示；每个新 case
仍必须有独立的当前证据。

## 1. 完整 TLB flush 没有使 PWC 失效

**发现**：2026 年 5 月，RLCR 第 4–6 轮。重复执行 `vector_add` 时，TLB entry
跨会话复用。`invalidateAll` 清除了 TLB，却没有清除 page walk cache，因此旧 PTE
片段仍然存在。

**证据**：两次 state snapshot 对比显示，`invalidateAll` 后 TLB 已空，但 PWC 仍保存
旧 page table 对应的 entry；下一次 page walk 未重新遍历便返回了旧 PWC 数据。

**修复**：`gem5/src/arch/amdgpu/vega/tlb.cc` 在每次完整 TLB flush 时保守地使整个
PWC 失效。提交：`arch-amdgpu: invalidate PWC on full TLB flush`。

**验证**：启用 PWC invalidation 后执行 200 次 `vector_add`，`TIMEOUT_WAIT` 为 0；
修复前约有 3% 失败率。

**调试参考**：使用 `cosim-gpu-debug` 的 cache 与 translation 证据，并阅读
`overview.md` 和 `cache-coherence-checkpoints.md`。

## 2. Kernarg L2 可见性缺口

**发现**：2026 年 5 月，RLCR 第 7–8 轮。Command processor（CP）通过绕过 GL2/L2
的路径写入 kernarg，而 compute unit（CU）的 scalar load 经 GL2 读取；旧 L2 cache
line 返回了旧值。

**证据**：插桩的 CP 侧 kernarg dump 为正确值，但 CU scalar-load trace 对同一物理
地址显示不同值。CP 写入是绕过 GPU GL2 路径的 `systemReq`，CU 读取则经过 Ruby
VIPER L2。

**修复**：跟踪 kernarg system memory range，并在 kernel launch 前使这些范围的
GL2 cache line 失效。PR：`zevorn/gem5#1`。

**验证**：使用精确源码的重复运行复现，并比较 invalidate 前后的 CP 侧 kernarg 写入
与 CU scalar-load 证据。保留当前运行 artifact；历史的一次性验证记录未随本仓库
提供。

**调试参考**：使用 `cosim-gpu-debug` 的 cache 证据，并阅读 `overview.md` 的 cache
hierarchy 与 `cache-coherence-checkpoints.md`。

## 3. QEMU↔gem5 memory coherence 缺口

**发现**：2026 年 5 月。重复执行时，ROCm runtime 会分配新虚拟地址、复用 page
table page 并更新 PTE。QEMU 与 gem5 通过 run-scoped
`/dev/shm/cosim-guest-ram-<run-id>` 共享 Guest RAM，但二者之间没有显式 coherence
协议：如果 gem5 在 QEMU 的 PTE 更新传播到共享内存前访问 page，可能读到旧 PTE
或找不到 PTE。

**证据**：多次 `TIMEOUT_WAIT` 都与 gem5 TLB 中的旧 PTE 相关；其值不同于 QEMU
shadow page table 的当前值。

**状态**：上面的 PWC 修复（#1）和 cache coherence 修复（#2、#4）只解决了部分
问题；QEMU 与 gem5 之间的完整 coherence 协议仍未实现。

**调试参考**：使用 `cosim-gpu-debug` 的 translation 与 VMID 证据；unmap trigger
还需阅读 `cosim-gpu-rocm-stack` 的 KFD ioctl 说明。

## 4. ACQUIRE_MEM / RELEASE_MEM 语义不完整

**发现**：2026 年 5 月。gem5 模型能识别 `ACQUIRE_MEM` 与 `RELEASE_MEM` PM4
packet，但没有执行完整的 cache maintenance。base=0、size=0xffffffffffffff00 的
global `ACQUIRE_MEM` 应使 code 与 data cache 失效；模型只处理了 code object，
遗漏 data cache invalidation。

**证据**：ROCm Compute Profiler 文档和 gem5 PM4 packet trace 显示，
`ACQUIRE_MEM` 在 kernel 执行前到达，但 invalidation scope 比硬件行为更窄。

**状态**：2026 年 5 月完成调查；修复范围改为特定 cache layer 的修复（#1、#2），
而不是完整实现 `ACQUIRE_MEM`/`RELEASE_MEM`。

**调试参考**：使用 `cosim-gpu-debug` 的 PM4 packet 对比，并阅读 `overview.md`。

## 5. Kernel launch 时没有使 SQC（scalar L1）失效

**发现**：2026 年 5 月。Kernel launch 时的 `prepareInvalidate` 会使 kernarg 范围
的 GL2 cache line 失效，但不会到达 SQC。即使 GL2 已干净，先前 kernel 执行后命中
SQC 的 scalar load 仍可能返回旧值。

**证据**：插桩显示，GL2 invalidation 完成后 kernarg 地址仍出现 SQC hit。增加 SQC
invalidation 后，残留的错误结果消失。

**修复**：扩展 `prepareInvalidate`，使 kernarg 范围的 SQC 同时失效。

**调试参考**：使用 `cosim-gpu-debug` 的 cache 证据，并阅读 `overview.md` 的 cache
hierarchy。

## 6. 多操作程序的 interrupt VMID 路由

**发现**：2026 年 5–6 月。同时执行 kernel 和 `hipFree` 的程序出现 signal timeout，
原因是 `RELEASE_MEM` 触发的 CP_EOP interrupt 携带错误 VMID。Driver interrupt
handler 使用 interrupt cookie VMID 路由 signal completion，但 cookie 取自最后记录的
VMID，而不是正确的 `MAP_PROCESS` context。

**证据**：gem5 中跟踪的 `hipFree` signal address 写入正确，但 interrupt cookie 为
vmid=0 而不是 vmid=3。Driver 收到 interrupt，却无法把它匹配到等待中的 user
process。

**修复**：`gem5/src/dev/amdgpu/interrupt_handler.cc` 跟踪 `MAP_PROCESS` 的 VMID，
而不是 `lastVMID`。PR：`zevorn/gem5#4`。

**调试参考**：使用 `cosim-gpu-debug` 的 signal、interrupt、translation 与 VMID
证据，并阅读 `overview.md` 的 interrupt handling 小节。

## 跨问题经验

1. **Cache coherence 分层存在**：一个层级的症状（如 signal timeout）可能来自另一层
   （如 PWC 保存旧 PTE）。下结论前应检查所有相关 cache layer。

2. **重复执行暴露 coherence 缺口**：单次测试可能掩盖 stale-state bug；应使用重复
   program matrix 验证。

3. **CP 与 CU 属于不同 coherence domain**：command processor 与 compute unit 通过
   不同路径访问内存；没有显式 invalidation 时，CP 写入的数据可能对 CU 不可见。

4. **插桩必须非侵入**：会触发 cache lookup、TLB walk 或 packet processing 的 debug
   输出可能改变失败模式；优先在现有路径上被动观察。
