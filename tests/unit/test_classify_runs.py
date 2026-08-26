#!/usr/bin/env python3

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.classify_runs import classify_artifact, write_json_atomic  # noqa: E402
from scripts.cosim_log_evidence import (  # noqa: E402
    analyze_gpu_evidence,
    completion_token_run_sha256,
    evidence_boundary_token,
    parse_rfc3339nano,
    split_docker_timestamp,
)
from scripts.cosim_artifact_audit import TABLE_HEADERS, audit  # noqa: E402


def write_complete_artifact(root: Path, program: str = "vector_add") -> None:
    patch_dir = root / "patch"
    patch_dir.mkdir(parents=True)
    boundary_binary = root / "staging/tools-build/cosim_evidence_boundary"
    boundary_binary.parent.mkdir(parents=True)
    boundary_binary.write_bytes(b"synthetic evidence boundary helper\n")
    boundary_binary.chmod(0o755)
    boundary_sha256 = hashlib.sha256(boundary_binary.read_bytes()).hexdigest()
    (root / "metadata.txt").write_text(
        "\n".join(
            (
                "run_id=unit-001",
                "category=test_pass",
                f"program={program}",
                f"test={program}",
                f"program_source=tests/kernels/{program}.cpp",
                f"program_binary=tests/build/{program}",
                f"runner_argument={program}",
                "compile_exit_code=0",
                "test_exit_code=0",
                "cleanup_status=verified",
                "cleanup_exit_code=0",
                "",
            )
        ),
        encoding="utf-8",
    )
    (root / "qemu.log").write_text(
        "root@gem5:~#\n"
        "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1\n"
        f"[PASS] {program}\n",
        encoding="utf-8",
    )
    (root / "gem5.log").write_text("gem5 simulation started\n", encoding="utf-8")
    (patch_dir / "source-snapshot.txt").write_text(
        "head_commit=0123456789abcdef\n"
        "source_fingerprint=fedcba9876543210\n",
        encoding="utf-8",
    )
    (patch_dir / "binary-provenance.txt").write_text(
        "gem5_source_commit=0123456789abcdef\n"
        "gem5_binary=/work/gem5/build/VEGA_X86/gem5.opt\n"
        f"gem5_sha256={'a' * 64}\n"
        f"gem5_evidence_boundary_binary={boundary_binary}\n"
        f"gem5_evidence_boundary_binary_sha256={boundary_sha256}\n",
        encoding="utf-8",
    )
    (root / "runner-invocation.txt").write_text(
        "schema=cosim-runner-invocation/v1\n"
        f"gem5_evidence_boundary_binary={boundary_binary}\n"
        f"gem5_evidence_boundary_binary_sha256={boundary_sha256}\n",
        encoding="utf-8",
    )
    (patch_dir / "gem5-status.txt").write_text("", encoding="utf-8")
    (patch_dir / "gem5.patch").write_text("", encoding="utf-8")


def update_metadata(root: Path, **updates: str | None) -> None:
    path = root / "metadata.txt"
    values = {}
    order = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value
        order.append(key)
    for key, value in updates.items():
        if value is None:
            values.pop(key, None)
            if key in order:
                order.remove(key)
        else:
            if key not in values:
                order.append(key)
            values[key] = value
    path.write_text(
        "".join(f"{key}={values[key]}\n" for key in order), encoding="utf-8"
    )


def write_identity_claim_sequence(
    root: Path,
    key: str,
    values: tuple[str, ...],
) -> None:
    path = root / "metadata.txt"
    prefix = f"{key}="
    remaining = [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if not line.startswith(prefix)
    ]
    claims = [f"{key}={value}" for value in values]
    path.write_text("\n".join((*claims, *remaining)) + "\n", encoding="utf-8")


def write_strict_qemu_evidence(
    root: Path,
    *,
    program: str = "vector_add",
    run_id: str = "unit-001",
    signal_number: int | None = None,
    strict_acceptance: str = "1",
) -> None:
    run_sha256 = completion_token_run_sha256(run_id)
    boundary_binary = root / "staging/tools-build/cosim_evidence_boundary"
    boundary_sha256 = hashlib.sha256(boundary_binary.read_bytes()).hexdigest()
    lines = [
        f"  Run-ID:     {run_id}",
        "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
        "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=60",
        f"__COSIM_COMPILE_DONE_{program}_{run_sha256}__:0",
        f"__COSIM_BOUNDARY_READY_{program}_{run_sha256}__:{boundary_sha256}",
        f"[PASS] {program}",
        f"__COSIM_TEST_DONE_{program}_{run_sha256}__:0",
    ]
    if signal_number is not None:
        lines.append(
            "\x1b[?2004hroot@gem5:~# qemu-system-x86_64: "
            f"terminating on signal {signal_number} from pid 1 (/bin/bash)"
        )
    qemu_log = root / "qemu.log"
    qemu_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    update_metadata(
        root,
        expected_hsa_enable_interrupt="1",
        gem5_evidence_boundary_binary=str(boundary_binary),
        gem5_evidence_boundary_binary_sha256=boundary_sha256,
        qemu_log_sha256=hashlib.sha256(qemu_log.read_bytes()).hexdigest(),
        strict_acceptance=strict_acceptance,
        test_timeout="60",
    )


def synchronize_strict_qemu_hash(root: Path) -> None:
    update_metadata(
        root,
        qemu_log_sha256=hashlib.sha256((root / "qemu.log").read_bytes()).hexdigest(),
    )


