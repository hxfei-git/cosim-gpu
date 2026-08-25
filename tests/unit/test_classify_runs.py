#!/usr/bin/env python3

from __future__ import annotations

import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.classify_runs import classify_artifact, write_json_atomic  # noqa: E402
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
