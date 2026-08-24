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

from scripts.verify_cosim_matrix import SCHEMA, verify_matrix  # noqa: E402


MANIFEST_FIELDS = (
    "row_id",
    "program",
    "program_source",
    "source_sha256",
    "program_binary",
    "runner_argument",
    "mode",
    "repeat_count",
    "timeout_policy",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
    "guest_test_prefix",
    "expected_hsa_interrupt",
    "gem5_binary",
    "gem5_config_args",
    "output_dir",
    "artifact_dir",
    "artifact_dir_pattern",
    "matrix_path",
    "provenance_file",
    "guest_bridge_policy",
    "status",
)
MATRIX_FIELDS = (
    "row_id",
    "program",
    "program_source",
    "source_sha256",
    "hsa_interrupt",
    "run",
    "session_id",
    "outcome",
    "exit_code",
    "reason",
    "artifact_dir",
    "verdict_artifact",
    "qemu_log",
    "gem5_log",
    "cleanup_status",
    "gem5_source_commit",
    "gem5_binary",
    "gem5_sha256",
    "test_binary",
    "test_binary_sha256",
    "source_fingerprint",
)
LOCAL_MATRIX_FIELDS = (
    "program",
    "hsa_interrupt",
    "run",
    "session_id",
    "outcome",
    "exit_code",
    "reason",
    "artifact_dir",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def staging_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    files = sorted(
        (
            path
            for path in root.rglob("*")
            if path.is_file()
            and path.relative_to(root).parts[0] != "build"
            and not path.name.startswith(".cosim_guest_run.")
        ),
        key=lambda path: path.relative_to(root).as_posix(),
    )
    for path in files:
        relative = path.relative_to(root).as_posix()
        digest.update(f"{sha256(path)}  ./{relative}\n".encode())
    return digest.hexdigest()


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


class MatrixFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact = root / "artifacts" / "run-vector"
        self.patch = self.artifact / "patch"
        self.staging = self.artifact / "staging"
        self.source = root / "tests" / "kernels" / "vector_add.cpp"
        self.test_binary = self.staging / "build" / "vector_add"
        self.gem5_binary = root / "gem5" / "build" / "gem5.opt"
        self.manifest = root / "run-manifest.tsv"
        self.matrix = root / "matrix.tsv"
        self.output = root / "verification.json"
        self.manifest_rows: list[dict[str, str]] = []
        self.matrix_rows: list[dict[str, str]] = []
        self._create()

    def _create(self) -> None:
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "run_cosim_tests.sh").write_text(
            "#!/bin/bash\necho runner\n", encoding="utf-8"
        )
        (self.root / "scripts" / "cosim_guest_env.sh").write_text(
            "#!/bin/bash\necho helper\n", encoding="utf-8"
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_text("int vector_add_source = 1;\n", encoding="utf-8")
        self.patch.mkdir(parents=True)
        (self.staging / "kernels").mkdir(parents=True)
        (self.staging / "build").mkdir(parents=True)
        (self.staging / "kernels" / "vector_add.cpp").write_bytes(
            self.source.read_bytes()
        )
        self.test_binary.write_bytes(b"synthetic hip binary\n")
        self.gem5_binary.parent.mkdir(parents=True)
        self.gem5_binary.write_bytes(b"synthetic gem5 binary\n")

        repo_patch = self.patch / "repo.patch"
        untracked_list = self.patch / "repo-untracked-files.txt"
        repo_patch.write_bytes(b"")
        untracked_list.write_bytes(b"")
        (self.patch / "source-snapshot.txt").write_text(
            "\n".join(
                (
                    f"head_commit={'a' * 40}",
                    f"source_fingerprint={staging_fingerprint(self.staging)}",
                    "program=vector_add",
                    f"runner_sha256={sha256(self.root / 'scripts' / 'run_cosim_tests.sh')}",
                    "guest_env_helper_sha256="
                    f"{sha256(self.root / 'scripts' / 'cosim_guest_env.sh')}",
                    f"repo_patch_sha256={sha256(repo_patch)}",
                    f"repo_untracked_list_sha256={sha256(untracked_list)}",
                    "repo_untracked_archive_sha256=none",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.patch / "binary-provenance.txt").write_text(
            "\n".join(
                (
                    f"gem5_source_commit={'b' * 40}",
                    f"gem5_binary={self.gem5_binary}",
                    f"gem5_sha256={sha256(self.gem5_binary)}",
                    f"test_binary={self.test_binary}",
                    f"test_binary_sha256={sha256(self.test_binary)}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "runner-metadata.txt").write_text(
            "\n".join(
                (
                    "run_id=unit-vector",
                    "category=test_pass",
                    "program=vector_add",
                    "test=vector_add",
                    "program_source=tests/kernels/vector_add.cpp",
                    "program_binary=tests/build/vector_add",
                    "runner_argument=vector_add",
                    "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                    "expected_hsa_enable_interrupt=0",
                    "compile_exit_code=0",
                    "test_exit_code=0",
                    "exit_code=0",
                    "pass_count=1",
                    "fail_count=0",
                    f"source_snapshot={self.patch / 'source-snapshot.txt'}",
                    "cleanup_status=verified",
                    "cleanup_exit_code=0",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "qemu.log").write_text(
            "[COSIM_ENV] HSA_ENABLE_INTERRUPT=0\n[PASS] vector_add\n",
            encoding="utf-8",
        )
        (self.artifact / "gem5.log").write_text(
            "gem5 GPU execution\n", encoding="utf-8"
        )
        (self.artifact / "cleanup-status.txt").write_text(
            "result=PASS\nprimary_category=test_pass\nsecondary_category=none\n",
            encoding="utf-8",
        )

        verdict = {
            "artifact_dir": str(self.artifact),
            "checks": {
                "binary_provenance": {"ok": True},
                "cleanup": {"exit_code": 0, "ok": True, "status": "verified"},
                "compile": {"exit_code": 0, "ok": True},
                "effective_environment": {
                    "hsa_enable_interrupt": "0",
                    "ok": True,
                },
                "markers": {
                    "exact_pass_count": 1,
                    "fail_count": 0,
                    "ok": True,
                },
                "program_identity": {
                    "ok": True,
                    "program_binary": "tests/build/vector_add",
                    "program_source": "tests/kernels/vector_add.cpp",
                    "recorded_program": "vector_add",
                    "requested_program": "vector_add",
                    "runner_argument": "vector_add",
                },
                "required_evidence": {"missing": [], "ok": True},
                "simulator_lifetime": {
                    "early_exit_observed": False,
                    "ok": True,
                },
                "source_snapshot": {"ok": True},
                "test_exit": {"exit_code": 0, "ok": True},
                "timeout": {"observed": False, "ok": True},
            },
            "evidence": {
                "binary_provenance": str(self.patch / "binary-provenance.txt"),
                "gem5_log": str(self.artifact / "gem5.log"),
                "guest_log": str(self.artifact / "qemu.log"),
                "metadata": str(self.artifact / "runner-metadata.txt"),
                "qemu_log": str(self.artifact / "qemu.log"),
                "source_snapshot": str(self.patch / "source-snapshot.txt"),
            },
            "exit_code": 0,
            "outcome": "PASS",
            "program": "vector_add",
            "provenance": {
                "gem5_binary": str(self.gem5_binary),
                "gem5_sha256": sha256(self.gem5_binary),
                "gem5_source_commit": "b" * 40,
            },
            "reason": "all_acceptance_gates_passed",
            "reasons": [],
            "schema": "cosim-run-verdict/v1",
        }
        (self.artifact / "verdict.json").write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        local_row = {
            "program": "vector_add",
            "hsa_interrupt": "0",
            "run": "1",
            "session_id": "unit-vector",
            "outcome": "PASS",
            "exit_code": "0",
            "reason": "all_acceptance_gates_passed",
            "artifact_dir": str(self.artifact),
        }
        write_tsv(
            self.artifact / "matrix.tsv", LOCAL_MATRIX_FIELDS, [local_row]
        )

        accepted = {
            "row_id": "accepted-vector",
            "program": "vector_add",
            "program_source": "tests/kernels/vector_add.cpp",
            "source_sha256": sha256(self.source),
            "program_binary": "tests/build/vector_add",
            "runner_argument": "vector_add",
            "mode": "pure_test",
            "repeat_count": "1",
            "timeout_policy": "fixed-30",
            "boot_timeout": "240",
            "test_timeout": "30",
            "guest_run_timeout": "1800",
            "guest_test_prefix": "HSA_ENABLE_INTERRUPT=0",
            "expected_hsa_interrupt": "0",
            "gem5_binary": str(self.gem5_binary),
            "gem5_config_args": "defaults",
            "output_dir": str(self.artifact),
            "artifact_dir": str(self.artifact),
            "artifact_dir_pattern": "-",
            "matrix_path": str(self.artifact / "matrix.tsv"),
            "provenance_file": str(self.patch / "binary-provenance.txt"),
            "guest_bridge_policy": "artifact-local",
            "status": "accepted",
        }
        superseded = dict(accepted)
        superseded.update(
            {
                "row_id": "old-vector",
                "artifact_dir": str(self.root / "missing-old-run"),
                "output_dir": str(self.root / "missing-old-run"),
                "matrix_path": str(self.root / "missing-old-run" / "matrix.tsv"),
                "provenance_file": str(
                    self.root / "missing-old-run" / "patch" / "binary-provenance.txt"
                ),
                "status": "superseded_old_contract",
            }
        )
        self.manifest_rows = [accepted, superseded]

        snapshot = self.read_snapshot()
        top = {
            "row_id": "accepted-vector",
            "program": "vector_add",
            "program_source": "tests/kernels/vector_add.cpp",
            "source_sha256": sha256(self.source),
            "hsa_interrupt": "0",
            "run": "1",
            "session_id": "unit-vector",
            "outcome": "PASS",
            "exit_code": "0",
            "reason": "all_acceptance_gates_passed",
            "artifact_dir": str(self.artifact),
            "verdict_artifact": str(self.artifact / "verdict.json"),
            "qemu_log": str(self.artifact / "qemu.log"),
            "gem5_log": str(self.artifact / "gem5.log"),
            "cleanup_status": "verified",
            "gem5_source_commit": "b" * 40,
            "gem5_binary": str(self.gem5_binary),
            "gem5_sha256": sha256(self.gem5_binary),
            "test_binary": str(self.test_binary),
            "test_binary_sha256": sha256(self.test_binary),
            "source_fingerprint": snapshot["source_fingerprint"],
        }
        self.matrix_rows = [top]
        self.flush()

    def read_snapshot(self) -> dict[str, str]:
        values = {}
        for line in (self.patch / "source-snapshot.txt").read_text().splitlines():
            if "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        return values

    def flush(self) -> None:
        write_tsv(self.manifest, MANIFEST_FIELDS, self.manifest_rows)
        write_tsv(self.matrix, MATRIX_FIELDS, self.matrix_rows)


def error_codes(result: dict[str, object]) -> set[str]:
    codes = {error["code"] for error in result["errors"]}
    for row in result["rows"]:
        codes.update(error["code"] for error in row["errors"])
    return codes


def error_details(result: dict[str, object]) -> list[str]:
    details = [error["detail"] for error in result["errors"]]
    for row in result["rows"]:
        details.extend(error["detail"] for error in row["errors"])
    return details


class VerifyCosimMatrixTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = MatrixFixture(Path(self.temporary.name) / "workspace")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def verify(self) -> dict[str, object]:
        return verify_matrix(
            self.fixture.manifest, self.fixture.matrix, self.fixture.root
        )

    def test_complete_join_passes_and_superseded_row_is_ignored(self) -> None:
        result = self.verify()
        self.assertEqual(SCHEMA, result["schema"])
        self.assertEqual("PASS", result["outcome"])
        self.assertEqual(1, result["accepted_row_count"])
        self.assertEqual("PASS", result["rows"][0]["verification_outcome"])
        self.assertEqual(
            [{"row_id": "old-vector", "status": "superseded_old_contract"}],
            result["ignored_rows"],
        )

        completed = subprocess.run(
            (
                sys.executable,
                str(REPO_ROOT / "scripts" / "verify_cosim_matrix.py"),
                "--manifest",
                str(self.fixture.manifest),
                "--matrix",
                str(self.fixture.matrix),
                "--output",
                str(self.fixture.output),
                "--repo-root",
                str(self.fixture.root),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        written = json.loads(self.fixture.output.read_text(encoding="utf-8"))
        self.assertEqual(result, written)

    def test_missing_accepted_artifact_fails(self) -> None:
        missing = self.fixture.root / "missing-accepted-run"
        row = self.fixture.manifest_rows[0]
        row["artifact_dir"] = str(missing)
        row["output_dir"] = str(missing)
        row["matrix_path"] = str(missing / "matrix.tsv")
        row["provenance_file"] = str(missing / "patch" / "binary-provenance.txt")
        self.fixture.matrix_rows[0]["artifact_dir"] = str(missing)
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("artifact_missing", error_codes(result))

        completed = subprocess.run(
            (
                sys.executable,
                str(REPO_ROOT / "scripts" / "verify_cosim_matrix.py"),
                "--manifest",
                str(self.fixture.manifest),
                "--matrix",
                str(self.fixture.matrix),
                "--output",
                str(self.fixture.output),
                "--repo-root",
                str(self.fixture.root),
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, completed.returncode, completed.stderr)
        self.assertEqual(
            "FAIL",
            json.loads(self.fixture.output.read_text(encoding="utf-8"))["outcome"],
        )

    def test_duplicate_top_matrix_row_fails(self) -> None:
        self.fixture.matrix_rows.append(dict(self.fixture.matrix_rows[0]))
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("top_matrix_row_count", error_codes(result))
        self.assertIn("duplicate_matrix_artifact", error_codes(result))

    def test_mismatched_hsa_fails(self) -> None:
        self.fixture.matrix_rows[0]["hsa_interrupt"] = "1"
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any("top.hsa_interrupt" in detail for detail in error_details(result))
        )

    def test_source_hash_corruption_fails(self) -> None:
        self.fixture.source.write_text(
            "int vector_add_source = 2;\n", encoding="utf-8"
        )
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))
        self.assertTrue(
            any("manifest.source_sha256" in detail for detail in error_details(result))
        )

    def test_repository_provenance_corruption_fails(self) -> None:
        (self.fixture.patch / "repo.patch").write_text(
            "corrupted patch\n", encoding="utf-8"
        )
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))
        self.assertTrue(
            any("repo_patch_sha256" in detail for detail in error_details(result))
        )

    def test_binary_corruption_fails(self) -> None:
        self.fixture.test_binary.write_bytes(b"corrupted binary\n")
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))
        self.assertTrue(
            any("test_binary_sha256" in detail for detail in error_details(result))
        )

    def test_path_mismatch_fails(self) -> None:
        wrong = self.fixture.artifact / "wrong-qemu.log"
        wrong.write_text("[COSIM_ENV] HSA_ENABLE_INTERRUPT=0\n", encoding="utf-8")
        self.fixture.matrix_rows[0]["qemu_log"] = str(wrong)
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("path_mismatch", error_codes(result))


if __name__ == "__main__":
    unittest.main()
