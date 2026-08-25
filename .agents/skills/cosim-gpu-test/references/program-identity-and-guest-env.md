# Program identity 与 Guest 环境

启动指定 operator、variant，或任何带 Guest environment prefix 的 row 前阅读本文。
当前 `scripts/run_cosim_tests.sh` 只接受仓库内
`tests/kernels/<snake_case>.cpp` 的精确 stem，不接受 ROCm example directory 或模糊
filter。

## Program identity

每个 manifest row 必须记录：

```text
source: tests/kernels/<program>.cpp
logical binary: tests/build/<program>
runner argument: <program>
```

启动前用精确、只读检查确认 source 和唯一 stem，例如：

```bash
PROGRAM=vector_add
test -f "tests/kernels/${PROGRAM}.cpp"
test "$(find tests/kernels -maxdepth 1 -type f -name "${PROGRAM}.cpp" | wc -l)" -eq 1
sha256sum "tests/kernels/${PROGRAM}.cpp"
```

不要要求 Host 上预先存在 `tests/build/<program>`。Runner 会把 tests tree 暂存到本次
artifact，并在 fresh Guest 的 `/mnt` 中执行 `make -j1`；实际 binary 位于
`<artifact>/staging/build/<program>`，其 hash 记录在
`patch/binary-provenance.txt`。Host 旧 binary 不能替代这份证据。

若 exact source 不存在，记录 `MISSING_PROGRAM`、请求的路径和 discovery 输出，不得
替换为相近名称。Variant 必须作为新的 `tests/kernels/<variant>.cpp` identity 进入
runner，不能用基础 program 的 PASS 代替。

## Timeout 合同

默认 Guest-side operator timeout 是 60 秒；Host compile+test deadline 是 1800 秒。
只通过 runner option 调整：

```bash
./scripts/run_cosim_tests.sh \
    --test-timeout 120 \
    --guest-run-timeout 1800 vector_add
```

Timeout 变化必须写入 run manifest，并在 launch 前的 `runner-invocation.txt`、归档的
`guest-run.sh`、`runner-metadata.txt`、Guest log 与 local/top matrix 中得到证明。发生
timeout 时保留原始 row，按 `cosim-gpu-debug` 判断是无进展 wait、slow progress 还是
预算不足；不能只扩大时间后覆盖原失败证据。默认 `strict_acceptance=0` 的 timeout
probe 只能作为诊断；最终 accepted timeout row 必须另以
`COSIM_STRICT_ACCEPTANCE=1` 启动并由所有证据文件证明该值。

## Device-side `printf`

当前模型已知不支持 device-side `printf`。发现 kernel 中存在该调用时，先记录源码
位置并把 row 分类为已知模型限制或专门实验；不要把它加入 required baseline 后等待
无界超时，也不要通过删除输出语句伪造同一 program 的 PASS。

## Guest environment prefix

当前 runner 只接受空值或以下两个精确值：

```bash
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=0 \
    ./scripts/run_cosim_tests.sh vector_add
GUEST_TEST_PREFIX=HSA_ENABLE_INTERRUPT=1 \
    ./scripts/run_cosim_tests.sh vector_add
```

上面的命令用于开发/诊断 row，默认记录 `strict_acceptance=0`。需要把相同 environment
纳入 strict v2 matrix 时，在 clean tree 上为每个值新建 manifest leaf，并显式设置
`COSIM_STRICT_ACCEPTANCE=1`；不能复用或改标先前的 diagnostic artifact。

Guest workload 默认值和 prefix 解析集中在 `scripts/cosim_guest_env.sh`。不要在生成的
Guest command 中添加临时 ROCm export；需要新环境维度时，应先扩展并测试共享 helper
与 manifest schema。

有效值来自 Guest log 的 `[COSIM_ENV] HSA_ENABLE_INTERRUPT=<value>` 和同一 artifact
的 matrix，而不是 Host shell 的意图。如果 assignment 被当作 executable，或实际值为
`unknown`/与 manifest 不符，将其分类为 runner invocation defect，修复后重跑同一
program。只有产生 non-PASS row 或受控 live wait 后才转入 debug。
