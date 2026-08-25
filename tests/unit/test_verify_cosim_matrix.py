#!/usr/bin/env python3

from __future__ import annotations

import csv
import hashlib
import json
import subprocess
import sys
import tarfile
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
    "strict_acceptance",
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
    "strict_acceptance",
    "mode",
    "repeat_count",
    "timeout_policy",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
    "guest_test_prefix",
    "gem5_config_args",
    "artifact_dir_pattern",
    "guest_bridge_policy",
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
    "strict_acceptance",
    "boot_timeout",
    "test_timeout",
    "guest_run_timeout",
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


def synthetic_hip_executable() -> bytes:
    header = bytearray(64)
    header[:7] = b"\x7fELF\x02\x01\x01"
    header[16:18] = (2).to_bytes(2, "little")
    header[18:20] = (62).to_bytes(2, "little")
    return bytes(header) + (
        b"__CLANG_OFFLOAD_BUNDLE__\x00"
        b"hipv4-amdgcn-amd-amdhsa--gfx942\x00"
    )


def write_tsv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=fields, delimiter="\t", lineterminator="\n"
        )
        writer.writeheader()
        writer.writerows(rows)


def replace_key_value(path: Path, key: str, value: str) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    replaced = False
    output = []
    for line in lines:
        if line.startswith(f"{key}="):
            output.append(f"{key}={value}")
            replaced = True
        else:
            output.append(line)
    if not replaced:
        raise AssertionError(f"missing fixture key {key} in {path}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")


class MatrixFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact = root / "artifacts" / "run-vector"
        self.patch = self.artifact / "patch"
        self.staging = self.artifact / "staging"
        self.source = root / "tests" / "kernels" / "vector_add.cpp"
        self.test_binary = self.staging / "build" / "vector_add"
        self.gem5_binary = root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
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
        (self.root / "scripts" / "cosim_launch.sh").write_text(
            "#!/bin/bash\necho launcher\n", encoding="utf-8"
        )
        (self.root / "scripts" / "cosim_build.sh").write_text(
            "#!/bin/bash\n"
            f"source_fingerprint() {{ printf '%s\\n' {'c' * 64!r}; }}\n",
            encoding="utf-8",
        )
        (self.root / "scripts" / "Dockerfile.run").write_text(
            "FROM synthetic.invalid/gem5-run\n",
            encoding="utf-8",
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_text("int vector_add_source = 1;\n", encoding="utf-8")
        self.patch.mkdir(parents=True)
        (self.staging / "kernels").mkdir(parents=True)
        (self.staging / "build").mkdir(parents=True)
        (self.staging / "kernels" / "vector_add.cpp").write_bytes(
            self.source.read_bytes()
        )
        self.test_binary.write_bytes(synthetic_hip_executable())
        self.test_binary.chmod(0o755)
        self.gem5_binary.parent.mkdir(parents=True)
        self.gem5_binary.write_bytes(b"synthetic gem5 binary\n")
        self.gem5_binary.chmod(0o755)

        (self.root / "gem5" / ".gitignore").write_text(
            "/build/\n", encoding="utf-8"
        )
        (self.root / "gem5" / "model.cc").write_text(
            "int synthetic_model = 1;\n", encoding="utf-8"
        )
        subprocess.run(
            ("git", "init", "-q", str(self.root / "gem5")), check=True
        )
        subprocess.run(
            ("git", "-C", str(self.root / "gem5"), "config", "user.name", "Test"),
            check=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(self.root / "gem5"),
                "config",
                "user.email",
                "test@example.invalid",
            ),
            check=True,
        )
        subprocess.run(
            ("git", "-C", str(self.root / "gem5"), "add", "."), check=True
        )
        subprocess.run(
            ("git", "-C", str(self.root / "gem5"), "commit", "-qm", "fixture"),
            check=True,
        )
        self.gem5_commit = subprocess.check_output(
            ("git", "-C", str(self.root / "gem5"), "rev-parse", "HEAD"),
            text=True,
        ).strip()

        gem5_status = self.patch / "gem5-status.txt"
        gem5_patch = self.patch / "gem5.patch"
        gem5_untracked_list = self.patch / "untracked-files.txt"
        gem5_build_meta = self.patch / "gem5-build-meta.txt"
        gem5_status.write_bytes(b"")
        gem5_patch.write_bytes(b"")
        gem5_untracked_list.write_bytes(b"")
        gem5_build_meta.write_text(
            "\n".join(
                (
                    f"commit={self.gem5_commit}",
                    "source_fingerprint_algorithm=2",
                    f"source_fingerprint={'c' * 64}",
                    "docker_build_recipe_fingerprint="
                    f"{sha256(self.root / 'scripts' / 'Dockerfile.run')}",
                    "timestamp=2026-01-01T00:00:00Z",
                    "target=VEGA_X86",
                    f"binary={self.gem5_binary}",
                    f"binary_sha256={sha256(self.gem5_binary)}",
                    f"docker_image=sha256:{'e' * 64}",
                    "",
                )
            ),
            encoding="utf-8",
        )

        current_lock = self.root / "configs" / "cosim" / "gem5-baseline.lock"
        current_lock.parent.mkdir(parents=True)
        current_lock.write_text(
            "\n".join(
                (
                    "schema=1",
                    f"gem5_commit={self.gem5_commit}",
                    "source_fingerprint_algorithm=2",
                    f"source_fingerprint={'c' * 64}",
                    f"binary_sha256={sha256(self.gem5_binary)}",
                    f"docker_image=sha256:{'e' * 64}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        gem5_baseline_lock = self.patch / "gem5-baseline.lock"
        gem5_baseline_lock.write_bytes(current_lock.read_bytes())

        self.qemu_binary = (
            self.root / ".local/cosim/qemu/10.1.5/bin/qemu-system-x86_64"
        )
        self.qemu_binary.parent.mkdir(parents=True)
        self.qemu_binary.write_bytes(b"synthetic qemu binary\n")
        self.qemu_binary.chmod(0o755)
        self.disk_image = (
            self.root
            / "gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70"
        )
        self.disk_image.parent.mkdir(parents=True)
        self.disk_image.write_bytes(b"synthetic guest disk\n")
        self.kernel = (
            self.root
            / "gem5-resources/src/x86-ubuntu-gpu-ml/vmlinux-rocm70"
        )
        self.kernel.parent.mkdir(parents=True, exist_ok=True)
        self.kernel.write_bytes(b"synthetic guest kernel\n")

        (self.root / ".gitignore").write_text(
            "/.local/\n/artifacts/\n/run-manifest.tsv\n/matrix.tsv\n",
            encoding="utf-8",
        )
        subprocess.run(("git", "init", "-q", str(self.root)), check=True)
        subprocess.run(
            ("git", "-C", str(self.root), "config", "user.name", "Test"),
            check=True,
        )
        subprocess.run(
            (
                "git",
                "-C",
                str(self.root),
                "config",
                "user.email",
                "test@example.invalid",
            ),
            check=True,
        )
        subprocess.run(
            ("git", "-C", str(self.root), "add", "."),
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            ("git", "-C", str(self.root), "commit", "-qm", "fixture"),
            check=True,
        )
        self.head_commit = subprocess.check_output(
            ("git", "-C", str(self.root), "rev-parse", "HEAD"), text=True
        ).strip()

        repo_patch = self.patch / "repo.patch"
        repo_status = self.patch / "repo-status.txt"
        untracked_list = self.patch / "repo-untracked-files.txt"
        repo_patch.write_bytes(b"")
        repo_status.write_bytes(b"")
        untracked_list.write_bytes(b"")
        (self.patch / "source-snapshot.txt").write_text(
            "\n".join(
                (
                    f"head_commit={self.head_commit}",
                    f"source_fingerprint={staging_fingerprint(self.staging)}",
                    "program=vector_add",
                    f"runner_sha256={sha256(self.root / 'scripts' / 'run_cosim_tests.sh')}",
                    "guest_env_helper_sha256="
                    f"{sha256(self.root / 'scripts' / 'cosim_guest_env.sh')}",
                    "launcher_sha256="
                    f"{sha256(self.root / 'scripts' / 'cosim_launch.sh')}",
                    "build_script_sha256="
                    f"{sha256(self.root / 'scripts' / 'cosim_build.sh')}",
                    f"repo_status_sha256={sha256(repo_status)}",
                    f"repo_patch_sha256={sha256(repo_patch)}",
                    f"repo_untracked_list_sha256={sha256(untracked_list)}",
                    "repo_untracked_archive_sha256=none",
                    f"gem5_source_commit={self.gem5_commit}",
                    f"gem5_source_fingerprint={'c' * 64}",
                    f"gem5_status_sha256={sha256(gem5_status)}",
                    f"gem5_patch_sha256={sha256(gem5_patch)}",
                    f"gem5_untracked_list_sha256={sha256(gem5_untracked_list)}",
                    "gem5_untracked_archive_sha256=none",
                    f"gem5_build_meta_sha256={sha256(gem5_build_meta)}",
                    f"gem5_baseline_lock_sha256={sha256(gem5_baseline_lock)}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.patch / "binary-provenance.txt").write_text(
            "\n".join(
                (
                    f"gem5_source_commit={self.gem5_commit}",
                    "gem5_source_subject=synthetic gem5 commit",
                    "gem5_source_fingerprint_algorithm=2",
                    f"gem5_source_fingerprint={'c' * 64}",
                    f"gem5_binary={self.gem5_binary}",
                    f"gem5_sha256={sha256(self.gem5_binary)}",
                    f"gem5_build_meta={gem5_build_meta}",
                    f"gem5_build_meta_sha256={sha256(gem5_build_meta)}",
                    f"gem5_baseline_lock={gem5_baseline_lock}",
                    f"gem5_baseline_lock_sha256={sha256(gem5_baseline_lock)}",
                    "gem5_docker_image_name=gem5-run:local",
                    f"gem5_docker_image=sha256:{'e' * 64}",
                    f"test_binary={self.test_binary}",
                    f"test_binary_sha256={sha256(self.test_binary)}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "runner-invocation.txt").write_text(
            "\n".join(
                (
                    "schema=cosim-runner-invocation/v1",
                    "run_id=unit-vector",
                    "program=vector_add",
                    "program_source=tests/kernels/vector_add.cpp",
                    "program_binary=tests/build/vector_add",
                    "runner_argument=vector_add",
                    "strict_acceptance=1",
                    "mode=pure_test",
                    "repeat_count=1",
                    "timeout_policy=fixed-30",
                    "boot_timeout=240",
                    "test_timeout=30",
                    "guest_run_timeout=1800",
                    "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                    "guest_test_prefix_input=",
                    "expected_hsa_interrupt=0",
                    f"gem5_binary={self.gem5_binary}",
                    "gem5_docker_image_name=gem5-run:local",
                    f"gem5_docker_image=sha256:{'e' * 64}",
                    "gem5_config_args=defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB",
                    f"output_dir={self.artifact}",
                    f"artifact_dir={self.artifact}",
                    "artifact_dir_pattern=-",
                    f"matrix_path={self.artifact / 'matrix.tsv'}",
                    f"provenance_file={self.patch / 'binary-provenance.txt'}",
                    "guest_bridge_policy=artifact-local",
                    f"guest_bridge_host={self.staging}",
                    "guest_bridge_guest=/mnt",
                    f"cwd={self.root}",
                    f"argv0={self.root / 'scripts' / 'run_cosim_tests.sh'}",
                    "argv=--boot-timeout 240 --test-timeout 30 "
                    "--guest-run-timeout 1800 "
                    f"--output-dir {self.artifact} vector_add",
                    "passthrough_args=",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "launch-invocation.txt").write_text(
            "\n".join(
                (
                    "schema=cosim-launch-invocation/v1",
                    "run_id=unit-vector",
                    f"artifact_dir={self.artifact}",
                    f"share_dir={self.staging}",
                    f"gem5_binary={self.gem5_binary}",
                    "gem5_container_binary=/gem5/build/VEGA_X86/gem5.opt",
                    "gem5_config_args=defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB",
                    "gem5_docker_image=gem5-run:local",
                    "qemu_binary="
                    f"{self.root / '.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64'}",
                    "disk_image="
                    f"{self.root / 'gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70'}",
                    "kernel="
                    f"{self.root / 'gem5-resources/src/x86-ubuntu-gpu-ml/vmlinux-rocm70'}",
                    "host_cpus=4",
                    "gem5_init_timeout=120",
                    f"cwd={self.root}",
                    f"argv0={self.root / 'scripts' / 'cosim_launch.sh'}",
                    f"argv=--share-dir {self.staging} "
                    f"--artifact-dir {self.artifact}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "guest-run.sh").write_text(
            "#!/bin/bash\n"
            "echo '[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=30'\n"
            'echo "__COSIM_COMPILE_DONE_vector_add_1__:${build_rc}"\n'
            "TEST_TIMEOUT_SECS=30 ./run_tests.sh vector_add\n"
            'echo "__COSIM_TEST_DONE_vector_add_1__:${rc}"\n',
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
                    "strict_acceptance=1",
                    "mode=pure_test",
                    "repeat_count=1",
                    "timeout_policy=fixed-30",
                    "boot_timeout=240",
                    "test_timeout=30",
                    "guest_run_timeout=1800",
                    "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                    "guest_test_prefix_input=",
                    "expected_hsa_enable_interrupt=0",
                    f"gem5_binary={self.gem5_binary}",
                    "gem5_docker_image_name=gem5-run:local",
                    f"gem5_docker_image=sha256:{'e' * 64}",
                    "gem5_config_args=defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB",
                    "artifact_dir_pattern=-",
                    "guest_bridge_policy=artifact-local",
                    f"guest_bridge_host={self.staging}",
                    "guest_bridge_guest=/mnt",
                    "compile_exit_code=0",
                    "test_exit_code=0",
                    "exit_code=0",
                    "pass_count=1",
                    "fail_count=0",
                    f"source_snapshot={self.patch / 'source-snapshot.txt'}",
                    f"gem5_baseline_lock={gem5_baseline_lock}",
                    f"gem5_baseline_lock_sha256={sha256(gem5_baseline_lock)}",
                    f"runner_invocation={self.artifact / 'runner-invocation.txt'}",
                    f"launch_invocation={self.artifact / 'launch-invocation.txt'}",
                    f"guest_script={self.artifact / 'guest-run.sh'}",
                    "cleanup_status=verified",
                    "cleanup_exit_code=0",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "qemu.log").write_text(
            "[COSIM_ENV] HSA_ENABLE_INTERRUPT=0\n"
            "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=30\n"
            "__COSIM_COMPILE_DONE_vector_add_1__:0\n"
            "[PASS] vector_add\n"
            "__COSIM_TEST_DONE_vector_add_1__:0\n",
            encoding="utf-8",
        )
        (self.artifact / "gem5.log").write_text(
            "gem5 GPU execution\n", encoding="utf-8"
        )
        (self.artifact / "cleanup-status.txt").write_text(
            "result=PASS\nprimary_category=test_pass\nsecondary_category=none\n",
            encoding="utf-8",
        )
        (self.artifact / "docker-inspect.json").write_text(
            json.dumps(
                [
                    {
                        "Image": f"sha256:{'e' * 64}",
                        "Config": {"Image": "gem5-run:local"},
                    }
                ],
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        overlay_path = Path("/tmp/cosim-unit-vector.session/guest-overlay.qcow2")
        (self.artifact / "guest-overlay.json").write_text(
            json.dumps(
                {
                    "backing-filename-format": "raw",
                    "backing-filename": str(self.disk_image),
                    "dirty-flag": False,
                    "filename": str(overlay_path),
                    "format": "qcow2",
                    "format-specific": {
                        "data": {"corrupt": False},
                        "type": "qcow2",
                    },
                    "full-backing-filename": str(self.disk_image),
                    "virtual-size": self.disk_image.stat().st_size,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        disk_mtime = subprocess.check_output(
            ("stat", "-c", "%y", str(self.disk_image)), text=True
        ).strip()
        (self.artifact / "guest-base-stat.txt").write_text(
            f"path={self.disk_image}\n"
            f"size={self.disk_image.stat().st_size}\n"
            f"mtime={disk_mtime}\n",
            encoding="utf-8",
        )
        preflight_dir = self.artifact / "preflight"
        preflight_dir.mkdir()
        preflight = {
            "schema": "cosim-preflight-v1",
            "profile": "run",
            "generated_at": "2026-01-01T00:00:00Z",
            "repo_root": str(self.root),
            "overall_status": "PASS",
            "required_failure_count": 0,
            "checks": [
                {
                    "id": "run.gem5_provenance",
                    "status": "PASS",
                    "required": True,
                    "summary": "synthetic",
                    "detail": str(gem5_build_meta),
                },
                {
                    "id": "run.qemu_provenance",
                    "status": "PASS",
                    "required": True,
                    "summary": "synthetic",
                    "detail": str(
                        self.root
                        / ".local/cosim/build/qemu-10.1.5/.cosim-build-meta"
                    ),
                },
            ],
        }
        (preflight_dir / "preflight.json").write_text(
            json.dumps(preflight, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (preflight_dir / "preflight.txt").write_text(
            "Cosim preflight: profile=run overall=PASS\n", encoding="utf-8"
        )
        (self.artifact / "preflight-resources.log").write_text(
            "=== Preflight Resource Audit ===\n=== End Preflight Audit ===\n",
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
                "gem5_source_commit": self.gem5_commit,
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
            "strict_acceptance": "1",
            "boot_timeout": "240",
            "test_timeout": "30",
            "guest_run_timeout": "1800",
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
            "strict_acceptance": "1",
            "mode": "pure_test",
            "repeat_count": "1",
            "timeout_policy": "fixed-30",
            "boot_timeout": "240",
            "test_timeout": "30",
            "guest_run_timeout": "1800",
            "guest_test_prefix": "HSA_ENABLE_INTERRUPT=0",
            "expected_hsa_interrupt": "0",
            "gem5_binary": str(self.gem5_binary),
            "gem5_config_args": "defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB",
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
            "gem5_source_commit": self.gem5_commit,
            "gem5_binary": str(self.gem5_binary),
            "gem5_sha256": sha256(self.gem5_binary),
            "test_binary": str(self.test_binary),
            "test_binary_sha256": sha256(self.test_binary),
            "source_fingerprint": snapshot["source_fingerprint"],
            "strict_acceptance": "1",
            "mode": "pure_test",
            "repeat_count": "1",
            "timeout_policy": "fixed-30",
            "boot_timeout": "240",
            "test_timeout": "30",
            "guest_run_timeout": "1800",
            "guest_test_prefix": "HSA_ENABLE_INTERRUPT=0",
            "gem5_config_args": "defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB",
            "artifact_dir_pattern": "-",
            "guest_bridge_policy": "artifact-local",
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

    def mutate_program_identity(self, field: str, value: str) -> None:
        previous = self.fixture.manifest_rows[0][field]
        self.fixture.manifest_rows[0][field] = value
        if field in self.fixture.matrix_rows[0]:
            self.fixture.matrix_rows[0][field] = value
        self.fixture.flush()
        for name in ("runner-invocation.txt", "runner-metadata.txt"):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    f"{field}={previous}", f"{field}={value}"
                ),
                encoding="utf-8",
            )
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["checks"]["program_identity"][field] = value
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    def set_hsa_mode(self, value: str) -> None:
        self.fixture.manifest_rows[0]["guest_test_prefix"] = (
            f"HSA_ENABLE_INTERRUPT={value}"
        )
        self.fixture.manifest_rows[0]["expected_hsa_interrupt"] = value
        self.fixture.matrix_rows[0]["guest_test_prefix"] = (
            f"HSA_ENABLE_INTERRUPT={value}"
        )
        self.fixture.matrix_rows[0]["hsa_interrupt"] = value
        self.fixture.flush()
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8")
            .replace(
                "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                f"guest_test_prefix=HSA_ENABLE_INTERRUPT={value}",
            )
            .replace("expected_hsa_interrupt=0", f"expected_hsa_interrupt={value}"),
            encoding="utf-8",
        )
        metadata = self.fixture.artifact / "runner-metadata.txt"
        metadata.write_text(
            metadata.read_text(encoding="utf-8")
            .replace(
                "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                f"guest_test_prefix=HSA_ENABLE_INTERRUPT={value}",
            )
            .replace(
                "expected_hsa_enable_interrupt=0",
                f"expected_hsa_enable_interrupt={value}",
            ),
            encoding="utf-8",
        )
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "HSA_ENABLE_INTERRUPT=0", f"HSA_ENABLE_INTERRUPT={value}"
            ),
            encoding="utf-8",
        )
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["checks"]["effective_environment"]["hsa_enable_interrupt"] = value
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        with (self.fixture.artifact / "matrix.tsv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            local_rows = list(csv.DictReader(handle, delimiter="\t"))
        local_rows[0]["hsa_interrupt"] = value
        write_tsv(
            self.fixture.artifact / "matrix.tsv", LOCAL_MATRIX_FIELDS, local_rows
        )

    def set_structured_gem5_config(self, value: str) -> None:
        previous = self.fixture.manifest_rows[0]["gem5_config_args"]
        self.fixture.manifest_rows[0]["gem5_config_args"] = value
        self.fixture.matrix_rows[0]["gem5_config_args"] = value
        self.fixture.flush()
        for name in (
            "runner-invocation.txt",
            "runner-metadata.txt",
            "launch-invocation.txt",
        ):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    f"gem5_config_args={previous}",
                    f"gem5_config_args={value}",
                ),
                encoding="utf-8",
            )

    def set_raw_passthrough(self, option: str, value: str) -> None:
        runner = self.fixture.artifact / "runner-invocation.txt"
        runner_text = runner.read_text(encoding="utf-8")
        runner_text = runner_text.replace(
            f"--output-dir {self.fixture.artifact} vector_add",
            f"--output-dir {self.fixture.artifact} {option} {value} vector_add",
        ).replace("passthrough_args=", f"passthrough_args= {option} {value}")
        runner.write_text(runner_text, encoding="utf-8")
        launcher = self.fixture.artifact / "launch-invocation.txt"
        launcher.write_text(
            launcher.read_text(encoding="utf-8").replace(
                f"--artifact-dir {self.fixture.artifact}",
                f"--artifact-dir {self.fixture.artifact} {option} {value}",
            ),
            encoding="utf-8",
        )

    def test_raw_gem5_config_override_without_structured_update_fails(self) -> None:
        self.set_raw_passthrough("--num-cus", "2")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "gem5_config_args_from_argv" in detail
                for detail in error_details(result)
            )
        )

    def test_runner_owned_passthrough_is_rejected_even_when_coordinated(self) -> None:
        self.set_raw_passthrough(
            "--share-dir", str(self.fixture.artifact / "wrong-staging")
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsupported_acceptance_passthrough", error_codes(result))

    def test_untracked_launcher_passthrough_is_not_accepted(self) -> None:
        self.set_raw_passthrough("--qemu-trace", "pci_*")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsupported_acceptance_passthrough", error_codes(result))

    def test_zero_compute_units_are_rejected_even_when_coordinated(self) -> None:
        self.set_raw_passthrough("--num-cus", "0")
        self.set_structured_gem5_config(
            "defaults:num-gpus=1,num-cus=0,host-mem=8G,vram-size=16GiB"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_config_value", error_codes(result))

    def test_gem5_binary_alias_argument_is_rejected(self) -> None:
        alias = self.fixture.gem5_binary.with_name("alias.opt")
        alias.symlink_to(self.fixture.gem5_binary)
        self.set_raw_passthrough("--gem5-bin", str(alias))

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_gem5_binary_argument", error_codes(result))

    def test_unsafe_run_id_is_rejected_when_coordinated(self) -> None:
        for name in (
            "runner-invocation.txt",
            "launch-invocation.txt",
            "runner-metadata.txt",
        ):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "run_id=unit-vector", "run_id=../unsafe"
                ),
                encoding="utf-8",
            )
        self.fixture.matrix_rows[0]["session_id"] = "../unsafe"
        self.fixture.flush()
        with (self.fixture.artifact / "matrix.tsv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        rows[0]["session_id"] = "../unsafe"
        write_tsv(self.fixture.artifact / "matrix.tsv", LOCAL_MATRIX_FIELDS, rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsafe_run_id", error_codes(result))

    def test_unsafe_session_name_is_rejected(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                f"--output-dir {self.fixture.artifact} vector_add",
                f"--output-dir {self.fixture.artifact} "
                "--session-name ../../unsafe vector_add",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsafe_session_name", error_codes(result))

    def test_timeout_leading_zero_is_rejected(self) -> None:
        self.fixture.manifest_rows[0]["boot_timeout"] = "0240"
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_manifest_timeout", error_codes(result))

    def test_run_ordinal_must_be_one(self) -> None:
        self.fixture.matrix_rows[0]["run"] = "999"
        self.fixture.flush()
        with (self.fixture.artifact / "matrix.tsv").open(
            "r", encoding="utf-8", newline=""
        ) as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        rows[0]["run"] = "999"
        write_tsv(self.fixture.artifact / "matrix.tsv", LOCAL_MATRIX_FIELDS, rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_run_ordinal", error_codes(result))

    def test_gem5_build_metadata_requires_full_producer_schema(self) -> None:
        build_meta = self.fixture.patch / "gem5-build-meta.txt"
        build_meta.write_text(
            "\n".join(
                line
                for line in build_meta.read_text(encoding="utf-8").splitlines()
                if not line.startswith("docker_image=")
            )
            + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_build_metadata", error_codes(result))

    def test_bash_ansi_c_quoted_argv_is_rejected(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                f"--output-dir {self.fixture.artifact} vector_add",
                f"--output-dir {self.fixture.artifact} "
                "--gem5-debug $'Foo\\tBar' vector_add",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsupported_bash_ansi_c_quoting", error_codes(result))

    def test_launch_runtime_identity_cannot_be_forged(self) -> None:
        invocation = self.fixture.artifact / "launch-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                "gem5_docker_image=gem5-run:local",
                "gem5_docker_image=forged:latest",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "launch_invocation.gem5_docker_image" in detail
                for detail in error_details(result)
            )
        )

    def test_staging_symlink_is_rejected(self) -> None:
        external_staging = self.fixture.root / "external-staging"
        self.fixture.staging.rename(external_staging)
        self.fixture.staging.symlink_to(external_staging, target_is_directory=True)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("symlink_not_allowed", error_codes(result))

    def test_test_binary_symlink_is_rejected(self) -> None:
        external_binary = self.fixture.root / "external-vector-add"
        external_binary.write_bytes(self.fixture.test_binary.read_bytes())
        external_binary.chmod(0o755)
        self.fixture.test_binary.unlink()
        self.fixture.test_binary.symlink_to(external_binary)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("symlink_not_allowed", error_codes(result))

    def test_nonexecutable_binaries_are_rejected(self) -> None:
        self.fixture.test_binary.chmod(0o644)
        self.fixture.gem5_binary.chmod(0o644)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("binary_not_executable", error_codes(result))

    def test_guest_completion_tokens_are_required(self) -> None:
        guest_script = self.fixture.artifact / "guest-run.sh"
        guest_script.write_text(
            "#!/bin/bash\n"
            "echo '[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=30'\n"
            "TEST_TIMEOUT_SECS=30 ./run_tests.sh vector_add\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_completion_token", error_codes(result))

    def test_noncanonical_screen_log_is_rejected(self) -> None:
        runner = self.fixture.artifact / "runner-invocation.txt"
        runner.write_text(
            runner.read_text(encoding="utf-8").replace(
                f"--output-dir {self.fixture.artifact}",
                f"--output-dir {self.fixture.artifact} "
                f"--screen-log {self.fixture.artifact / 'custom.log'}",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_screen_log", error_codes(result))

    def test_structured_gem5_config_cannot_override_raw_argv(self) -> None:
        self.set_structured_gem5_config(
            "defaults:num-gpus=1,num-cus=2,host-mem=8G,vram-size=16GiB"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "gem5_config_args_from_argv" in detail
                for detail in error_details(result)
            )
        )

    def test_coherent_raw_and_structured_gem5_config_passes(self) -> None:
        self.set_raw_passthrough("--num-cus", "2")
        self.set_structured_gem5_config(
            "defaults:num-gpus=1,num-cus=2,host-mem=8G,vram-size=16GiB"
        )

        result = self.verify()
        self.assertEqual("PASS", result["outcome"])

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

    def test_missing_gem5_replay_state_fails(self) -> None:
        (self.fixture.patch / "gem5.patch").unlink()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("missing_file", error_codes(result))

    def test_gem5_build_metadata_corruption_fails(self) -> None:
        build_meta = self.fixture.patch / "gem5-build-meta.txt"
        build_meta.write_text(
            build_meta.read_text(encoding="utf-8") + "forged=true\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))

    def test_gem5_untracked_list_requires_an_archive(self) -> None:
        untracked = self.fixture.patch / "untracked-files.txt"
        old_hash = sha256(untracked)
        untracked.write_text("local-model-change.cc\n", encoding="utf-8")
        snapshot = self.fixture.patch / "source-snapshot.txt"
        snapshot.write_text(
            snapshot.read_text(encoding="utf-8").replace(
                f"gem5_untracked_list_sha256={old_hash}",
                f"gem5_untracked_list_sha256={sha256(untracked)}",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("untracked_archive_missing", error_codes(result))

    def test_failed_run_preflight_cannot_be_accepted(self) -> None:
        preflight_path = self.fixture.artifact / "preflight" / "preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        preflight["overall_status"] = "FAIL"
        preflight["required_failure_count"] = 1
        preflight["checks"][0]["status"] = "FAIL"
        preflight_path.write_text(
            json.dumps(preflight, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("run_preflight_failed", error_codes(result))

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

    def test_manifest_schema_requires_mode(self) -> None:
        fields = tuple(field for field in MANIFEST_FIELDS if field != "mode")
        rows = [
            {field: value for field, value in row.items() if field in fields}
            for row in self.fixture.manifest_rows
        ]
        write_tsv(self.fixture.manifest, fields, rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("manifest_error", error_codes(result))

    def test_top_matrix_schema_requires_timeouts(self) -> None:
        fields = tuple(field for field in MATRIX_FIELDS if field != "test_timeout")
        rows = [
            {field: value for field, value in row.items() if field in fields}
            for row in self.fixture.matrix_rows
        ]
        write_tsv(self.fixture.matrix, fields, rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("matrix_error", error_codes(result))

    def test_runner_invocation_timeout_mismatch_fails(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                "test_timeout=30", "test_timeout=31"
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any("invocation.test_timeout" in detail for detail in error_details(result))
        )

    def test_launch_invocation_config_mismatch_fails(self) -> None:
        invocation = self.fixture.artifact / "launch-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                "gem5_config_args=defaults",
                "gem5_config_args=defaults-but-different",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "launch_invocation.gem5_config_args" in detail
                for detail in error_details(result)
            )
        )

    def test_launch_invocation_bridge_mismatch_fails(self) -> None:
        invocation = self.fixture.artifact / "launch-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                f"share_dir={self.fixture.staging}",
                f"share_dir={self.fixture.artifact / 'wrong-staging'}",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "launch_invocation.share_dir" in detail
                for detail in error_details(result)
            )
        )

    def test_guest_script_timeout_mismatch_fails(self) -> None:
        guest_script = self.fixture.artifact / "guest-run.sh"
        guest_script.write_text(
            guest_script.read_text(encoding="utf-8").replace(
                "TEST_TIMEOUT_SECS=30 ./run_tests.sh vector_add",
                "TEST_TIMEOUT_SECS=31 ./run_tests.sh vector_add",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("timeout_mismatch", error_codes(result))

    def test_accepted_nonpass_row_fails(self) -> None:
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict.update(
            {
                "outcome": "FAIL",
                "exit_code": 1,
                "reason": "nonzero_test_exit",
            }
        )
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.fixture.matrix_rows[0].update(
            {"outcome": "FAIL", "exit_code": "1", "reason": "nonzero_test_exit"}
        )
        write_tsv(
            self.fixture.artifact / "matrix.tsv",
            LOCAL_MATRIX_FIELDS,
            [
                {
                    "program": "vector_add",
                    "hsa_interrupt": "0",
                    "run": "1",
                    "session_id": "unit-vector",
                    "outcome": "FAIL",
                    "exit_code": "1",
                    "reason": "nonzero_test_exit",
                    "artifact_dir": str(self.fixture.artifact),
                    "boot_timeout": "240",
                    "test_timeout": "30",
                    "guest_run_timeout": "1800",
                }
            ],
        )
        metadata_path = self.fixture.artifact / "runner-metadata.txt"
        metadata = metadata_path.read_text(encoding="utf-8")
        metadata = metadata.replace("category=test_pass", "category=test_fail")
        metadata = metadata.replace("test_exit_code=0", "test_exit_code=1")
        metadata = metadata.replace("\nexit_code=0\n", "\nexit_code=1\n")
        metadata_path.write_text(metadata, encoding="utf-8")
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("accepted_outcome_not_pass", error_codes(result))

    def test_timeout_policy_suffix_is_rejected(self) -> None:
        self.fixture.manifest_rows[0]["timeout_policy"] = "fixed-30;bogus"
        self.fixture.matrix_rows[0]["timeout_policy"] = "fixed-30;bogus"
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_timeout_policy", error_codes(result))

    def test_metadata_gem5_binary_mismatch_fails(self) -> None:
        metadata = self.fixture.artifact / "runner-metadata.txt"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                f"gem5_binary={self.fixture.gem5_binary}",
                "gem5_binary=/wrong/gem5.opt",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any("metadata.gem5_binary" in detail for detail in error_details(result))
        )

    def test_container_gem5_binary_mismatch_fails(self) -> None:
        invocation = self.fixture.artifact / "launch-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                "gem5_container_binary=/gem5/build/VEGA_X86/gem5.opt",
                "gem5_container_binary=/gem5/wrong.opt",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "launch_invocation.gem5_container_binary" in detail
                for detail in error_details(result)
            )
        )

    def test_unknown_guest_bridge_policy_fails(self) -> None:
        self.fixture.manifest_rows[0]["guest_bridge_policy"] = "arbitrary"
        self.fixture.matrix_rows[0]["guest_bridge_policy"] = "arbitrary"
        for name in ("runner-invocation.txt", "runner-metadata.txt"):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "guest_bridge_policy=artifact-local",
                    "guest_bridge_policy=arbitrary",
                ),
                encoding="utf-8",
            )
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_bridge_policy", error_codes(result))

    def test_forged_runner_argv_fails(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                "argv=--boot-timeout 240 --test-timeout 30 "
                "--guest-run-timeout 1800 "
                f"--output-dir {self.fixture.artifact} vector_add",
                "argv=forged",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invocation_program_mismatch", error_codes(result))

    def test_recorded_passthrough_not_present_in_runner_argv_fails(self) -> None:
        runner_invocation = self.fixture.artifact / "runner-invocation.txt"
        runner_invocation.write_text(
            runner_invocation.read_text(encoding="utf-8").replace(
                "passthrough_args=",
                "passthrough_args=--qemu-trace forged",
            ),
            encoding="utf-8",
        )
        launch_invocation = self.fixture.artifact / "launch-invocation.txt"
        launch_invocation.write_text(
            launch_invocation.read_text(encoding="utf-8").replace(
                f"--artifact-dir {self.fixture.artifact}",
                f"--artifact-dir {self.fixture.artifact} --qemu-trace forged",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invocation_passthrough_mismatch", error_codes(result))

    def test_runner_argv_passthrough_not_launched_fails(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                f"--output-dir {self.fixture.artifact} vector_add",
                f"--output-dir {self.fixture.artifact} --qemu-trace forged vector_add",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invocation_passthrough_mismatch", error_codes(result))

    def test_noncanonical_guest_prefix_fails(self) -> None:
        noncanonical = "FOO=bar HSA_ENABLE_INTERRUPT=0"
        self.fixture.manifest_rows[0]["guest_test_prefix"] = noncanonical
        self.fixture.matrix_rows[0]["guest_test_prefix"] = noncanonical
        for name in ("runner-invocation.txt", "runner-metadata.txt"):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "guest_test_prefix=HSA_ENABLE_INTERRUPT=0",
                    f"guest_test_prefix={noncanonical}",
                ),
                encoding="utf-8",
            )
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_guest_prefix", error_codes(result))

    def test_noncanonical_program_source_fails(self) -> None:
        self.mutate_program_identity(
            "program_source", "tests/kernels/../kernels/vector_add.cpp"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_program_identity", error_codes(result))

    def test_noncanonical_program_binary_fails(self) -> None:
        self.mutate_program_identity("program_binary", "wrong/vector_add")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_program_identity", error_codes(result))

    def test_noncanonical_runner_argument_fails(self) -> None:
        self.mutate_program_identity("runner_argument", "wrong")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_program_identity", error_codes(result))

    def test_program_must_be_strict_basename(self) -> None:
        self.fixture.manifest_rows[0]["program"] = "nested/vector_add"
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_program_name", error_codes(result))

    def test_guest_prefix_inputs_must_match(self) -> None:
        metadata = self.fixture.artifact / "runner-metadata.txt"
        metadata.write_text(
            metadata.read_text(encoding="utf-8").replace(
                "guest_test_prefix_input=",
                "guest_test_prefix_input=HSA_ENABLE_INTERRUPT=0",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("guest_prefix_input_mismatch", error_codes(result))

    def test_empty_guest_prefix_input_cannot_enable_interrupts(self) -> None:
        self.set_hsa_mode("1")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("noncanonical_guest_prefix_input", error_codes(result))

    def test_explicit_guest_prefix_input_can_enable_interrupts(self) -> None:
        self.set_hsa_mode("1")
        for name in ("runner-invocation.txt", "runner-metadata.txt"):
            path = self.fixture.artifact / name
            path.write_text(
                path.read_text(encoding="utf-8").replace(
                    "guest_test_prefix_input=",
                    "guest_test_prefix_input=HSA_ENABLE_INTERRUPT=1",
                ),
                encoding="utf-8",
            )

        result = self.verify()
        self.assertEqual("PASS", result["outcome"])

    def test_coordinated_shell_test_binary_fails_shape_check(self) -> None:
        self.fixture.test_binary.write_bytes(b"#!/bin/sh\necho forged\n")
        binary_hash = sha256(self.fixture.test_binary)
        replace_key_value(
            self.fixture.patch / "binary-provenance.txt",
            "test_binary_sha256",
            binary_hash,
        )
        self.fixture.matrix_rows[0]["test_binary_sha256"] = binary_hash
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_test_binary_format", error_codes(result))

    def test_coordinated_gem5_recipe_fingerprint_forgery_fails(self) -> None:
        build_meta = self.fixture.patch / "gem5-build-meta.txt"
        replace_key_value(
            build_meta,
            "docker_build_recipe_fingerprint",
            "f" * 64,
        )
        build_meta_hash = sha256(build_meta)
        replace_key_value(
            self.fixture.patch / "source-snapshot.txt",
            "gem5_build_meta_sha256",
            build_meta_hash,
        )
        replace_key_value(
            self.fixture.patch / "binary-provenance.txt",
            "gem5_build_meta_sha256",
            build_meta_hash,
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))
        self.assertTrue(
            any(
                "docker_build_recipe_fingerprint" in detail
                for detail in error_details(result)
            )
        )

    def test_strict_archive_rejects_nonregular_members(self) -> None:
        archive_path = self.fixture.patch / "repo-untracked-files.tar"
        with tarfile.open(archive_path, mode="w") as archive:
            member = tarfile.TarInfo("escape")
            member.type = tarfile.SYMTYPE
            member.linkname = "../../outside"
            archive.addfile(member)
        replace_key_value(
            self.fixture.patch / "source-snapshot.txt",
            "repo_untracked_archive_sha256",
            sha256(archive_path),
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("unsupported_archive_member", error_codes(result))
        self.assertIn(
            "acceptance_untracked_archive_not_empty",
            error_codes(result),
        )

    def test_cleanup_aliases_are_not_acceptance_evidence(self) -> None:
        replace_key_value(
            self.fixture.artifact / "runner-metadata.txt",
            "cleanup_status",
            "success",
        )
        replace_key_value(
            self.fixture.artifact / "cleanup-status.txt",
            "result",
            "success",
        )
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["checks"]["cleanup"]["status"] = "success"
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        self.fixture.matrix_rows[0]["cleanup_status"] = "success"
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("cleanup_not_verified", error_codes(result))

    def test_qemu_preflight_provenance_is_required(self) -> None:
        preflight_path = self.fixture.artifact / "preflight" / "preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        preflight["checks"] = [
            check
            for check in preflight["checks"]
            if check["id"] != "run.qemu_provenance"
        ]
        preflight_path.write_text(
            json.dumps(preflight, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_run_preflight", error_codes(result))

    def test_missing_canonical_qemu_binary_fails(self) -> None:
        self.fixture.qemu_binary.unlink()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("missing_file", error_codes(result))
        self.assertTrue(
            any("qemu_binary" in detail for detail in error_details(result))
        )

    def test_symlinked_canonical_kernel_fails(self) -> None:
        replacement = self.fixture.root / ".local" / "forged-kernel"
        replacement.parent.mkdir(parents=True, exist_ok=True)
        replacement.write_bytes(self.fixture.kernel.read_bytes())
        self.fixture.kernel.unlink()
        self.fixture.kernel.symlink_to(replacement)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("symlink_not_allowed", error_codes(result))
        self.assertTrue(any("kernel" in detail for detail in error_details(result)))

    def test_guest_overlay_must_bind_canonical_disk_and_format(self) -> None:
        overlay_path = self.fixture.artifact / "guest-overlay.json"
        overlay = json.loads(overlay_path.read_text(encoding="utf-8"))
        overlay["format"] = "raw"
        overlay["backing-filename"] = "/tmp/forged-disk"
        overlay_path.write_text(
            json.dumps(overlay, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("path_mismatch", error_codes(result))
        self.assertTrue(
            any("guest_overlay.format" in detail for detail in error_details(result))
        )


if __name__ == "__main__":
    unittest.main()
