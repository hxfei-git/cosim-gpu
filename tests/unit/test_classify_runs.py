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
    completion_token_run_sha256,
    parse_rfc3339nano,
    split_docker_timestamp,
)
from scripts.cosim_artifact_audit import TABLE_HEADERS, audit  # noqa: E402


def write_complete_artifact(root: Path, program: str = "vector_add") -> None:
    patch_dir = root / "patch"
    patch_dir.mkdir(parents=True)
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
        f"gem5_sha256={'a' * 64}\n",
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


def write_strict_qemu_evidence(
    root: Path,
    *,
    program: str = "vector_add",
    run_id: str = "unit-001",
    signal_number: int | None = None,
) -> None:
    run_sha256 = completion_token_run_sha256(run_id)
    lines = [
        f"  Run-ID:     {run_id}",
        "[COSIM_ENV] HSA_ENABLE_INTERRUPT=1",
        "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=60",
        f"__COSIM_COMPILE_DONE_{program}_{run_sha256}__:0",
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
        qemu_log_sha256=hashlib.sha256(qemu_log.read_bytes()).hexdigest(),
        strict_acceptance="1",
        test_timeout="60",
    )


def synchronize_strict_qemu_hash(root: Path) -> None:
    update_metadata(
        root,
        qemu_log_sha256=hashlib.sha256((root / "qemu.log").read_bytes()).hexdigest(),
    )


def write_strict_gem5_evidence(root: Path) -> None:
    write_strict_qemu_evidence(root)
    started = "2026-08-26T00:00:01.000000000Z"
    finished = "2026-08-26T00:00:02.000000000Z"
    gem5_log = root / "gem5.log"
    gem5_log.write_text(
        "2026-08-26T00:00:00.100000000Z command line: "
        "/gem5/build/VEGA_X86/gem5.opt "
        "--debug-flags=HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo "
        "--socket-path=/tmp/gem5-mi300x-unit-001.sock "
        "--shmem-path=/mi300x-vram-unit-001 "
        "--shmem-host-path=/cosim-guest-ram-unit-001\n"
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
    digest = hashlib.sha256(gem5_log.read_bytes()).hexdigest()
    update_metadata(
        root,
        strict_acceptance="1",
        guest_test_started_at=started,
        guest_test_finished_at=finished,
        gem5_log_sha256=digest,
    )


def synchronize_strict_gem5_hash(root: Path) -> None:
    update_metadata(
        root,
        gem5_log_sha256=hashlib.sha256((root / "gem5.log").read_bytes()).hexdigest(),
    )


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

    def test_requested_identity_must_match_all_recorded_identity(self) -> None:
        update_metadata(self.root, runner_argument="reduction")
        self.assert_failed_for("program_identity_mismatch")

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

    def test_nonzero_test_exit_fails(self) -> None:
        update_metadata(self.root, test_exit_code="9", category="test_fail")
        self.assert_failed_for("nonzero_test_exit")

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
            "run<hsa<timeout<compile<pass<test",
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

    def test_strict_gem5_command_must_precede_client_and_gpu(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        lines = gem5_log.read_text(encoding="utf-8").splitlines()
        command = lines[0].replace("00.100000000Z", "00.200000000Z")
        client = lines[1].replace("00.200000000Z", "00.100000000Z")
        gem5_log.write_text(
            "\n".join((client, command, *lines[2:])) + "\n",
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        result = self.assert_failed_for("gpu_execution_unproven")
        self.assertFalse(
            result["checks"]["gem5_gpu_execution"]["analysis"][
                "causal_chain_ok"
            ]
        )

    def test_strict_gem5_client_must_precede_test_window(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "2026-08-26T00:00:00.200000000Z src/dev",
                "2026-08-26T00:00:01.050000000Z src/dev",
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
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
        gem5_log = self.root / "gem5.log"
        text = "\n".join(
            line
            for line in gem5_log.read_text(encoding="utf-8").splitlines()
            if "Dispatching a workgroup" not in line
        ) + "\n"
        gem5_log.write_text(text, encoding="utf-8")
        update_metadata(
            self.root,
            gem5_log_sha256=hashlib.sha256(gem5_log.read_bytes()).hexdigest(),
        )
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_missing_kernel_completion_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            "\n".join(
                line
                for line in gem5_log.read_text(encoding="utf-8").splitlines()
                if "Completed kernel" not in line
            )
            + "\n",
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_wrong_kernel_completion_id_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "Completed kernel 0", "Completed kernel 1"
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_wrong_workgroup_completion_id_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "notify WgCompl 0", "notify WgCompl 99"
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
        self.assert_failed_for("gpu_execution_unproven")

    def test_strict_kernel_completion_before_workgroup_completion_fails(self) -> None:
        write_strict_gem5_evidence(self.root)
        gem5_log = self.root / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "2026-08-26T00:00:01.300000000Z 12: "
                "dispatcher: notify WgCompl 0\n"
                "2026-08-26T00:00:01.400000000Z 13: "
                "dispatcher: Completed kernel 0\n",
                "2026-08-26T00:00:01.300000000Z 12: "
                "dispatcher: Completed kernel 0\n"
                "2026-08-26T00:00:01.400000000Z 13: "
                "dispatcher: notify WgCompl 0\n",
            ),
            encoding="utf-8",
        )
        synchronize_strict_gem5_hash(self.root)
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