def write_strict_gem5_evidence(root: Path) -> None:
    write_strict_qemu_evidence(root)
    boundary_token = evidence_boundary_token("unit-001", "vector_add")
    started = "2026-08-26T00:00:01.000000000Z"
    finished = "2026-08-26T00:00:02.000000000Z"
    gem5_log = root / "gem5.log"
    gem5_log.write_text(
        "2026-08-26T00:00:00.100000000Z command line: "
        "/gem5/build/VEGA_X86/gem5.opt "
        "--debug-flags=HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo "
        "--socket-path=/tmp/gem5-mi300x-unit-001.sock "
        "--shmem-path=/mi300x-vram-unit-001 "
        "--shmem-host-path=/cosim-guest-ram-unit-001 "
        "--evidence-path=/cosim-artifacts/gem5-evidence.tsv "
        "--evidence-run-id=unit-001 "
        "--evidence-test-id=vector_add "
        f"--evidence-token={boundary_token}\n"
        "2026-08-26T00:00:00.200000000Z src/dev/amdgpu/mi300x_vfio_user.cc:312: "
        "info: MI300XVfioUser: client connected (vfio-user)\n"
        "2026-08-26T00:00:01.100000000Z 10: "
        "system.Shader.gpu_cmd_proc.dispatcher: launching kernel: "
        "Some kernel, dispatch ID: 0\n"
        "2026-08-26T00:00:01.200000000Z 11: system.Shader: "
        "Dispatching a workgroup to CU 0: WG 0\n"
        "2026-08-26T00:00:01.300000000Z 12: dispatcher: notify WgCompl 0\n"
        "2026-08-26T00:00:01.400000000Z 13: dispatcher: Completed kernel 0\n",
        encoding="utf-8",
    )
    gpu_evidence = root / "gem5-evidence.tsv"
    gpu_evidence.write_text(
        "schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t0\t0\tsession_start\t-1\t-1\t-1\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t1\t1\tclient_connected\t0\t-1\t-1\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t2\t2\ttest_begin\t0\t-1\t-1\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t3\t10\tkernel_launch\t0\t0\t-1\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t4\t11\tworkgroup_dispatch\t0\t0\t0\t0\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t5\t12\tworkgroup_complete\t0\t0\t0\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t6\t13\tkernel_complete\t0\t0\t-1\t-1\n"
        "COSIM_GPU_EVIDENCE_V1\tunit-001\t7\t14\ttest_end\t0\t-1\t-1\t-1\n",
        encoding="ascii",
    )
    gpu_evidence.chmod(0o600)
    digest = hashlib.sha256(gem5_log.read_bytes()).hexdigest()
    update_metadata(
        root,
        strict_acceptance="1",
        guest_test_started_at=started,
        guest_test_finished_at=finished,
        gem5_config_args="defaults:num-gpus=1,num-cus=40,host-mem=8G,"
        "vram-size=16GiB;"
        f"evidence-test-id=vector_add,evidence-token={boundary_token};"
        "debug-flags=HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo",
        gem5_evidence_end_seq="7",
        gem5_evidence_sha256=hashlib.sha256(gpu_evidence.read_bytes()).hexdigest(),
        gem5_evidence_start_seq="2",
        gem5_evidence_test_id="vector_add",
        gem5_evidence_token=boundary_token,
        gem5_log_sha256=digest,
    )


def synchronize_strict_gem5_hash(root: Path) -> None:
    update_metadata(
        root,
        gem5_log_sha256=hashlib.sha256((root / "gem5.log").read_bytes()).hexdigest(),
    )


def synchronize_strict_gpu_evidence_hash(root: Path) -> None:
    update_metadata(
        root,
        gem5_evidence_sha256=hashlib.sha256(
            (root / "gem5-evidence.tsv").read_bytes()
        ).hexdigest(),
    )


def write_strict_boundary_failure_artifact(root: Path, phase: str) -> None:
    """生成 compile 或 BEGIN 失败后的完整 runner 归档 fixture。"""

    if phase not in {"compile", "begin"}:
        raise ValueError(f"unsupported boundary failure phase: {phase}")
    write_strict_gem5_evidence(root)
    run_sha256 = completion_token_run_sha256("unit-001")
    boundary_binary = root / "staging/tools-build/cosim_evidence_boundary"
    boundary_sha256 = hashlib.sha256(boundary_binary.read_bytes()).hexdigest()
    qemu_lines = [
        "  Run-ID:     unit-001",
        "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
        "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=60",
    ]
    if phase == "compile":
        qemu_lines.extend(
            (
                f"__COSIM_COMPILE_DONE_vector_add_{run_sha256}__:2",
                f"__COSIM_TEST_DONE_vector_add_{run_sha256}__:2",
            )
        )
        compile_exit_code = "2"
        test_exit_code = "2"
    else:
        qemu_lines.extend(
            (
                f"__COSIM_COMPILE_DONE_vector_add_{run_sha256}__:0",
                f"__COSIM_BOUNDARY_READY_vector_add_{run_sha256}__:"
                f"{boundary_sha256}",
                f"__COSIM_TEST_DONE_vector_add_{run_sha256}__:125",
            )
        )
        compile_exit_code = "0"
        test_exit_code = "125"
    qemu_log = root / "qemu.log"
    qemu_log.write_text("\n".join(qemu_lines) + "\n", encoding="utf-8")

    gem5_log = root / "gem5.log"
    gem5_lines = gem5_log.read_text(encoding="utf-8").splitlines()
    gem5_log.write_text("\n".join(gem5_lines[:2]) + "\n", encoding="utf-8")
    gpu_evidence = root / "gem5-evidence.tsv"
    gpu_lines = gpu_evidence.read_text(encoding="ascii").splitlines()
    gpu_line_count = 3 if phase == "compile" else 4
    gpu_evidence.write_text(
        "\n".join(gpu_lines[:gpu_line_count]) + "\n", encoding="ascii"
    )

    update_metadata(
        root,
        category="test_fail",
        compile_exit_code=compile_exit_code,
        test_exit_code=test_exit_code,
        cleanup_status="verified",
        cleanup_exit_code="0",
        gem5_evidence_start_seq="",
        gem5_evidence_end_seq="",
        gem5_evidence_sha256=hashlib.sha256(gpu_evidence.read_bytes()).hexdigest(),
        gem5_log_sha256=hashlib.sha256(gem5_log.read_bytes()).hexdigest(),
        qemu_log_sha256=hashlib.sha256(qemu_log.read_bytes()).hexdigest(),
    )
    if phase == "compile":
        update_metadata(
            root,
            gem5_evidence_boundary_binary="",
            gem5_evidence_boundary_binary_sha256="",
        )
        for path in (
            root / "runner-invocation.txt",
            root / "patch/binary-provenance.txt",
        ):
            retained = [
                line
                for line in path.read_text(encoding="utf-8").splitlines()
                if not line.startswith("gem5_evidence_boundary_binary")
            ]
            path.write_text("\n".join(retained) + "\n", encoding="utf-8")
        boundary_binary.unlink()
    (root / "metadata.txt").replace(root / "runner-metadata.txt")


class LogEvidenceParserTests(unittest.TestCase):
    def test_rfc3339nano_offsets_and_fraction_widths_are_normalized(self) -> None:
        utc = parse_rfc3339nano("2026-08-26T00:00:00.123456789Z")
        offset = parse_rfc3339nano("2026-08-26T08:00:00.123456789+08:00")
        self.assertEqual(utc, offset)
        self.assertEqual(
            parse_rfc3339nano("2026-08-26T00:00:00.100000000Z"),
            parse_rfc3339nano("2026-08-26T00:00:00.1Z"),
        )
        self.assertIsNotNone(parse_rfc3339nano("2026-08-26T00:00:00Z"))

    def test_docker_prefix_requires_a_valid_timestamp_and_ascii_space(self) -> None:
        timestamp_ns, payload = split_docker_timestamp(
            "2026-08-26T00:00:00.123456789Z fatal: stopped"
        )
        self.assertIsNotNone(timestamp_ns)
        self.assertEqual("fatal: stopped", payload)
        self.assertEqual(
            (None, "2026-02-30T00:00:00Z warn: impossible"),
            split_docker_timestamp("2026-02-30T00:00:00Z warn: impossible"),
        )

    def test_structured_gpu_evidence_requires_exact_final_cursor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            analysis = analyze_gpu_evidence(
                root / "gem5-evidence.tsv",
                expected_run_id="unit-001",
                start_seq="1",
                end_seq="4",
            )
            self.assertFalse(analysis["boundary_ok"])
            self.assertFalse(analysis["ok"])

    def test_structured_gpu_evidence_rejects_run_and_sequence_forgery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            evidence.write_text(
                evidence.read_text(encoding="ascii").replace(
                    "unit-001\t3", "other-run\t9"
                ),
                encoding="ascii",
            )
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                start_seq="1",
                end_seq="5",
            )
            self.assertFalse(analysis["structural_ok"])
            reasons = {
                error["reason"] for error in analysis["structural_errors"]
            }
            self.assertIn("run_id", reasons)

    def test_structured_gpu_evidence_rejects_unframed_record(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            evidence.write_bytes(evidence.read_bytes().rstrip(b"\n"))
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                start_seq="1",
                end_seq="5",
            )
            self.assertFalse(analysis["structural_ok"])
            self.assertEqual(
                "record_framing", analysis["structural_errors"][0]["reason"]
            )

    def test_structured_gpu_evidence_requires_private_mode(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            evidence.chmod(0o640)
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                start_seq="1",
                end_seq="5",
            )
            self.assertFalse(analysis["mode_ok"])
            self.assertFalse(analysis["structural_ok"])

    def test_structured_gpu_evidence_binds_configured_gpu_set(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            analysis = analyze_gpu_evidence(
                root / "gem5-evidence.tsv",
                expected_run_id="unit-001",
                expected_num_gpus="2",
                start_seq="2",
                end_seq="7",
            )
            self.assertFalse(analysis["ok"])
            self.assertIn("client_set_mismatch", analysis["causal_errors"])

    def test_structured_gpu_evidence_rejects_duplicate_client(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            evidence.write_text(
                evidence.read_text(encoding="ascii").replace(
                    "3\t10\tkernel_launch\t0\t0\t-1\t-1",
                    "3\t10\tclient_connected\t0\t-1\t-1\t-1",
                ),
                encoding="ascii",
            )
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                start_seq="2",
                end_seq="7",
            )
            self.assertFalse(analysis["ok"])
            self.assertIn("client_after_start:3", analysis["causal_errors"])

    def test_structured_gpu_evidence_rejects_events_after_kernel_end(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            evidence.write_text(
                evidence.read_text(encoding="ascii").replace(
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t7\t14\t"
                    "test_end\t0\t-1\t-1\t-1\n",
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t7\t14\t"
                    "workgroup_dispatch\t0\t0\t1\t0\n"
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t8\t15\t"
                    "workgroup_complete\t0\t0\t1\t-1\n"
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t9\t16\t"
                    "test_end\t0\t-1\t-1\t-1\n",
                ),
                encoding="ascii",
            )
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                start_seq="2",
                end_seq="9",
            )
            self.assertFalse(analysis["ok"])
            self.assertIn(
                "dispatch_after_kernel_completion:7",
                analysis["causal_errors"],
            )

    def test_structured_gpu_evidence_rejects_post_end_complete_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_complete_artifact(root)
            write_strict_gem5_evidence(root)
            evidence = root / "gem5-evidence.tsv"
            with evidence.open("a", encoding="ascii") as handle:
                handle.write(
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t8\t15\t"
                    "kernel_launch\t0\t1\t-1\t-1\n"
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t9\t16\t"
                    "workgroup_dispatch\t0\t1\t0\t0\n"
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t10\t17\t"
                    "workgroup_complete\t0\t1\t0\t-1\n"
                    "COSIM_GPU_EVIDENCE_V1\tunit-001\t11\t18\t"
                    "kernel_complete\t0\t1\t-1\t-1\n"
                )
            analysis = analyze_gpu_evidence(
                evidence,
                expected_run_id="unit-001",
                expected_num_gpus="1",
                require_test_boundaries=True,
                start_seq="2",
                end_seq="11",
            )
            self.assertFalse(analysis["boundary_ok"])
            self.assertFalse(analysis["ok"])
            self.assertEqual(11, analysis["final_seq"])
            self.assertEqual(7, analysis["boundary_events"]["end"]["seq"])


class ClassifyRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "run"
        self.root.mkdir()
        write_complete_artifact(self.root)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def classify(self, program: str = "vector_add") -> dict:
        return classify_artifact(self.root, program)

    def assert_failed_for(self, reason: str) -> dict:
        result = self.classify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn(reason, result["reasons"])
        return result

    def test_complete_exact_evidence_passes(self) -> None:
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertEqual("all_acceptance_gates_passed", result["reason"])
        self.assertEqual(1, result["checks"]["markers"]["exact_pass_count"])

    def test_legacy_nonstrict_artifact_without_helper_marker_remains_accepted(
        self,
    ) -> None:
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertNotIn(
            "gem5_evidence_boundary_binary_sha256",
            (self.root / "metadata.txt").read_text(encoding="utf-8"),
        )

    def test_nonstrict_artifact_with_helper_hash_validates_ready_marker(
        self,
    ) -> None:
        write_strict_qemu_evidence(self.root, strict_acceptance="0")
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertTrue(
            result["checks"]["evidence_boundary_helper"]["marker_ok"]
        )

    def test_nonstrict_artifact_rejects_helper_ready_hash_mismatch(self) -> None:
        write_strict_qemu_evidence(self.root, strict_acceptance="0")
        qemu_log = self.root / "qemu.log"
        lines = qemu_log.read_text(encoding="utf-8").splitlines()
        lines = [
            f"{line.rsplit(':', 1)[0]}:{'f' * 64}"
            if line.startswith("__COSIM_BOUNDARY_READY_")
            else line
            for line in lines
        ]
        qemu_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "qemu_log:boundary_helper_marker",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_requested_identity_must_match_all_recorded_identity(self) -> None:
        update_metadata(self.root, runner_argument="reduction")
        self.assert_failed_for("program_identity_mismatch")

    def test_legacy_runner_arg_preserves_boundary_whitespace(self) -> None:
        for suffix, runner_arg in (
            ("leading", " vector_add"),
            ("trailing", "vector_add "),
        ):
            with self.subTest(runner_arg=runner_arg):
                case_root = Path(self.temporary.name) / f"runner-arg-{suffix}"
                write_complete_artifact(case_root)
                update_metadata(
                    case_root,
                    runner_argument=None,
                    runner_arg=runner_arg,
                )

                result = classify_artifact(case_root, "vector_add")

                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("program_identity_invalid", result["reasons"])
                self.assertEqual(
                    runner_arg,
                    result["checks"]["program_identity"]["runner_argument"],
                )

    def test_legacy_runner_arg_only_remains_compatible(self) -> None:
        update_metadata(
            self.root,
            runner_argument=None,
            runner_arg="vector_add",
        )

        result = self.classify()

        self.assertEqual("PASS", result["outcome"])
        self.assertTrue(result["checks"]["program_identity"]["ok"])

    def test_matching_canonical_and_legacy_runner_claims_pass(self) -> None:
        update_metadata(self.root, runner_arg="vector_add")

        result = self.classify()

        self.assertEqual("PASS", result["outcome"])
        self.assertTrue(result["checks"]["program_identity"]["ok"])

    def test_any_malformed_runner_claim_is_invalid(self) -> None:
        for suffix, runner_arg in (
            ("empty", ""),
            ("leading", " vector_add"),
            ("trailing", "vector_add "),
            ("control", "vector_add\t"),
        ):
            with self.subTest(runner_arg=runner_arg):
                case_root = Path(self.temporary.name) / f"runner-claim-{suffix}"
                write_complete_artifact(case_root)
                update_metadata(case_root, runner_arg=runner_arg)

                result = classify_artifact(case_root, "vector_add")

                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("program_identity_invalid", result["reasons"])

    def test_conflicting_runner_claim_is_mismatch(self) -> None:
        update_metadata(self.root, runner_arg="reduction")

        result = self.classify()

        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("program_identity_mismatch", result["reasons"])

    def test_all_distinct_identity_aliases_can_match(self) -> None:
        update_metadata(
            self.root,
            expected_program="vector_add",
            runner_arg="vector_add",
        )

        result = self.classify()

        self.assertEqual("PASS", result["outcome"])
        claims = result["checks"]["program_identity"]["identity_claims"]
        self.assertEqual(
            {
                "program",
                "test",
                "expected_program",
                "runner_argument",
                "runner_arg",
            },
            {claim["key"] for claim in claims},
        )

    def test_each_identity_alias_rejects_malformed_single_claim(self) -> None:
        identity_keys = (
            "program",
            "test",
            "expected_program",
            "runner_argument",
            "runner_arg",
        )
        malformed_values = (
            "",
            " vector_add",
            "vector_add ",
            "\tvector_add",
            "vector_add\t",
        )
        for key in identity_keys:
            for value in malformed_values:
                with self.subTest(key=key, value=value):
                    case_root = Path(self.temporary.name) / (
                        f"malformed-{key}-{len(value)}-"
                        f"{malformed_values.index(value)}"
                    )
                    write_complete_artifact(case_root)
                    if key == "runner_arg":
                        update_metadata(
                            case_root,
                            runner_argument=None,
                            runner_arg=value,
                        )
                    else:
                        update_metadata(case_root, **{key: value})

                    result = classify_artifact(case_root, "vector_add")

                    self.assertEqual("FAIL", result["outcome"])
                    self.assertIn(
                        "program_identity_invalid", result["reasons"]
                    )

    def test_each_distinct_identity_alias_conflict_is_mismatch(self) -> None:
        for key in (
            "program",
            "test",
            "expected_program",
            "runner_argument",
            "runner_arg",
        ):
            with self.subTest(key=key):
                case_root = Path(self.temporary.name) / f"conflict-{key}"
                write_complete_artifact(case_root)
                update_metadata(case_root, **{key: "reduction"})

                result = classify_artifact(case_root, "vector_add")

                self.assertEqual("FAIL", result["outcome"])
                self.assertIn(
                    "program_identity_mismatch", result["reasons"]
                )

    def test_cli_rejects_every_duplicate_identity_key_and_order(self) -> None:
        identity_keys = (
            "program",
            "test",
            "expected_program",
            "runner_argument",
            "runner_arg",
        )
        patterns = (
            ("same", ("vector_add", "vector_add")),
            ("early-invalid", (" vector_add", "vector_add")),
            ("early-conflict", ("reduction", "vector_add")),
            ("late-invalid", ("vector_add", "vector_add ")),
            ("late-conflict", ("vector_add", "reduction")),
        )
        for key in identity_keys:
            for pattern, claim_values in patterns:
                with self.subTest(key=key, pattern=pattern):
                    case_root = Path(self.temporary.name) / (
                        f"duplicate-{key}-{pattern}"
                    )
                    write_complete_artifact(case_root)
                    write_identity_claim_sequence(
                        case_root, key, claim_values
                    )

                    completed = subprocess.run(
                        (
                            sys.executable,
                            str(REPO_ROOT / "scripts" / "classify_runs.py"),
                            "--artifact-dir",
                            str(case_root),
                            "--program",
                            "vector_add",
                            "--json",
                        ),
                        check=False,
                        capture_output=True,
                        text=True,
                    )

                    self.assertEqual(1, completed.returncode, completed.stderr)
                    payload = json.loads(completed.stdout)
                    self.assertEqual("FAIL", payload["outcome"])
                    self.assertIn(
                        "program_identity_duplicate", payload["reasons"]
                    )
                    identity = payload["checks"]["program_identity"]
                    self.assertEqual(
                        [key], identity["duplicate_identity_keys"]
                    )
                    matching_claims = [
                        claim
                        for claim in identity["identity_claims"]
                        if claim["key"] == key
                    ]
                    self.assertEqual(
                        list(claim_values),
                        [claim["value"] for claim in matching_claims],
                    )
                    self.assertEqual(
                        [1, 2],
                        [claim["line"] for claim in matching_claims],
                    )

    def test_local_source_and_binary_basenames_are_exact(self) -> None:
        update_metadata(self.root, program_source="tests/kernels/reduction.cpp")
        self.assert_failed_for("program_identity_mismatch")

    def test_compile_success_must_be_explicit(self) -> None:
        update_metadata(self.root, compile_exit_code=None)
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "metadata:compile_exit_code",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_compile_failure_cannot_be_hidden_by_exit_zero(self) -> None:
        update_metadata(self.root, compile_exit_code="2")
        self.assert_failed_for("compile_failure")

    def test_compile_failure_without_boundaries_archives_fail_verdict(self) -> None:
        write_strict_boundary_failure_artifact(self.root, "compile")

        result = classify_artifact(self.root, "vector_add")
        write_json_atomic(self.root / "verdict.json", result)
        (self.root / "matrix.tsv").write_text(
            "program\toutcome\texit_code\treason\tartifact_dir\n"
            f"vector_add\t{result['outcome']}\t2\t{result['reason']}\t"
            f"{self.root}\n",
            encoding="utf-8",
        )

        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("compile_failure", result["reasons"])
        self.assertTrue(result["checks"]["cleanup"]["ok"])
        self.assertTrue((self.root / "runner-metadata.txt").is_file())
        self.assertTrue((self.root / "verdict.json").is_file())
        self.assertTrue((self.root / "matrix.tsv").is_file())

    def test_begin_failure_without_closed_boundary_archives_fail_verdict(
        self,
    ) -> None:
        write_strict_boundary_failure_artifact(self.root, "begin")

        result = classify_artifact(self.root, "vector_add")
        write_json_atomic(self.root / "verdict.json", result)
        (self.root / "matrix.tsv").write_text(
            "program\toutcome\texit_code\treason\tartifact_dir\n"
            f"vector_add\t{result['outcome']}\t125\t{result['reason']}\t"
            f"{self.root}\n",
            encoding="utf-8",
        )

        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("nonzero_test_exit", result["reasons"])
        self.assertTrue(result["checks"]["cleanup"]["ok"])
        gpu_analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertEqual(1, gpu_analysis["gpu_evidence"]["event_counts"]["test_begin"])
        self.assertEqual(0, gpu_analysis["gpu_evidence"]["event_counts"]["test_end"])
        self.assertTrue((self.root / "runner-metadata.txt").is_file())
        self.assertTrue((self.root / "verdict.json").is_file())
        self.assertTrue((self.root / "matrix.tsv").is_file())

    def test_nonzero_test_exit_fails(self) -> None:
        update_metadata(self.root, test_exit_code="9", category="test_fail")
        self.assert_failed_for("nonzero_test_exit")

    def test_program_identity_rejects_more_than_128_ascii_bytes(self) -> None:
        program = "a" * 129
        write_complete_artifact(self.root / "long-program", program)

        result = classify_artifact(self.root / "long-program", program)

        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("program_identity_invalid", result["reasons"])

    def test_exact_pass_marker_is_required(self) -> None:
        (self.root / "qemu.log").write_text(
            "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1\n"
            "[PASS] vector_add extra text\n",
            encoding="utf-8",
        )
        result = self.assert_failed_for("invalid_pass_marker_count")
        self.assertEqual(0, result["checks"]["markers"]["exact_pass_count"])

    def test_duplicate_exact_pass_marker_fails(self) -> None:
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("[PASS] vector_add\n")
        result = self.assert_failed_for("invalid_pass_marker_count")
        self.assertEqual(2, result["checks"]["markers"]["exact_pass_count"])

    def test_any_fail_marker_fails(self) -> None:
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("[FAIL] another_check\n")
        self.assert_failed_for("fail_marker_present")

    def test_timeout_category_fails_even_with_pass_marker(self) -> None:
        update_metadata(self.root, category="test_timeout")
        self.assert_failed_for("timeout")

    def test_timeout_policy_marker_is_not_a_timeout_signal(self) -> None:
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=60\n")
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertFalse(result["checks"]["timeout"]["observed"])

    def test_timeout_log_marker_fails_even_with_pass_marker(self) -> None:
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("[TIMEOUT] vector_add exceeded 60s\n")
        self.assert_failed_for("timeout")

    def test_nonpolicy_cosim_timeout_marker_still_fails(self) -> None:
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("[COSIM_TIMEOUT] vector_add exceeded 60s\n")
        result = self.assert_failed_for("timeout")
        self.assertTrue(result["checks"]["timeout"]["observed"])

    def test_simulator_early_exit_category_fails(self) -> None:
        update_metadata(self.root, category="gem5_exit")
        self.assert_failed_for("simulator_early_exit")

    def test_gem5_fatal_log_cannot_be_hidden_by_pass_metadata(self) -> None:
        (self.root / "gem5.log").write_text(
            "panic: simulator stopped before normal completion\n", encoding="utf-8"
        )
        self.assert_failed_for("simulator_early_exit")

    def test_docker_timestamped_source_fatal_is_detected(self) -> None:
        (self.root / "gem5.log").write_text(
            "2026-08-26T08:00:00.123456789+08:00 "
            "src/base/logging.cc:10: fatal: stopped\n",
            encoding="utf-8",
        )
        self.assert_failed_for("simulator_early_exit")

    def test_docker_timestamped_source_assertion_is_detected(self) -> None:
        (self.root / "gem5.log").write_text(
            "2026-08-26T00:00:00Z gem5.opt: src/foo.cc:20: run: "
            "Assertion queue != nullptr failed.\n",
            encoding="utf-8",
        )
        self.assert_failed_for("simulator_early_exit")

    def test_qemu_signal_15_before_test_token_is_detected(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        test_marker = (
            "__COSIM_TEST_DONE_vector_add_"
            f"{completion_token_run_sha256('unit-001')}__:0"
        )
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                test_marker,
                "\x1b[?2004hroot@gem5:~# qemu-system-x86_64: "
                "terminating on signal 15 from pid 1 (/bin/bash)\n"
                f"{test_marker}",
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)
        self.assert_failed_for("simulator_early_exit")

    def test_qemu_signal_15_after_test_token_is_expected_cleanup(self) -> None:
        write_strict_gem5_evidence(self.root)
        write_strict_qemu_evidence(self.root, signal_number=15)

        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        lifetime = result["checks"]["simulator_lifetime"]
        self.assertTrue(lifetime["ok"])
        self.assertEqual(1, len(lifetime["expected_cleanup_events"]))

    def test_readline_prefix_on_hsa_marker_is_terminal_semantics(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
                "\x1b[?2004l\r[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)

        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertEqual(
            ["1"],
            result["checks"]["qemu_completion"]["analysis"]["hsa_values"],
        )

    def test_arbitrary_prefix_cannot_create_hsa_evidence(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
                "quoted text\r[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertEqual(
            [],
            result["checks"]["qemu_completion"]["analysis"]["hsa_values"],
        )

    def test_duplicate_readline_prefixed_hsa_marker_is_rejected(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
                "\x1b[?2004l\r[COSIM_ENV] HSA_ENABLE_INTERRUPT=1\n"
                "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertEqual(
            ["1", "1"],
            result["checks"]["qemu_completion"]["analysis"]["hsa_values"],
        )

    def test_readline_prefix_cannot_hide_native_fatal(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        with qemu_log.open("a", encoding="utf-8") as handle:
            handle.write(
                "\x1b[?2004l\rSegmentation fault (core dumped)\n"
            )
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("simulator_early_exit")
        self.assertEqual(
            "native_segmentation_fault",
            result["checks"]["qemu_completion"]["analysis"][
                "fatal_events"
            ][0]["kind"],
        )

    def test_qemu_signal_9_or_11_after_test_token_is_not_expected(self) -> None:
        write_strict_gem5_evidence(self.root)
        for signal_number in (9, 11):
            with self.subTest(signal_number=signal_number):
                write_strict_qemu_evidence(
                    self.root, signal_number=signal_number
                )
                self.assert_failed_for("simulator_early_exit")

    def test_strict_gpu_execution_evidence_passes(self) -> None:
        write_strict_gem5_evidence(self.root)
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])
        self.assertTrue(result["checks"]["gem5_gpu_execution"]["ok"])
        self.assertEqual(
            1,
            result["checks"]["gem5_gpu_execution"]["analysis"][
                "workgroup_completion_count"
            ],
        )
        self.assertTrue(result["checks"]["qemu_completion"]["ok"])
        self.assertTrue(
            result["checks"]["qemu_completion"]["analysis"][
                "stable_snapshot_ok"
            ]
        )
        helper = result["checks"]["evidence_boundary_helper"]
        self.assertTrue(helper["ok"])
        self.assertEqual(
            str(self.root / "staging/tools-build/cosim_evidence_boundary"),
            result["provenance"]["gem5_evidence_boundary_binary"],
        )
        self.assertEqual(
            helper["sha256"],
            result["provenance"][
                "gem5_evidence_boundary_binary_sha256"
            ],
        )

    def test_strict_replaced_helper_with_only_binary_provenance_update_fails(
        self,
    ) -> None:
        write_strict_gem5_evidence(self.root)
        helper = self.root / "staging/tools-build/cosim_evidence_boundary"
        old_sha256 = hashlib.sha256(helper.read_bytes()).hexdigest()
        helper.write_bytes(b"replaced executable helper\n")
        helper.chmod(0o755)
        new_sha256 = hashlib.sha256(helper.read_bytes()).hexdigest()
        provenance = self.root / "patch/binary-provenance.txt"
        provenance.write_text(
            provenance.read_text(encoding="utf-8").replace(
                f"gem5_evidence_boundary_binary_sha256={old_sha256}",
                f"gem5_evidence_boundary_binary_sha256={new_sha256}",
            ),
            encoding="utf-8",
        )

        result = self.assert_failed_for("evidence_incomplete")
        self.assertFalse(result["checks"]["evidence_boundary_helper"]["ok"])
        self.assertIn(
            "helper_identity:sha256_mismatch",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_strict_duplicate_boundary_ready_marker_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        ready = next(
            line
            for line in qemu_log.read_text(encoding="utf-8").splitlines()
            if line.startswith("__COSIM_BOUNDARY_READY_")
        )
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                f"{ready}\n", f"{ready}\n{ready}\n"
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertEqual(
            2,
            result["checks"]["qemu_completion"]["analysis"][
                "boundary_ready_marker_count"
            ],
        )

    def test_strict_boundary_ready_hash_mismatch_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        lines = qemu_log.read_text(encoding="utf-8").splitlines()
        lines = [
            f"{line.rsplit(':', 1)[0]}:{'f' * 64}"
            if line.startswith("__COSIM_BOUNDARY_READY_")
            else line
            for line in lines
        ]
        qemu_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        synchronize_strict_qemu_hash(self.root)

        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertFalse(
            result["checks"]["qemu_completion"]["analysis"]["sequence"][
                "ok"
            ]
        )

    def test_strict_post_token_chain_fails_after_coordinated_hash_update(self) -> None:
        write_strict_gem5_evidence(self.root)
        evidence = self.root / "gem5-evidence.tsv"
        with evidence.open("a", encoding="ascii") as handle:
            handle.write(
                "COSIM_GPU_EVIDENCE_V1\tunit-001\t8\t15\t"
                "kernel_launch\t0\t1\t-1\t-1\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-001\t9\t16\t"
                "workgroup_dispatch\t0\t1\t0\t0\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-001\t10\t17\t"
                "workgroup_complete\t0\t1\t0\t-1\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-001\t11\t18\t"
                "kernel_complete\t0\t1\t-1\t-1\n"
            )
        synchronize_strict_gpu_evidence_hash(self.root)
        update_metadata(self.root, gem5_evidence_end_seq="11")
        result = self.assert_failed_for("gpu_execution_unproven")
        analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertFalse(analysis["gpu_evidence"]["boundary_ok"])
        self.assertEqual(
            7,
            analysis["gpu_evidence"]["boundary_events"]["end"]["seq"],
        )

    def test_strict_gem5_command_rejects_duplicate_evidence_option(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "--evidence-run-id=unit-001 ",
                "--evidence-run-id=unit-001 "
                "--evidence-run-id=unit-001 ",
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        result = self.assert_failed_for("gpu_execution_unproven")
        analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertFalse(analysis["command_identity_ok"])
        self.assertTrue(analysis["noncanonical_run_tokens"])

    def test_strict_qemu_hash_mismatch_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        with (self.root / "qemu.log").open("a", encoding="utf-8") as handle:
            handle.write("benign line after recorded hash\n")
        self.assert_failed_for("qemu_completion_unproven")

    def test_strict_qemu_stale_run_marker_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "  Run-ID:     unit-001", "  Run-ID:     stale-001"
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)
        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertEqual(
            [{"line": 1, "run_id": "stale-001"}],
            result["checks"]["qemu_completion"]["analysis"]["run_markers"],
        )

    def test_strict_qemu_duplicate_run_marker_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        with qemu_log.open("a", encoding="utf-8") as handle:
            handle.write("  Run-ID:     unit-001\n")
        synchronize_strict_qemu_hash(self.root)
        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertEqual(
            2,
            result["checks"]["qemu_completion"]["analysis"]["run_marker_count"],
        )

    def test_strict_completion_token_is_bound_to_run_id(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                completion_token_run_sha256("unit-001"), "0" * 64
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)
        self.assert_failed_for("qemu_completion_unproven")

    def test_strict_pass_after_cleanup_signal_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        run_sha = completion_token_run_sha256("unit-001")
        pass_and_test = (
            "[PASS] vector_add\n"
            f"__COSIM_TEST_DONE_vector_add_{run_sha}__:0"
        )
        test_signal_pass = (
            f"__COSIM_TEST_DONE_vector_add_{run_sha}__:0\n"
            "\x1b[?2004hroot@gem5:~# qemu-system-x86_64: "
            "terminating on signal 15 from pid 1 (/bin/bash)\n"
            "[PASS] vector_add"
        )
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                pass_and_test, test_signal_pass
            ),
            encoding="utf-8",
        )
        synchronize_strict_qemu_hash(self.root)
        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertIn("simulator_early_exit", result["reasons"])
        self.assertIn(
            "run<hsa<timeout<compile<boundary_ready<pass<test",
            result["checks"]["qemu_completion"]["analysis"]["order_errors"],
        )

    def test_strict_qemu_symlink_snapshot_fails_closed(self) -> None:
        write_strict_gem5_evidence(self.root)
        qemu_log = self.root / "qemu.log"
        target = self.root / "qemu-target.log"
        qemu_log.rename(target)
        qemu_log.symlink_to(target.name)
        result = self.assert_failed_for("qemu_completion_unproven")
        self.assertFalse(
            result["checks"]["qemu_completion"]["analysis"][
                "stable_snapshot_ok"
            ]
        )

    def test_strict_gem5_symlink_snapshot_fails_closed(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        target = self.root / "gem5-target.log"
        gem5_log.rename(target)
        gem5_log.symlink_to(target.name)
        result = self.assert_failed_for("gpu_execution_unproven")
        self.assertFalse(
            result["checks"]["gem5_gpu_execution"]["analysis"][
                "stable_snapshot_ok"
            ]
        )

    def test_native_gem5_abort_and_segfault_are_fatal(self) -> None:
        for payload in (
            "Program aborted at tick 42",
            "Program aborted",
            "gem5 has encountered a segmentation fault!",
            "Segmentation fault (core dumped)",
            "Aborted (core dumped)",
            "gem5.opt: Segmentation fault (core dumped)",
            "qemu-system-x86_64: Aborted (core dumped)",
        ):
            with self.subTest(payload=payload):
                write_strict_gem5_evidence(self.root)
                with (self.root / "gem5.log").open("a", encoding="utf-8") as handle:
                    handle.write(f"2026-08-26T00:00:01.500000000Z {payload}\n")
                synchronize_strict_gem5_hash(self.root)
                result = self.assert_failed_for("simulator_early_exit")
                self.assertGreater(
                    result["checks"]["gem5_gpu_execution"]["analysis"][
                        "fatal_count"
                    ],
                    0,
                )

    def test_qemu_shell_crashes_after_completion_are_fatal(self) -> None:
        for payload, expected_kind in (
            (
                "qemu-system-x86_64: Segmentation fault (core dumped)",
                "native_segmentation_fault",
            ),
            (
                "scripts/cosim_launch.sh: line 786: 12345 "
                'Segmentation fault      (core dumped) "${QEMU_CMD[@]}"',
                "native_segmentation_fault",
            ),
            (
                "scripts/cosim_launch.sh: line 786: 12345 "
                'Aborted                 (core dumped) "${QEMU_CMD[@]}"',
                "native_aborted",
            ),
        ):
            with self.subTest(payload=payload):
                write_strict_gem5_evidence(self.root)
                qemu_log = self.root / "qemu.log"
                with qemu_log.open("a", encoding="utf-8") as handle:
                    handle.write(f"{payload}\n")
                synchronize_strict_qemu_hash(self.root)
                result = self.assert_failed_for("simulator_early_exit")
                self.assertEqual(
                    expected_kind,
                    result["checks"]["qemu_completion"]["analysis"][
                        "fatal_events"
                    ][0]["kind"],
                )

    def test_quoted_shell_crash_text_is_not_fatal(self) -> None:
        write_strict_gem5_evidence(self.root)
        with (self.root / "gem5.log").open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-08-26T00:00:01.500000000Z warn: quoted text "
                "Segmentation fault (core dumped)\n"
            )
        synchronize_strict_gem5_hash(self.root)
        result = self.classify()
        self.assertEqual("PASS", result["outcome"])

    def test_strict_gem5_timestamp_regression_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "2026-08-26T00:00:00.200000000Z src/dev",
                "2026-08-26T00:00:00.050000000Z src/dev",
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        result = self.assert_failed_for("gpu_execution_unproven")
        self.assertEqual(
            [2],
            result["checks"]["gem5_gpu_execution"]["analysis"][
                "timestamp_regression_lines"
            ],
        )

    def test_strict_gem5_warning_timestamp_regression_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        with gem5_log.open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-08-26T00:00:01.350000000Z "
                "src/gpu-compute/gpu_command_processor.cc:799: "
                "warn: Ignoring vendor packet\n"
            )
        synchronize_strict_gem5_hash(self.root)

        result = self.assert_failed_for("gpu_execution_unproven")
        analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertEqual([7], analysis["timestamp_regression_lines"])

    def test_strict_gem5_command_must_precede_client_and_gpu(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "command line:", "warn: quoted command line:"
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        result = self.assert_failed_for("gpu_execution_unproven")
        self.assertFalse(
            result["checks"]["gem5_gpu_execution"]["analysis"][
                "command_identity_ok"
            ]
        )

    def test_warning_text_cannot_supply_gpu_acceptance_events(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        text = gem5_log.read_text(encoding="utf-8")
        replacements = (
            (
                "src/dev/amdgpu/mi300x_vfio_user.cc:312: info: "
                "MI300XVfioUser: client connected (vfio-user)",
                "src/a.cc:1: warn: MI300XVfioUser: client connected "
                "(vfio-user)",
            ),
            (
                "10: system.Shader.gpu_cmd_proc.dispatcher: launching "
                "kernel: Some kernel, dispatch ID: 0",
                "src/a.cc:2: warn: dispatcher: launching kernel: quoted, "
                "dispatch ID: 0",
            ),
            (
                "11: system.Shader: Dispatching a workgroup to CU 0: WG 0",
                "src/a.cc:3: warn: Dispatching a workgroup to CU 0: WG 0",
            ),
            (
                "12: dispatcher: notify WgCompl 0",
                "src/a.cc:4: warn: dispatcher: notify WgCompl 0",
            ),
            (
                "13: dispatcher: Completed kernel 0",
                "src/a.cc:5: warn: dispatcher: Completed kernel 0",
            ),
        )
        for original, quoted in replacements:
            self.assertIn(original, text)
            text = text.replace(original, quoted)
        gem5_log.write_text(text, encoding="utf-8")
        synchronize_strict_gem5_hash(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        gpu_evidence.write_text(
            "schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu\n"
            "COSIM_GPU_EVIDENCE_V1\tunit-001\t0\t0\t"
            "session_start\t-1\t-1\t-1\t-1\n",
            encoding="ascii",
        )
        update_metadata(
            self.root,
            gem5_evidence_start_seq="0",
            gem5_evidence_end_seq="0",
        )
        synchronize_strict_gpu_evidence_hash(self.root)

        result = self.assert_failed_for("gpu_execution_unproven")
        analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertEqual(0, analysis["client_connected_count"])
        self.assertFalse(analysis["gpu_sequence"]["ok"])

    def test_strict_gem5_client_must_precede_test_window(self) -> None:
        write_strict_gem5_evidence(self.root)
        update_metadata(self.root, gem5_evidence_start_seq="0")
        result = self.assert_failed_for("gpu_execution_unproven")
        analysis = result["checks"]["gem5_gpu_execution"]["analysis"]
        self.assertEqual([], analysis["timestamp_regression_lines"])
        self.assertFalse(analysis["causal_chain_ok"])

    def test_strict_malformed_timestamp_fails_closed(self) -> None:
        write_strict_gem5_evidence(self.root)
        with (self.root / "gem5.log").open("a", encoding="utf-8") as handle:
            handle.write("not-a-timestamp warn: hidden line\n")
        update_metadata(
            self.root,
            gem5_log_sha256=hashlib.sha256(
                (self.root / "gem5.log").read_bytes()
            ).hexdigest(),
        )
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_missing_workgroup_dispatch_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        text = gpu_evidence.read_text(encoding="ascii")
        text = "\n".join(
            line for line in text.splitlines()
            if "workgroup_dispatch" not in line
        ) + "\n"
        text = text.replace("\t4\t12\tworkgroup_complete", "\t3\t12\tworkgroup_complete")
        text = text.replace("\t5\t13\tkernel_complete", "\t4\t13\tkernel_complete")
        gpu_evidence.write_text(text, encoding="ascii")
        update_metadata(
            self.root,
            gem5_evidence_end_seq="4",
        )
        synchronize_strict_gpu_evidence_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_missing_kernel_completion_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        gpu_evidence.write_text(
            "\n".join(
                line
                for line in gpu_evidence.read_text(encoding="ascii").splitlines()
                if "kernel_complete" not in line
            )
            + "\n",
            encoding="ascii",
        )
        update_metadata(self.root, gem5_evidence_end_seq="4")
        synchronize_strict_gpu_evidence_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_wrong_kernel_completion_id_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        gpu_evidence.write_text(
            gpu_evidence.read_text(encoding="ascii").replace(
                "kernel_complete\t0\t0\t-1\t-1",
                "kernel_complete\t0\t1\t-1\t-1",
            ),
            encoding="ascii",
        )
        synchronize_strict_gpu_evidence_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_wrong_workgroup_completion_id_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        gpu_evidence.write_text(
            gpu_evidence.read_text(encoding="ascii").replace(
                "workgroup_complete\t0\t0\t0\t-1",
                "workgroup_complete\t0\t0\t99\t-1",
            ),
            encoding="ascii",
        )
        synchronize_strict_gpu_evidence_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_kernel_completion_before_workgroup_completion_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gpu_evidence = self.root / "gem5-evidence.tsv"
        lines = gpu_evidence.read_text(encoding="ascii").splitlines()
        completion = lines[5].replace("\t4\t12\t", "\t5\t13\t")
        kernel_completion = lines[6].replace("\t5\t13\t", "\t4\t12\t")
        gpu_evidence.write_text(
            "\n".join((*lines[:5], kernel_completion, completion)) + "\n",
            encoding="ascii",
        )
        synchronize_strict_gpu_evidence_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_cleanup_must_be_proven_successful(self) -> None:
        update_metadata(
            self.root, cleanup_status="failed", cleanup_exit_code="1"
        )
        self.assert_failed_for("cleanup_failure")

    def test_missing_cleanup_proof_is_incomplete(self) -> None:
        update_metadata(
            self.root, cleanup_status=None, cleanup_exit_code=None
        )
        self.assert_failed_for("evidence_incomplete")

    def test_required_raw_evidence_is_enforced(self) -> None:
        (self.root / "gem5.log").unlink()
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn("gem5_log", result["checks"]["required_evidence"]["missing"])

    def test_source_snapshot_must_be_replayable(self) -> None:
        (self.root / "patch" / "source-snapshot.txt").write_text(
            "error=not_a_git_repository\n", encoding="utf-8"
        )
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "source_snapshot:not_a_git_repository",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_binary_provenance_requires_valid_hash(self) -> None:
        provenance = self.root / "patch" / "binary-provenance.txt"
        provenance.write_text(
            "gem5_source_commit=abc\n"
            "gem5_binary=/tmp/gem5.opt\n"
            "gem5_sha256=not-a-sha\n",
            encoding="utf-8",
        )
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "binary_provenance:invalid_gem5_sha256",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_untracked_sources_require_archive(self) -> None:
        (self.root / "patch" / "untracked-files.txt").write_text(
            "src/new_file.cc\n", encoding="utf-8"
        )
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "untracked_archive", result["checks"]["required_evidence"]["missing"]
        )

    def test_tracked_changes_require_a_binary_patch(self) -> None:
        (self.root / "patch" / "gem5-status.txt").write_text(
            " M src/gpu-compute/gpu_command_processor.cc\n", encoding="utf-8"
        )
        result = self.assert_failed_for("evidence_incomplete")
        self.assertIn(
            "source_snapshot:tracked_changes_without_patch",
            result["checks"]["required_evidence"]["missing"],
        )

    def test_cli_writes_atomic_verdict_json(self) -> None:
        verdict = self.root / "verdict.json"
        completed = subprocess.run(
            (
                sys.executable,
                str(REPO_ROOT / "scripts" / "classify_runs.py"),
                "--artifact-dir",
                str(self.root),
                "--program",
                "vector_add",
                "--write-verdict",
                str(verdict),
                "--json",
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("PASS", json.loads(verdict.read_text())["outcome"])
        self.assertEqual("PASS", json.loads(completed.stdout)["outcome"])

    def test_cli_rejects_explicit_empty_program_as_argument_error(self) -> None:
        second_root = Path(self.temporary.name) / "second-run"
        write_complete_artifact(second_root)
        for artifact_dirs in ((self.root,), (self.root, second_root)):
            with self.subTest(artifact_count=len(artifact_dirs)):
                command = [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "classify_runs.py"),
                ]
                for artifact_dir in artifact_dirs:
                    command.extend(("--artifact-dir", str(artifact_dir)))
                command.extend(("--program", "", "--json"))

                completed = subprocess.run(
                    command,
                    check=False,
                    capture_output=True,
                    text=True,
                )

                self.assertEqual(2, completed.returncode)
                self.assertEqual("", completed.stdout)
                self.assertIn("--program", completed.stderr)
                self.assertIn("[a-z0-9_]{1,128}", completed.stderr)


class ArtifactAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "task"
        self.run = self.root / "tests" / "vector_add-run-1"
        self.run.mkdir(parents=True)
        write_complete_artifact(self.run)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def finalize_row(self) -> None:
        verdict = classify_artifact(self.run, "vector_add")
        write_json_atomic(self.run / "verdict.json", verdict)
        (self.run / "matrix.tsv").write_text(
            "program\thsa_interrupt\trun\tsession_id\toutcome\texit_code\treason\tartifact_dir\n"
            f"vector_add\t1\t1\tunit-001\t{verdict['outcome']}\t"
            f"{verdict['exit_code']}\t{verdict['reason']}\t{self.run}\n",
            encoding="utf-8",
        )

    def read_tsv(self, out: Path, name: str) -> list[dict[str, str]]:
        with (out / name).open(encoding="utf-8", newline="") as handle:
            return list(csv.DictReader(handle, delimiter="\t"))

    def test_auditor_emits_full_compact_table_contract(self) -> None:
        self.finalize_row()
        out = self.root / "audit"
        summary = audit(self.root, out)
        self.assertEqual(1, summary["artifact_rows"])
        self.assertEqual(set(TABLE_HEADERS), {path.name for path in out.iterdir()})
        status = self.read_tsv(out, "row_status.tsv")
        self.assertEqual("complete", status[0]["status"])
        verdicts = self.read_tsv(out, "verdicts.tsv")
        self.assertEqual("PASS", verdicts[0]["outcome"])

    def test_unknown_nonpass_tail_remains_visible_and_queued(self) -> None:
        update_metadata(
            self.run, category="test_fail", test_exit_code="7"
        )
        (self.run / "qemu.log").write_text(
            "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1\n"
            "mystery packet 0x42 reached an unknown state\n",
            encoding="utf-8",
        )
        self.finalize_row()
        out = self.root / "audit"
        audit(self.root, out)
        candidates = self.read_tsv(out, "candidate_events.tsv")
        self.assertTrue(
            any("mystery packet 0x42" in row["text"] for row in candidates)
        )
        queue = self.read_tsv(out, "review_queue.tsv")
        self.assertTrue(
            any(row["issue"] == "nonpass_unclassified" for row in queue)
        )
        reads = self.read_tsv(out, "raw_read_plan.tsv")
        self.assertTrue(any(row["reason"] == "candidate_event" for row in reads))

    def test_filter_coverage_self_reports_missing_dimensions(self) -> None:
        update_metadata(self.run, diagnostic_filter="queue=0-3")
        self.finalize_row()
        out = self.root / "audit"
        audit(self.root, out)
        coverage = self.read_tsv(out, "filter_coverage.tsv")
        self.assertEqual("coverage_insufficient", coverage[0]["status"])
        queue = self.read_tsv(out, "review_queue.tsv")
        self.assertTrue(any(row["issue"] == "coverage_insufficient" for row in queue))


if __name__ == "__main__":
    unittest.main()
