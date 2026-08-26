#!/usr/bin/env python3

from __future__ import annotations

import csv
import hashlib
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts.guest_provenance import (  # noqa: E402
    LOCK_KEYS as GUEST_LOCK_KEYS,
    META_KEYS as GUEST_META_KEYS,
    SEAL_KEYS as GUEST_SEAL_KEYS,
    recipe_fingerprint as guest_recipe_fingerprint,
)
from scripts.cosim_log_evidence import (  # noqa: E402
    BOUNDARY_HANDSHAKE_MIN_TIMEOUT_SECONDS,
    evidence_boundary_token,
    render_guest_run_script,
)
from scripts.verify_cosim_matrix import (  # noqa: E402
    GEM5_STDIO_WRAPPER_ARGS,
    GEM5_STDIO_WRAPPER_PATH,
    PROGRAM_RE,
    RUN_PREFLIGHT_CHECK_IDS,
    RUN_PREFLIGHT_REQUIRED_IDS,
    SCHEMA,
    _array_fingerprint,
    _directory_fingerprint,
    _hash_file,
    verify_matrix,
)


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
    "gem5_evidence_boundary_binary",
    "gem5_evidence_boundary_binary_sha256",
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
STRICT_DEBUG_FLAGS = "HSAPacketProcessor,GPUCommandProc,GPUDisp,GPUKernelInfo"
UNIT_BOUNDARY_TOKEN = evidence_boundary_token("unit-vector", "vector_add")
UNIT_GEM5_CONFIG_ARGS = (
    "defaults:num-gpus=1,num-cus=40,host-mem=8G,vram-size=16GiB;"
    f"evidence-test-id=vector_add,evidence-token={UNIT_BOUNDARY_TOKEN};"
    f"debug-flags={STRICT_DEBUG_FLAGS}"
)
GEM5_DOCKER_ARGS = (
    f"--debug-flags={STRICT_DEBUG_FLAGS}",
    "--listener-mode=on",
    "/gem5/configs/example/gpufs/mi300_cosim.py",
    "--socket-path=/tmp/gem5-mi300x-unit-vector.sock",
    "--shmem-path=/mi300x-vram-unit-vector",
    "--shmem-host-path=/cosim-guest-ram-unit-vector",
    "--evidence-path=/cosim-artifacts/gem5-evidence.tsv",
    "--evidence-run-id=unit-vector",
    "--dgpu-mem-size=16GiB",
    "--num-compute-units=40",
    "--mem-size=8G",
    "--num-gpus=1",
    "--evidence-test-id=vector_add",
    f"--evidence-token={UNIT_BOUNDARY_TOKEN}",
)
GEM5_LOG_TEXT = (
    "2026-01-01T00:00:00.100000000Z command line: "
    + " ".join(("/gem5/build/VEGA_X86/gem5.opt", *GEM5_DOCKER_ARGS))
    + "\n"
    "2026-01-01T00:00:00.200000000Z src/dev/amdgpu/mi300x_vfio_user.cc:312: "
    "info: MI300XVfioUser: client connected (vfio-user)\n"
    "2026-01-01T00:00:01.100000000Z 10: "
    "system.Shader.gpu_cmd_proc.dispatcher: launching kernel: "
    "Some kernel, dispatch ID: 0\n"
    "2026-01-01T00:00:01.200000000Z 11: system.Shader: "
    "Dispatching a workgroup to CU 0: WG 0\n"
    "2026-01-01T00:00:01.300000000Z 12: dispatcher: notify WgCompl 0\n"
    "2026-01-01T00:00:01.400000000Z 13: dispatcher: Completed kernel 0\n"
)
GEM5_LOG_SHA256 = hashlib.sha256(GEM5_LOG_TEXT.encode()).hexdigest()
GEM5_EVIDENCE_TEXT = (
    "schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t0\t0\tsession_start\t-1\t-1\t-1\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t1\t1\tclient_connected\t0\t-1\t-1\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t2\t2\ttest_begin\t0\t-1\t-1\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t3\t10\tkernel_launch\t0\t0\t-1\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t4\t11\tworkgroup_dispatch\t0\t0\t0\t0\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t5\t12\tworkgroup_complete\t0\t0\t0\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t6\t13\tkernel_complete\t0\t0\t-1\t-1\n"
    "COSIM_GPU_EVIDENCE_V1\tunit-vector\t7\t14\ttest_end\t0\t-1\t-1\t-1\n"
)
GEM5_EVIDENCE_SHA256 = hashlib.sha256(GEM5_EVIDENCE_TEXT.encode()).hexdigest()
QEMU_SIGTERM_LINE = (
    "\x1b[?2004hroot@gem5:~# qemu-system-x86_64: "
    "terminating on signal 15 from pid 1 (/bin/bash)"
)
UNIT_RUN_SHA256 = hashlib.sha256(b"unit-vector").hexdigest()
UNIT_COMPILE_TOKEN = f"COSIM_COMPILE_DONE_vector_add_{UNIT_RUN_SHA256}"
UNIT_BOUNDARY_READY_TOKEN = (
    f"COSIM_BOUNDARY_READY_vector_add_{UNIT_RUN_SHA256}"
)
UNIT_TEST_TOKEN = f"COSIM_TEST_DONE_vector_add_{UNIT_RUN_SHA256}"
UNIT_BOUNDARY_BINARY_BYTES = b"synthetic evidence boundary helper\n"
UNIT_BOUNDARY_BINARY_SHA256 = hashlib.sha256(
    UNIT_BOUNDARY_BINARY_BYTES
).hexdigest()
QEMU_LOG_TEXT = (
    "  Run-ID:     unit-vector\n"
    "[COSIM_ENV] HSA_ENABLE_INTERRUPT=0\n"
    "[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=30\n"
    f"__{UNIT_COMPILE_TOKEN}__:0\n"
    f"__{UNIT_BOUNDARY_READY_TOKEN}__:{UNIT_BOUNDARY_BINARY_SHA256}\n"
    "[PASS] vector_add\n"
    f"__{UNIT_TEST_TOKEN}__:0\n"
    f"{QEMU_SIGTERM_LINE}\n"
)
QEMU_LOG_SHA256 = hashlib.sha256(QEMU_LOG_TEXT.encode()).hexdigest()


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
            and path.relative_to(root).parts[0] not in {"build", "tools-build"}
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


class ProgramIdentityContractTests(unittest.TestCase):
    def test_program_identity_accepts_128_and_rejects_129_ascii_bytes(self) -> None:
        accepted = "a" * 128
        rejected = accepted + "a"

        self.assertIsNotNone(PROGRAM_RE.fullmatch(accepted))
        self.assertIsNone(PROGRAM_RE.fullmatch(rejected))
        self.assertRegex(
            evidence_boundary_token("unit-id-boundary", accepted),
            r"^[0-9a-f]{32}$",
        )
        with self.assertRaises(ValueError):
            evidence_boundary_token("unit-id-boundary", rejected)
        with self.assertRaises(ValueError):
            render_guest_run_script(
                program=rejected,
                run_id="unit-id-boundary",
                hsa_enable_interrupt="0",
                test_timeout="1",
            )

    def test_minimum_workload_timeout_keeps_independent_ack_budget(self) -> None:
        script = render_guest_run_script(
            program="vector_add",
            run_id="unit-ack-timeout",
            hsa_enable_interrupt="0",
            test_timeout="1",
        )

        self.assertGreaterEqual(BOUNDARY_HANDSHAKE_MIN_TIMEOUT_SECONDS, 30)
        self.assertIn("[COSIM_TIMEOUT] TEST_TIMEOUT_SECS=1", script)
        self.assertIn(
            "boundary_handshake_timeout_secs="
            f"{BOUNDARY_HANDSHAKE_MIN_TIMEOUT_SECONDS}",
            script,
        )
        self.assertIn(
            "boundary_wait<boundary_handshake_timeout_secs",
            script,
        )
        self.assertEqual(
            2,
            script.count(
                'timeout --signal=TERM "${boundary_handshake_timeout_secs}s"'
            ),
        )


class HashCacheTests(unittest.TestCase):
    def test_same_path_replacement_does_not_reuse_stale_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "payload"
            path.write_bytes(b"AAAA")
            before = path.stat()
            cache = {}
            first = _hash_file(path, cache)
            replacement = path.with_name("replacement")
            replacement.write_bytes(b"BBBB")
            os.utime(
                replacement,
                ns=(before.st_atime_ns, before.st_mtime_ns),
            )
            os.replace(replacement, path)
            second = _hash_file(path, cache)
            self.assertNotEqual(first, second)
            self.assertEqual(hashlib.sha256(b"BBBB").hexdigest(), second)


def path_stat(path: Path) -> dict[str, object]:
    info = path.stat()
    return {
        "path": str(path.resolve()),
        "size": info.st_size,
        "device": info.st_dev,
        "inode": info.st_ino,
        "mtime_ns": info.st_mtime_ns,
        "ctime_ns": info.st_ctime_ns,
    }


def write_key_values(
    path: Path, keys: tuple[str, ...], values: dict[str, str]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(f"{key}={values[key]}\n" for key in keys),
        encoding="utf-8",
    )


class MatrixFixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.artifact = root / "artifacts" / "run-vector"
        self.patch = self.artifact / "patch"
        self.staging = self.artifact / "staging"
        self.source = root / "tests" / "kernels" / "vector_add.cpp"
        self.test_binary = self.staging / "build" / "vector_add"
        self.boundary_binary = (
            self.staging / "tools-build" / "cosim_evidence_boundary"
        )
        self.gem5_binary = root / "gem5" / "build" / "VEGA_X86" / "gem5.opt"
        self.resources = root / "gem5-resources"
        self.guest_template = self.resources / "src/x86-ubuntu-gpu-ml"
        self.disk_image = self.guest_template / "disk-image/x86-ubuntu-rocm70"
        self.kernel = self.guest_template / "vmlinux-rocm70"
        self.m5 = self.guest_template / "files/m5"
        self.qemu_binary = (
            root / ".local/cosim/qemu/10.1.5/bin/qemu-system-x86_64"
        )
        self.qemu_img = root / ".local/cosim/qemu/10.1.5/bin/qemu-img"
        self.qemu_source = root / ".local/cosim/src/qemu-10.1.5"
        self.qemu_build_meta = (
            root / ".local/cosim/build/qemu-10.1.5/.cosim-build-meta"
        )
        self.toolchain_lock = root / "configs/cosim/toolchain.lock"
        self.guest_build_meta = (
            root / ".local/cosim/build/guest/.cosim-build-meta"
        )
        self.guest_content_seal = (
            root / ".local/cosim/build/guest/.cosim-content-seal"
        )
        self.guest_lock = root / "configs/cosim/guest.lock"
        self.guest_patch = (
            root / "scripts/patches/0002-guest-core-reproducible.patch"
        )
        self.manifest = root / "run-manifest.tsv"
        self.matrix = root / "matrix.tsv"
        self.output = root / "verification.json"
        self.manifest_rows: list[dict[str, str]] = []
        self.matrix_rows: list[dict[str, str]] = []
        self._create()

    @staticmethod
    def _git(repo: Path, *arguments: str, output: bool = False) -> str:
        completed = subprocess.run(
            ("git", "-C", str(repo), *arguments),
            check=True,
            stdout=subprocess.PIPE if output else subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )
        return completed.stdout.strip() if output else ""

    @classmethod
    def _configure_git(cls, repo: Path) -> None:
        cls._git(repo, "config", "user.name", "Test")
        cls._git(repo, "config", "user.email", "test@example.invalid")

    @staticmethod
    def _guest_lock_values() -> dict[str, str]:
        values: dict[str, str] = {}
        for index, key in enumerate(GUEST_LOCK_KEYS, 1):
            if key.endswith("_URL"):
                values[key] = f"https://example.invalid/{key.lower()}"
            elif key.endswith("_SHA256"):
                values[key] = f"{index:064x}"
            else:
                values[key] = f"value-{index}"
        values["GUEST_LOCK_VERSION"] = "1"
        values["ROCM_KEY_FINGERPRINT"] = "A" * 40
        values["PACKER_VERSION"] = "1.10.0"
        values["PACKER_QEMU_PLUGIN_VERSION"] = "1.1.6"
        values["AMDGPU_DKMS_VERSION"] = "fixture-dkms"
        values["ROCM_VERSION"] = "fixture-rocm"
        values["GUEST_KERNEL"] = "fixture-kernel"
        return values

    def _write_qemu_provenance(self) -> None:
        source_fingerprint = _directory_fingerprint(self.qemu_source, {})
        configure_args = (
            f"--prefix={self.root / '.local/cosim/qemu/10.1.5'}",
            "--target-list=x86_64-softmmu",
            "--disable-download",
            "--disable-docs",
            "--disable-gtk",
            "--disable-sdl",
            "--disable-werror",
            "--enable-kvm",
            "--enable-slirp",
            "--enable-tools",
            "--enable-virtfs",
        )
        source_sha = "1" * 64
        configure_fingerprint = _array_fingerprint(configure_args)
        build_fingerprint = _array_fingerprint(
            (source_sha, source_fingerprint, configure_fingerprint)
        )
        metadata = {
            "version": "10.1.5",
            "source_url": "https://download.qemu.org/qemu-10.1.5.tar.xz",
            "source_sha256": source_sha,
            "signature_url": "https://download.qemu.org/qemu-10.1.5.tar.xz.sig",
            "signing_key": "CEACC9E15534EBABB82D3FA03353C9CEF108B584",
            "signing_verified": "true",
            "initial_source_fingerprint": source_fingerprint,
            "source_fingerprint": source_fingerprint,
            "source_pristine": "true",
            "configure_fingerprint": configure_fingerprint,
            "build_fingerprint": build_fingerprint,
            "configure_args": " ".join(configure_args),
            "binary": str(self.qemu_binary.resolve()),
            "binary_sha256": sha256(self.qemu_binary),
            "qemu_img": str(self.qemu_img.resolve()),
            "qemu_img_sha256": sha256(self.qemu_img),
            "compiler": "cc fixture 1.0",
            "timestamp": "2026-01-01T00:00:00Z",
        }
        write_key_values(
            self.qemu_build_meta,
            tuple(metadata),
            metadata,
        )
        (self.artifact / "qemu-build-meta.txt").write_bytes(
            self.qemu_build_meta.read_bytes()
        )
        (self.artifact / "toolchain.lock").write_bytes(
            self.toolchain_lock.read_bytes()
        )

    def _write_guest_provenance(self) -> None:
        lock_sha = sha256(self.guest_lock)
        patch_sha = sha256(self.guest_patch)
        image_sha = sha256(self.disk_image)
        kernel_sha = sha256(self.kernel)
        m5_sha = sha256(self.m5)
        qemu_sha = sha256(self.qemu_binary)
        qemu_img_sha = sha256(self.qemu_img)
        build_script_sha = sha256(self.root / "scripts/cosim_build.sh")
        validator_sha = sha256(self.root / "scripts/guest_provenance.py")
        recipe = guest_recipe_fingerprint(
            (
                "guest-recipe-v2",
                f"build_top_commit={self.head_commit}",
                f"build_script={build_script_sha}",
                f"provenance_validator={validator_sha}",
                f"resources_commit={self.resources_commit}",
                f"template_tree={self.template_tree}",
                f"overlay_patch={patch_sha}",
                f"m5={m5_sha}",
                f"qemu={qemu_sha}",
                f"qemu_img={qemu_img_sha}",
                f"packer={self.guest_lock_values['PACKER_SHA256']}",
                "packer_plugin="
                f"{self.guest_lock_values['PACKER_QEMU_PLUGIN_SHA256']}",
                f"guest_lock={lock_sha}",
            )
        )
        metadata = {
            "component": "guest",
            "schema": "2",
            "build_top_commit": self.head_commit,
            "build_script_sha256": build_script_sha,
            "provenance_validator_sha256": validator_sha,
            "resources_commit": self.resources_commit,
            "template_tree": self.template_tree,
            "overlay_patch_sha256": patch_sha,
            "guest_lock_sha256": lock_sha,
            "recipe_fingerprint": recipe,
            "packer_version": self.guest_lock_values["PACKER_VERSION"],
            "packer_sha256": self.guest_lock_values["PACKER_SHA256"],
            "packer_qemu_plugin_version": self.guest_lock_values[
                "PACKER_QEMU_PLUGIN_VERSION"
            ],
            "packer_qemu_plugin_sha256": self.guest_lock_values[
                "PACKER_QEMU_PLUGIN_SHA256"
            ],
            "ubuntu_iso_url": self.guest_lock_values["UBUNTU_ISO_URL"],
            "ubuntu_iso_sha256": self.guest_lock_values[
                "UBUNTU_ISO_SHA256"
            ],
            "amdgpu_dkms_version": self.guest_lock_values[
                "AMDGPU_DKMS_VERSION"
            ],
            "rocm_version": self.guest_lock_values["ROCM_VERSION"],
            "kernel_version": self.guest_lock_values["GUEST_KERNEL"],
            "qemu_binary_sha256": qemu_sha,
            "qemu_img_sha256": qemu_img_sha,
            "m5_sha256": m5_sha,
            "image": str(self.disk_image.resolve()),
            "image_sha256": image_sha,
            "image_size": str(self.disk_image.stat().st_size),
            "kernel": str(self.kernel.resolve()),
            "kernel_sha256": kernel_sha,
            "kernel_size": str(self.kernel.stat().st_size),
            "artifacts": str(
                self.root
                / "artifacts/amd-gpu-learning-env/build/guest/unit-build"
            ),
            "timestamp": "2026-01-01T00:00:00Z",
        }
        write_key_values(self.guest_build_meta, GUEST_META_KEYS, metadata)
        metadata_sha = sha256(self.guest_build_meta)

        image_stat = path_stat(self.disk_image)
        seal = {
            "component": "guest-content-seal",
            "schema": "1",
            "guest_build_meta_sha256": metadata_sha,
            "image": str(image_stat["path"]),
            "image_sha256": image_sha,
            "image_size": str(image_stat["size"]),
            "image_device": str(image_stat["device"]),
            "image_inode": str(image_stat["inode"]),
            "image_mtime_ns": str(image_stat["mtime_ns"]),
            "image_ctime_ns": str(image_stat["ctime_ns"]),
            "sealed_at": "2026-01-01T00:00:00.050000000Z",
        }
        write_key_values(self.guest_content_seal, GUEST_SEAL_KEYS, seal)
        seal_sha = sha256(self.guest_content_seal)

        (self.artifact / "guest-build-meta.txt").write_bytes(
            self.guest_build_meta.read_bytes()
        )
        (self.artifact / "guest-content-seal.txt").write_bytes(
            self.guest_content_seal.read_bytes()
        )
        (self.artifact / "guest.lock").write_bytes(self.guest_lock.read_bytes())
        (self.artifact / "guest-overlay.patch").write_bytes(
            self.guest_patch.read_bytes()
        )

        report = {
            "schema": "cosim-guest-provenance/v2",
            "run_id": "unit-vector",
            "validated_at": "2026-01-01T00:00:00.200000000Z",
            "guest_build_meta": {
                "path": str(self.guest_build_meta.resolve()),
                "sha256": metadata_sha,
            },
            "guest_content_seal": {
                "path": str(self.guest_content_seal.resolve()),
                "sha256": seal_sha,
            },
            "source": {
                "build_top_commit": self.head_commit,
                "validated_top_head": self.head_commit,
                "build_script_sha256": build_script_sha,
                "provenance_validator_sha256": validator_sha,
                "resources_gitlink": self.resources_commit,
                "resources_head": self.resources_commit,
                "template_tree": self.template_tree,
                "guest_lock_sha256": lock_sha,
                "overlay_patch_sha256": patch_sha,
                "recipe_fingerprint": recipe,
            },
            "toolchain": {
                "qemu_binary_sha256": qemu_sha,
                "qemu_img_sha256": qemu_img_sha,
            },
            "image": {
                **image_stat,
                "sha256": image_sha,
                "validation_method": "sealed-stat",
            },
            "kernel": {
                **path_stat(self.kernel),
                "sha256": kernel_sha,
                "validation_method": "full-sha256",
            },
            "m5": {
                **path_stat(self.m5),
                "sha256": m5_sha,
                "validation_method": "full-sha256",
            },
        }
        report_bytes = (
            json.dumps(report, indent=2, sort_keys=True) + "\n"
        ).encode()
        (self.artifact / "guest-provenance.json").write_bytes(report_bytes)
        preflight_dir = self.artifact / "preflight"
        preflight_dir.mkdir(parents=True, exist_ok=True)
        (preflight_dir / "guest-provenance.json").write_bytes(report_bytes)

        base_stat = {
            "schema": "cosim-guest-base-stat/v2",
            "path": str(report["image"]["path"]),
            "image_sha256": image_sha,
            "validation_method": "sealed-stat",
            "size": str(image_stat["size"]),
            "device": str(image_stat["device"]),
            "inode": str(image_stat["inode"]),
            "mtime_ns": str(image_stat["mtime_ns"]),
            "ctime_ns": str(image_stat["ctime_ns"]),
            "guest_build_meta_sha256": metadata_sha,
            "guest_content_seal_sha256": seal_sha,
        }
        base_keys = (
            "schema",
            "path",
            "image_sha256",
            "validation_method",
            "size",
            "device",
            "inode",
            "mtime_ns",
            "ctime_ns",
            "guest_build_meta_sha256",
            "guest_content_seal_sha256",
        )
        write_key_values(
            self.artifact / "guest-base-stat.txt", base_keys, base_stat
        )
        pre_stat_report = {
            "schema": "cosim-guest-post-stat/v1",
            "run_id": "unit-vector",
            "captured_at": "2026-01-01T00:00:00.500000000Z",
            "image": image_stat,
            "matches_pre": True,
        }
        post_stat_report = {
            **pre_stat_report,
            "captured_at": "2026-01-01T00:00:03.000000000Z",
        }
        pre_stat_bytes = (
            json.dumps(pre_stat_report, indent=2, sort_keys=True) + "\n"
        ).encode()
        post_stat_bytes = (
            json.dumps(post_stat_report, indent=2, sort_keys=True) + "\n"
        ).encode()
        (self.artifact / "guest-base-stat-pre.json").write_bytes(pre_stat_bytes)
        (self.artifact / "guest-base-stat-post.json").write_bytes(post_stat_bytes)

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
        (self.root / "scripts" / "guest_provenance.py").write_text(
            "#!/usr/bin/env python3\n# synthetic provenance validator\n",
            encoding="utf-8",
        )
        (self.root / "scripts" / "Dockerfile.run").write_text(
            "FROM synthetic.invalid/gem5-run\n",
            encoding="utf-8",
        )
        self.guest_lock_values = self._guest_lock_values()
        write_key_values(
            self.guest_lock,
            GUEST_LOCK_KEYS,
            self.guest_lock_values,
        )
        self.guest_patch.parent.mkdir(parents=True, exist_ok=True)
        self.guest_patch.write_text(
            "synthetic Guest overlay patch\n",
            encoding="utf-8",
        )
        self.source.parent.mkdir(parents=True)
        self.source.write_text("int vector_add_source = 1;\n", encoding="utf-8")
        self.patch.mkdir(parents=True)
        (self.staging / "kernels").mkdir(parents=True)
        (self.staging / "build").mkdir(parents=True)
        self.boundary_binary.parent.mkdir(parents=True)
        (self.staging / "kernels" / "vector_add.cpp").write_bytes(
            self.source.read_bytes()
        )
        self.test_binary.write_bytes(synthetic_hip_executable())
        self.test_binary.chmod(0o755)
        self.boundary_binary.write_bytes(UNIT_BOUNDARY_BINARY_BYTES)
        self.boundary_binary.chmod(0o755)

        self.guest_template.mkdir(parents=True)
        (self.guest_template / "files").mkdir()
        (self.guest_template / "recipe.txt").write_text(
            "synthetic Guest recipe\n",
            encoding="utf-8",
        )
        (self.guest_template / "files" / ".keep").write_text(
            "tracked\n",
            encoding="utf-8",
        )
        (self.resources / ".gitignore").write_text(
            "/src/x86-ubuntu-gpu-ml/disk-image/\n"
            "/src/x86-ubuntu-gpu-ml/vmlinux-rocm70\n"
            "/src/x86-ubuntu-gpu-ml/files/m5\n",
            encoding="utf-8",
        )
        subprocess.run(("git", "init", "-q", str(self.resources)), check=True)
        self._configure_git(self.resources)
        self._git(self.resources, "add", ".")
        self._git(self.resources, "commit", "-qm", "fixture")
        self.resources_commit = self._git(
            self.resources,
            "rev-parse",
            "HEAD",
            output=True,
        )
        self.template_tree = self._git(
            self.resources,
            "rev-parse",
            f"{self.resources_commit}:src/x86-ubuntu-gpu-ml",
            output=True,
        )

        self.disk_image.parent.mkdir(parents=True)
        self.disk_image.write_bytes(b"synthetic guest disk\n")
        self.kernel.write_bytes(b"synthetic guest kernel\n")
        self.m5.write_bytes(b"synthetic guest m5\n")

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
        current_lock.parent.mkdir(parents=True, exist_ok=True)
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
        (current_lock.parent / "strict-acceptance-rows.json").write_text(
            json.dumps(
                {
                    "schema": "cosim-strict-expected-rows/v1",
                    "rows": [
                        {
                            "program": "vector_add",
                            "expected_hsa_interrupt": "0",
                        }
                    ],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        gem5_baseline_lock = self.patch / "gem5-baseline.lock"
        gem5_baseline_lock.write_bytes(current_lock.read_bytes())

        self.qemu_source.mkdir(parents=True)
        (self.qemu_source / "README.rst").write_text(
            "synthetic qemu source\n", encoding="utf-8"
        )
        qemu_source_fingerprint = _directory_fingerprint(self.qemu_source, {})
        write_key_values(
            self.toolchain_lock,
            (
                "QEMU_VERSION",
                "QEMU_SOURCE_URL",
                "QEMU_SIGNATURE_URL",
                "QEMU_RELEASE_KEY_FINGERPRINT",
                "QEMU_RELEASE_KEY_URL",
                "QEMU_SOURCE_SHA256",
                "QEMU_SOURCE_FINGERPRINT",
            ),
            {
                "QEMU_VERSION": "10.1.5",
                "QEMU_SOURCE_URL": "https://download.qemu.org/qemu-10.1.5.tar.xz",
                "QEMU_SIGNATURE_URL": "https://download.qemu.org/qemu-10.1.5.tar.xz.sig",
                "QEMU_RELEASE_KEY_FINGERPRINT": "CEACC9E15534EBABB82D3FA03353C9CEF108B584",
                "QEMU_RELEASE_KEY_URL": "https://keys.openpgp.org/vks/v1/by-fingerprint/CEACC9E15534EBABB82D3FA03353C9CEF108B584",
                "QEMU_SOURCE_SHA256": "1" * 64,
                "QEMU_SOURCE_FINGERPRINT": qemu_source_fingerprint,
            },
        )

        self.qemu_binary.parent.mkdir(parents=True)
        self.qemu_binary.write_bytes(b"synthetic qemu binary\n")
        self.qemu_binary.chmod(0o755)
        self.qemu_img.write_bytes(b"synthetic qemu-img binary\n")
        self.qemu_img.chmod(0o755)
        build_lock = self.root / ".local/cosim/build.lock"
        build_lock.parent.mkdir(parents=True, exist_ok=True)
        build_lock.write_bytes(b"")

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
        self._write_qemu_provenance()
        self._write_guest_provenance()

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
                    "gem5_evidence_boundary_binary="
                    f"{self.boundary_binary}",
                    "gem5_evidence_boundary_binary_sha256="
                    f"{sha256(self.boundary_binary)}",
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
                    f"gem5_config_args={UNIT_GEM5_CONFIG_ARGS}",
                    "gem5_evidence_test_id=vector_add",
                    f"gem5_evidence_token={UNIT_BOUNDARY_TOKEN}",
                    "gem5_evidence_boundary_binary="
                    f"{self.boundary_binary}",
                    "gem5_evidence_boundary_binary_sha256="
                    f"{sha256(self.boundary_binary)}",
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
                    f"--output-dir {self.artifact} "
                    f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
                    f"passthrough_args= --gem5-debug {STRICT_DEBUG_FLAGS}",
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
                    f"gem5_evidence={self.artifact / 'gem5-evidence.tsv'}",
                    "gem5_container_evidence=/cosim-artifacts/gem5-evidence.tsv",
                    "gem5_evidence_test_id=vector_add",
                    f"gem5_evidence_token={UNIT_BOUNDARY_TOKEN}",
                    f"gem5_config_args={UNIT_GEM5_CONFIG_ARGS}",
                    "gem5_docker_image=gem5-run:local",
                    "qemu_binary="
                    f"{self.root / '.local/cosim/qemu/10.1.5/bin/qemu-system-x86_64'}",
                    f"qemu_img={self.qemu_img}",
                    "disk_image="
                    f"{self.root / 'gem5-resources/src/x86-ubuntu-gpu-ml/disk-image/x86-ubuntu-rocm70'}",
                    "kernel="
                    f"{self.root / 'gem5-resources/src/x86-ubuntu-gpu-ml/vmlinux-rocm70'}",
                    "strict_acceptance=1",
                    "host_cpus=4",
                    "gem5_init_timeout=120",
                    f"cwd={self.root}",
                    f"argv0={self.root / 'scripts' / 'cosim_launch.sh'}",
                    f"argv=--share-dir {self.staging} "
                    f"--artifact-dir {self.artifact} "
                    "--evidence-test-id vector_add "
                    f"--evidence-token {UNIT_BOUNDARY_TOKEN} "
                    f"--gem5-debug {STRICT_DEBUG_FLAGS}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.artifact / "guest-run.sh").write_text(
            render_guest_run_script(
                program="vector_add",
                run_id="unit-vector",
                hsa_enable_interrupt="0",
                test_timeout="30",
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
                    f"gem5_config_args={UNIT_GEM5_CONFIG_ARGS}",
                    "artifact_dir_pattern=-",
                    "guest_bridge_policy=artifact-local",
                    f"guest_bridge_host={self.staging}",
                    "guest_bridge_guest=/mnt",
                    "compile_exit_code=0",
                    "test_exit_code=0",
                    "exit_code=0",
                    "pass_count=1",
                    "fail_count=0",
                    "guest_test_started_at=2026-01-01T00:00:01.000000000Z",
                    "guest_test_finished_at=2026-01-01T00:00:02.000000000Z",
                    "gem5_evidence_start_seq=2",
                    "gem5_evidence_end_seq=7",
                    "gem5_evidence_test_id=vector_add",
                    f"gem5_evidence_token={UNIT_BOUNDARY_TOKEN}",
                    "gem5_evidence_boundary_binary="
                    f"{self.boundary_binary}",
                    "gem5_evidence_boundary_binary_sha256="
                    f"{sha256(self.boundary_binary)}",
                    f"qemu_log_sha256={QEMU_LOG_SHA256}",
                    f"gem5_log_sha256={GEM5_LOG_SHA256}",
                    f"gem5_evidence_sha256={GEM5_EVIDENCE_SHA256}",
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
            QEMU_LOG_TEXT,
            encoding="utf-8",
        )
        (self.artifact / "gem5.log").write_text(
            GEM5_LOG_TEXT, encoding="utf-8"
        )
        (self.artifact / "gem5-evidence.tsv").write_text(
            GEM5_EVIDENCE_TEXT, encoding="ascii"
        )
        (self.artifact / "gem5-evidence.tsv").chmod(0o600)
        (self.artifact / "cleanup-status.txt").write_text(
            "result=PASS\nprimary_category=test_pass\nsecondary_category=none\n",
            encoding="utf-8",
        )
        (self.artifact / "docker-inspect.json").write_text(
            json.dumps(
                [
                    {
                        "Name": "/gem5-cosim-unit-vector",
                        "Image": f"sha256:{'e' * 64}",
                        "Config": {"Image": "gem5-run:local"},
                        "Path": GEM5_STDIO_WRAPPER_PATH,
                        "Args": [
                            *GEM5_STDIO_WRAPPER_ARGS,
                            "/gem5/build/VEGA_X86/gem5.opt",
                            *GEM5_DOCKER_ARGS,
                        ],
                        "Mounts": [
                            {
                                "Type": "bind",
                                "Source": str(self.root / "gem5"),
                                "Destination": "/gem5",
                                "RW": True,
                            },
                            {
                                "Type": "bind",
                                "Source": "/tmp",
                                "Destination": "/tmp",
                                "RW": True,
                            },
                            {
                                "Type": "bind",
                                "Source": "/dev/shm",
                                "Destination": "/dev/shm",
                                "RW": True,
                            },
                            {
                                "Type": "bind",
                                "Source": str(self.artifact),
                                "Destination": "/cosim-artifacts",
                                "RW": True,
                            }
                        ],
                        "State": {
                            "Status": "running",
                            "Running": True,
                            "Paused": False,
                            "Restarting": False,
                            "OOMKilled": False,
                            "Dead": False,
                            "ExitCode": 0,
                        },
                        "RestartCount": 0,
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
        preflight_dir = self.artifact / "preflight"
        preflight_dir.mkdir(exist_ok=True)
        preflight = {
            "schema": "cosim-preflight-v1",
            "profile": "run",
            "generated_at": "2026-01-01T00:00:00.100000000Z",
            "repo_root": str(self.root),
            "overall_status": "PASS",
            "required_failure_count": 0,
            "checks": [
                {
                    "id": check_id,
                    "status": "PASS",
                    "required": check_id in RUN_PREFLIGHT_REQUIRED_IDS,
                    "summary": "synthetic",
                    "detail": (
                        "COSIM_STRICT_ACCEPTANCE=1"
                        if check_id == "run.strict_acceptance"
                        else "synthetic"
                    ),
                }
                for check_id in sorted(RUN_PREFLIGHT_CHECK_IDS)
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
                "evidence_boundary_helper": {
                    "ok": True,
                    "path": str(self.boundary_binary),
                    "sha256": sha256(self.boundary_binary),
                },
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
                "gem5_gpu_execution": {"ok": True},
                "program_identity": {
                    "ok": True,
                    "program_binary": "tests/build/vector_add",
                    "program_source": "tests/kernels/vector_add.cpp",
                    "recorded_program": "vector_add",
                    "requested_program": "vector_add",
                    "runner_argument": "vector_add",
                },
                "qemu_completion": {"ok": True},
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
                "gem5_evidence": str(self.artifact / "gem5-evidence.tsv"),
                "gem5_log": str(self.artifact / "gem5.log"),
                "guest_log": str(self.artifact / "qemu.log"),
                "metadata": str(self.artifact / "runner-metadata.txt"),
                "qemu_log": str(self.artifact / "qemu.log"),
                "runner_invocation": str(
                    self.artifact / "runner-invocation.txt"
                ),
                "source_snapshot": str(self.patch / "source-snapshot.txt"),
            },
            "exit_code": 0,
            "outcome": "PASS",
            "program": "vector_add",
            "provenance": {
                "gem5_binary": str(self.gem5_binary),
                "gem5_sha256": sha256(self.gem5_binary),
                "gem5_source_commit": self.gem5_commit,
                "gem5_evidence_boundary_binary": str(self.boundary_binary),
                "gem5_evidence_boundary_binary_sha256": sha256(
                    self.boundary_binary
                ),
                "gem5_evidence_sha256": GEM5_EVIDENCE_SHA256,
                "gem5_log_sha256": GEM5_LOG_SHA256,
                "qemu_log_sha256": QEMU_LOG_SHA256,
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
            "gem5_config_args": UNIT_GEM5_CONFIG_ARGS,
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
            "gem5_evidence_boundary_binary": str(self.boundary_binary),
            "gem5_evidence_boundary_binary_sha256": sha256(
                self.boundary_binary
            ),
            "source_fingerprint": snapshot["source_fingerprint"],
            "strict_acceptance": "1",
            "mode": "pure_test",
            "repeat_count": "1",
            "timeout_policy": "fixed-30",
            "boot_timeout": "240",
            "test_timeout": "30",
            "guest_run_timeout": "1800",
            "guest_test_prefix": "HSA_ENABLE_INTERRUPT=0",
            "gem5_config_args": UNIT_GEM5_CONFIG_ARGS,
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

    def synchronize_gem5_log_hash(self) -> str:
        digest = sha256(self.fixture.artifact / "gem5.log")
        replace_key_value(
            self.fixture.artifact / "runner-metadata.txt",
            "gem5_log_sha256",
            digest,
        )
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["provenance"]["gem5_log_sha256"] = digest
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return digest

    def synchronize_gem5_evidence_hash(self, end_seq: str = "7") -> str:
        digest = sha256(self.fixture.artifact / "gem5-evidence.tsv")
        metadata_path = self.fixture.artifact / "runner-metadata.txt"
        replace_key_value(metadata_path, "gem5_evidence_sha256", digest)
        replace_key_value(metadata_path, "gem5_evidence_end_seq", end_seq)
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["provenance"]["gem5_evidence_sha256"] = digest
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return digest

    def synchronize_qemu_log_hash(self) -> str:
        digest = sha256(self.fixture.artifact / "qemu.log")
        replace_key_value(
            self.fixture.artifact / "runner-metadata.txt",
            "qemu_log_sha256",
            digest,
        )
        verdict_path = self.fixture.artifact / "verdict.json"
        verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
        verdict["provenance"]["qemu_log_sha256"] = digest
        verdict_path.write_text(
            json.dumps(verdict, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return digest

    def mutate_docker_inspect(self, mutate) -> None:
        path = self.fixture.artifact / "docker-inspect.json"
        payload = json.loads(path.read_text(encoding="utf-8"))
        mutate(payload[0])
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
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
        self.synchronize_qemu_log_hash()
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
            f"--output-dir {self.fixture.artifact} "
            f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
            f"--output-dir {self.fixture.artifact} {option} {value} "
            f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
        ).replace(
            f"passthrough_args= --gem5-debug {STRICT_DEBUG_FLAGS}",
            f"passthrough_args= {option} {value} "
            f"--gem5-debug {STRICT_DEBUG_FLAGS}",
        )
        runner.write_text(runner_text, encoding="utf-8")
        launcher = self.fixture.artifact / "launch-invocation.txt"
        launcher.write_text(
            launcher.read_text(encoding="utf-8").replace(
                f"--artifact-dir {self.fixture.artifact} "
                "--evidence-test-id vector_add "
                f"--evidence-token {UNIT_BOUNDARY_TOKEN} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS}",
                f"--artifact-dir {self.fixture.artifact} "
                "--evidence-test-id vector_add "
                f"--evidence-token {UNIT_BOUNDARY_TOKEN} {option} {value} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS}",
            ),
            encoding="utf-8",
        )

    def replace_runtime_gem5_arg(self, old: str, new: str) -> None:
        def replace_docker_arg(container) -> None:
            self.assertEqual(1, container["Args"].count(old))
            container["Args"][container["Args"].index(old)] = new

        self.mutate_docker_inspect(replace_docker_arg)
        gem5_log = self.fixture.artifact / "gem5.log"
        text = gem5_log.read_text(encoding="utf-8")
        self.assertEqual(1, text.count(old))
        gem5_log.write_text(text.replace(old, new), encoding="utf-8")
        self.synchronize_gem5_log_hash()

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
            "defaults:num-gpus=1,num-cus=0,host-mem=8G,vram-size=16GiB;"
            f"debug-flags={STRICT_DEBUG_FLAGS}"
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
                f"--output-dir {self.fixture.artifact} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
                f"--output-dir {self.fixture.artifact} "
                "--session-name ../../unsafe "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
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
                f"--output-dir {self.fixture.artifact} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
                f"--output-dir {self.fixture.artifact} "
                "--num-cus $'2\\t3' "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
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

    def test_replaced_boundary_helper_with_only_binary_provenance_update_fails(
        self,
    ) -> None:
        self.fixture.boundary_binary.write_bytes(b"replaced executable helper\n")
        self.fixture.boundary_binary.chmod(0o755)
        replace_key_value(
            self.fixture.patch / "binary-provenance.txt",
            "gem5_evidence_boundary_binary_sha256",
            sha256(self.fixture.boundary_binary),
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))

    def test_duplicate_boundary_ready_marker_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        marker = (
            f"__{UNIT_BOUNDARY_READY_TOKEN}__:"
            f"{UNIT_BOUNDARY_BINARY_SHA256}\n"
        )
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(marker, marker * 2),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_qemu_sequence", error_codes(result))

    def test_boundary_ready_marker_after_pass_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        marker = (
            f"__{UNIT_BOUNDARY_READY_TOKEN}__:"
            f"{UNIT_BOUNDARY_BINARY_SHA256}\n"
        )
        text = qemu_log.read_text(encoding="utf-8")
        qemu_log.write_text(
            text.replace(f"{marker}[PASS] vector_add\n", "")
            .replace(
                f"__{UNIT_TEST_TOKEN}__:0\n",
                f"[PASS] vector_add\n{marker}__{UNIT_TEST_TOKEN}__:0\n",
            ),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_qemu_sequence", error_codes(result))

    def test_boundary_ready_marker_hash_mismatch_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                f"__{UNIT_BOUNDARY_READY_TOKEN}__:"
                f"{UNIT_BOUNDARY_BINARY_SHA256}",
                f"__{UNIT_BOUNDARY_READY_TOKEN}__:{'f' * 64}",
            ),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_qemu_sequence", error_codes(result))

    def test_guest_script_preceding_exit_fails(self) -> None:
        guest_script = self.fixture.artifact / "guest-run.sh"
        guest_script.write_text(
            guest_script.read_text(encoding="utf-8").replace(
                "#!/bin/bash\n", "#!/bin/bash\nexit 0\n", 1
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("guest_script_mismatch", error_codes(result))

    def test_guest_script_missing_build_and_rc_fails(self) -> None:
        guest_script = self.fixture.artifact / "guest-run.sh"
        guest_script.write_text(
            guest_script.read_text(encoding="utf-8").replace(
                "make -j1\nbuild_rc=$?\n", "", 1
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("guest_script_mismatch", error_codes(result))

    def test_guest_script_extra_completion_token_fails(self) -> None:
        guest_script = self.fixture.artifact / "guest-run.sh"
        with guest_script.open("a", encoding="utf-8") as handle:
            handle.write(f'echo "__{UNIT_TEST_TOKEN}__:${{rc}}"\n')

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("guest_script_mismatch", error_codes(result))

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
            "defaults:num-gpus=1,num-cus=2,host-mem=8G,vram-size=16GiB;"
            f"evidence-test-id=vector_add,evidence-token={UNIT_BOUNDARY_TOKEN};"
            f"debug-flags={STRICT_DEBUG_FLAGS}"
        )
        self.replace_runtime_gem5_arg(
            "--num-compute-units=40", "--num-compute-units=2"
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

    def test_verifier_rescan_rejects_fatal_appended_after_verdict(self) -> None:
        with (self.fixture.artifact / "gem5.log").open(
            "a", encoding="utf-8"
        ) as handle:
            handle.write(
                "2026-01-01T00:00:01.400000000Z "
                "src/base/logging.cc:10: fatal: appended after verdict\n"
            )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("simulator_fatal", error_codes(result))
        self.assertIn("gem5_log_hash_mismatch", error_codes(result))

    def test_verifier_rescan_rejects_log_without_workgroup_dispatch(self) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        text = "\n".join(
            line
            for line in evidence.read_text(encoding="ascii").splitlines()
            if "workgroup_dispatch" not in line
        ) + "\n"
        text = text.replace("\t5\t12\tworkgroup_complete", "\t4\t12\tworkgroup_complete")
        text = text.replace("\t6\t13\tkernel_complete", "\t5\t13\tkernel_complete")
        text = text.replace("\t7\t14\ttest_end", "\t6\t14\ttest_end")
        evidence.write_text(text, encoding="ascii")
        self.synchronize_gem5_evidence_hash("6")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_verifier_rejects_missing_kernel_completion(self) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        evidence.write_text(
            "\n".join(
                line
                for line in evidence.read_text(encoding="ascii").splitlines()
                if "kernel_complete" not in line
            )
            .replace("\t7\t14\ttest_end", "\t6\t14\ttest_end")
            + "\n",
            encoding="ascii",
        )
        self.synchronize_gem5_evidence_hash("6")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_verifier_rejects_wrong_kernel_completion_id(self) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        evidence.write_text(
            evidence.read_text(encoding="ascii").replace(
                "kernel_complete\t0\t0\t-1\t-1",
                "kernel_complete\t0\t1\t-1\t-1",
            ),
            encoding="ascii",
        )
        self.synchronize_gem5_evidence_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_verifier_rejects_wrong_workgroup_completion_id(self) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        evidence.write_text(
            evidence.read_text(encoding="ascii").replace(
                "workgroup_complete\t0\t0\t0\t-1",
                "workgroup_complete\t0\t0\t99\t-1",
            ),
            encoding="ascii",
        )
        self.synchronize_gem5_evidence_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_verifier_rejects_kernel_completion_before_workgroup_completion(
        self,
    ) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        lines = evidence.read_text(encoding="ascii").splitlines()
        completion = lines[6].replace("\t5\t12\t", "\t6\t13\t")
        kernel_completion = lines[7].replace("\t6\t13\t", "\t5\t12\t")
        evidence.write_text(
            "\n".join(
                (*lines[:6], kernel_completion, completion, *lines[8:])
            )
            + "\n",
            encoding="ascii",
        )
        self.synchronize_gem5_evidence_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_verifier_rejects_complete_gpu_chain_appended_after_test_end(
        self,
    ) -> None:
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        with evidence.open("a", encoding="ascii") as handle:
            handle.write(
                "COSIM_GPU_EVIDENCE_V1\tunit-vector\t8\t15\t"
                "kernel_launch\t0\t1\t-1\t-1\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-vector\t9\t16\t"
                "workgroup_dispatch\t0\t1\t0\t0\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-vector\t10\t17\t"
                "workgroup_complete\t0\t1\t0\t-1\n"
                "COSIM_GPU_EVIDENCE_V1\tunit-vector\t11\t18\t"
                "kernel_complete\t0\t1\t-1\t-1\n"
            )
        self.synchronize_gem5_evidence_hash("11")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_evidence_window", error_codes(result))

    def test_verifier_rejects_swapped_run_scoped_gem5_log(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            GEM5_LOG_TEXT.replace("unit-vector", "other-row"),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_command_identity_mismatch", error_codes(result))

    def test_expected_qemu_sigterm_and_running_container_are_accepted(self) -> None:
        result = self.verify()
        self.assertEqual("PASS", result["outcome"])
        self.assertNotIn("simulator_fatal", error_codes(result))

    def test_readline_prefix_on_hsa_marker_is_accepted(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "[COSIM_ENV] HSA_ENABLE_INTERRUPT=0",
                "\x1b[?2004l\r[COSIM_ENV] HSA_ENABLE_INTERRUPT=0",
            ),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("PASS", result["outcome"])

    def test_readline_prefix_cannot_hide_native_fatal(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        with qemu_log.open("a", encoding="utf-8") as handle:
            handle.write(
                "\x1b[?2004l\rSegmentation fault (core dumped)\n"
            )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("simulator_fatal", error_codes(result))

    def test_invalid_qemu_encoding_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        with qemu_log.open("ab") as handle:
            handle.write(b"\xff\n")
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("qemu_completion_unproven", error_codes(result))

    def test_qemu_signal_9_or_11_after_completion_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        baseline = qemu_log.read_text(encoding="utf-8")
        for signal_number in (9, 11):
            with self.subTest(signal_number=signal_number):
                qemu_log.write_text(
                    baseline.replace(
                        "terminating on signal 15",
                        f"terminating on signal {signal_number}",
                    ),
                    encoding="utf-8",
                )
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("simulator_fatal", error_codes(result))

    def test_qemu_sigterm_before_completion_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                f"__{UNIT_TEST_TOKEN}__:0\n"
                f"{QEMU_SIGTERM_LINE}\n",
                f"{QEMU_SIGTERM_LINE}\n"
                f"__{UNIT_TEST_TOKEN}__:0\n",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("simulator_fatal", error_codes(result))

    def test_stopped_docker_container_is_rejected(self) -> None:
        def stop(container) -> None:
            container["State"].update(
                {"Status": "exited", "Running": False, "ExitCode": 1}
            )

        self.mutate_docker_inspect(stop)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "docker_inspect.state.Running" in detail
                for detail in error_details(result)
            )
        )

    def test_oom_killed_docker_container_is_rejected(self) -> None:
        def mark_oom(container) -> None:
            container["State"]["OOMKilled"] = True

        self.mutate_docker_inspect(mark_oom)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "docker_inspect.state.OOMKilled" in detail
                for detail in error_details(result)
            )
        )

    def test_restarted_docker_container_is_rejected(self) -> None:
        def increment_restart(container) -> None:
            container["RestartCount"] = 1

        self.mutate_docker_inspect(increment_restart)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "docker_inspect.restart_count" in detail
                for detail in error_details(result)
            )
        )

    def test_nested_gem5_evidence_mount_is_rejected(self) -> None:
        def add_nested_mount(container) -> None:
            container["Mounts"].append(
                {
                    "Type": "bind",
                    "Source": str(self.fixture.root / "forged-evidence.tsv"),
                    "Destination": "/cosim-artifacts/gem5-evidence.tsv",
                    "RW": True,
                }
            )

        self.mutate_docker_inspect(add_nested_mount)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_evidence_mount", error_codes(result))

    def test_each_required_gem5_mount_is_mandatory(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        baseline = inspect_path.read_text(encoding="utf-8")
        for destination in ("/gem5", "/tmp", "/dev/shm", "/cosim-artifacts"):
            with self.subTest(destination=destination):
                inspect_path.write_text(baseline, encoding="utf-8")

                def remove_mount(container) -> None:
                    container["Mounts"] = [
                        mount
                        for mount in container["Mounts"]
                        if mount["Destination"] != destination
                    ]

                self.mutate_docker_inspect(remove_mount)
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("invalid_gem5_mount_set", error_codes(result))

    def test_each_required_gem5_mount_rejects_wrong_source(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        baseline = inspect_path.read_text(encoding="utf-8")
        for destination in ("/gem5", "/tmp", "/dev/shm", "/cosim-artifacts"):
            with self.subTest(destination=destination):
                inspect_path.write_text(baseline, encoding="utf-8")

                def replace_source(container) -> None:
                    mount = next(
                        item
                        for item in container["Mounts"]
                        if item["Destination"] == destination
                    )
                    mount["Source"] = str(self.fixture.root / "forged-mount")

                self.mutate_docker_inspect(replace_source)
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                expected_code = (
                    "invalid_gem5_evidence_mount"
                    if destination == "/cosim-artifacts"
                    else "invalid_gem5_mount"
                )
                self.assertIn(expected_code, error_codes(result))

    def test_each_required_gem5_mount_rejects_duplicate_target(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        baseline = inspect_path.read_text(encoding="utf-8")
        for destination in ("/gem5", "/tmp", "/dev/shm", "/cosim-artifacts"):
            with self.subTest(destination=destination):
                inspect_path.write_text(baseline, encoding="utf-8")

                def duplicate_mount(container) -> None:
                    mount = next(
                        item
                        for item in container["Mounts"]
                        if item["Destination"] == destination
                    )
                    container["Mounts"].append(dict(mount))

                self.mutate_docker_inspect(duplicate_mount)
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("invalid_gem5_mount_set", error_codes(result))

    def test_gem5_mount_type_and_rw_are_exact(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        baseline = inspect_path.read_text(encoding="utf-8")
        for field, forged in (("Type", "volume"), ("RW", False)):
            with self.subTest(field=field):
                inspect_path.write_text(baseline, encoding="utf-8")

                def replace_attribute(container) -> None:
                    mount = next(
                        item
                        for item in container["Mounts"]
                        if item["Destination"] == "/gem5"
                    )
                    mount[field] = forged

                self.mutate_docker_inspect(replace_attribute)
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("invalid_gem5_mount", error_codes(result))

    def test_each_protected_gem5_mount_rejects_nested_shadow(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        baseline = inspect_path.read_text(encoding="utf-8")
        for destination in ("/gem5", "/tmp", "/dev/shm", "/cosim-artifacts"):
            with self.subTest(destination=destination):
                inspect_path.write_text(baseline, encoding="utf-8")

                def add_shadow(container) -> None:
                    container["Mounts"].append(
                        {
                            "Type": "bind",
                            "Source": str(self.fixture.root / "shadow"),
                            "Destination": f"{destination}/shadow",
                            "RW": True,
                        }
                    )

                self.mutate_docker_inspect(add_shadow)
                result = self.verify()
                self.assertEqual("FAIL", result["outcome"])
                expected_code = (
                    "invalid_gem5_evidence_mount"
                    if destination == "/cosim-artifacts"
                    else "invalid_gem5_mount"
                )
                self.assertIn(expected_code, error_codes(result))

    def test_swapped_docker_args_are_rejected(self) -> None:
        def swap_args(container) -> None:
            socket_index = next(
                index
                for index, value in enumerate(container["Args"])
                if value.startswith("--socket-path=")
            )
            container["Args"][socket_index] = (
                "--socket-path=/tmp/gem5-mi300x-other-row.sock"
            )

        self.mutate_docker_inspect(swap_args)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))

    def test_coordinated_runtime_gem5_value_drift_is_rejected(self) -> None:
        self.replace_runtime_gem5_arg(
            "--num-compute-units=40", "--num-compute-units=2"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))
        self.assertIn("gem5_reported_argv_mismatch", error_codes(result))

    def test_coordinated_missing_gem5_arg_is_rejected(self) -> None:
        missing = "--listener-mode=on"

        def remove_arg(container) -> None:
            container["Args"].remove(missing)

        self.mutate_docker_inspect(remove_arg)
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(f" {missing}", ""),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))
        self.assertIn("gem5_reported_argv_mismatch", error_codes(result))

    def test_coordinated_extra_gem5_arg_is_rejected(self) -> None:
        extra = "--forged-option=1"

        def add_arg(container) -> None:
            container["Args"].append(extra)

        self.mutate_docker_inspect(add_arg)
        gem5_log = self.fixture.artifact / "gem5.log"
        token = f"--evidence-token={UNIT_BOUNDARY_TOKEN}"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                f"{token}\n", f"{token} {extra}\n"
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))
        self.assertIn("gem5_reported_argv_mismatch", error_codes(result))

    def test_coordinated_duplicate_gem5_arg_is_rejected(self) -> None:
        duplicate = "--num-gpus=1"

        def duplicate_arg(container) -> None:
            index = container["Args"].index(duplicate)
            container["Args"].insert(index + 1, duplicate)

        self.mutate_docker_inspect(duplicate_arg)
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                f" {duplicate} ", f" {duplicate} {duplicate} "
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))
        self.assertIn("gem5_reported_argv_mismatch", error_codes(result))

    def test_coordinated_misordered_gem5_args_are_rejected(self) -> None:
        first = "--mem-size=8G"
        second = "--num-gpus=1"

        def swap_order(container) -> None:
            first_index = container["Args"].index(first)
            second_index = container["Args"].index(second)
            container["Args"][first_index], container["Args"][second_index] = (
                container["Args"][second_index],
                container["Args"][first_index],
            )

        self.mutate_docker_inspect(swap_order)
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                f"{first} {second}", f"{second} {first}"
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("docker_gem5_argv_mismatch", error_codes(result))
        self.assertIn("gem5_reported_argv_mismatch", error_codes(result))

    def test_duplicate_docker_evidence_option_is_rejected(self) -> None:
        duplicate = "--evidence-run-id=unit-vector"

        def duplicate_arg(container) -> None:
            container["Args"].append(duplicate)

        self.mutate_docker_inspect(duplicate_arg)
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "--evidence-run-id=unit-vector ",
                f"--evidence-run-id=unit-vector {duplicate} ",
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_command_identity_mismatch", error_codes(result))

    def test_launcher_uses_verified_gem5_stdio_wrapper(self) -> None:
        launcher = (REPO_ROOT / "scripts/cosim_launch.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "    /bin/sh\n"
            "    -c\n"
            "    'exec \"$@\" 2>&1'\n"
            "    cosim-gem5\n"
            "    \"$C_GEM5_BIN\"\n",
            launcher,
        )

    def test_missing_gem5_stdio_wrapper_is_rejected(self) -> None:
        def remove_wrapper(container) -> None:
            container["Path"] = "/gem5/build/VEGA_X86/gem5.opt"
            container["Args"] = list(GEM5_DOCKER_ARGS)

        self.mutate_docker_inspect(remove_wrapper)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_stdio_wrapper", error_codes(result))

    def test_modified_gem5_stdio_wrapper_is_rejected(self) -> None:
        def modify_wrapper(container) -> None:
            container["Args"][1] = 'exec "$@"'

        self.mutate_docker_inspect(modify_wrapper)
        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_stdio_wrapper", error_codes(result))

    def test_empty_manifest_and_matrix_fail_closed(self) -> None:
        write_tsv(self.fixture.manifest, MANIFEST_FIELDS, [])
        write_tsv(self.fixture.matrix, MATRIX_FIELDS, [])

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("empty_manifest", error_codes(result))
        self.assertIn("empty_matrix", error_codes(result))

    def test_expected_row_spec_must_match_tracked_head(self) -> None:
        spec = (
            self.fixture.root / "configs/cosim/strict-acceptance-rows.json"
        )
        spec.write_text(spec.read_text(encoding="utf-8") + "\n", encoding="utf-8")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("expected_rows_differs_from_head", error_codes(result))

    def test_accepted_row_counter_rejects_duplicate_semantics(self) -> None:
        duplicate = dict(self.fixture.manifest_rows[0])
        duplicate["row_id"] = "duplicate-vector"
        self.fixture.manifest_rows.append(duplicate)
        self.fixture.flush()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("expected_rows_mismatch", error_codes(result))

    def test_repository_expected_rows_freeze_exact_eight_row_contract(self) -> None:
        payload = json.loads(
            (
                REPO_ROOT / "configs/cosim/strict-acceptance-rows.json"
            ).read_text(encoding="utf-8")
        )
        actual = {
            (row["program"], row["expected_hsa_interrupt"])
            for row in payload["rows"]
        }
        self.assertEqual(
            {
                ("gemm", "0"),
                ("histogram", "0"),
                ("multi_gpu_verify", "0"),
                ("prefix_scan", "0"),
                ("reduction", "0"),
                ("transpose", "0"),
                ("vector_add", "0"),
                ("vector_add", "1"),
            },
            actual,
        )
        self.assertEqual(8, len(payload["rows"]))

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
        self.assertIn("guest_script_mismatch", error_codes(result))

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
                f"--output-dir {self.fixture.artifact} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
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

    def test_quoted_empty_passthrough_is_not_zero_arguments(self) -> None:
        runner_invocation = self.fixture.artifact / "runner-invocation.txt"
        runner_invocation.write_text(
            runner_invocation.read_text(encoding="utf-8").replace(
                f"passthrough_args= --gem5-debug {STRICT_DEBUG_FLAGS}\n",
                f"passthrough_args= '' --gem5-debug {STRICT_DEBUG_FLAGS}\n",
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invocation_passthrough_mismatch", error_codes(result))
        self.assertIn("invocation_argv_mismatch", error_codes(result))

    def test_runner_argv_passthrough_not_launched_fails(self) -> None:
        invocation = self.fixture.artifact / "runner-invocation.txt"
        invocation.write_text(
            invocation.read_text(encoding="utf-8").replace(
                f"--output-dir {self.fixture.artifact} "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
                f"--output-dir {self.fixture.artifact} --qemu-trace forged "
                f"--gem5-debug {STRICT_DEBUG_FLAGS} vector_add",
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

    def test_matching_legacy_runner_argument_is_accepted(self) -> None:
        metadata = self.fixture.artifact / "runner-metadata.txt"
        with metadata.open("a", encoding="utf-8") as handle:
            handle.write("runner_arg=vector_add\n")

        result = self.verify()

        self.assertEqual("PASS", result["outcome"])

    def test_duplicate_runner_identity_keeps_generic_duplicate_gate(self) -> None:
        metadata = self.fixture.artifact / "runner-metadata.txt"
        with metadata.open("a", encoding="utf-8") as handle:
            handle.write("runner_argument=vector_add\n")

        result = self.verify()

        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("duplicate_key", error_codes(result))
        self.assertIn(
            "runner_metadata:runner_argument",
            error_details(result),
        )

    def test_legacy_runner_argument_fails_closed(self) -> None:
        metadata = self.fixture.artifact / "runner-metadata.txt"
        original = metadata.read_text(encoding="utf-8")
        for runner_arg in (
            "",
            " vector_add",
            "vector_add ",
            "\tvector_add",
            "vector_add\t",
            "reduction",
        ):
            with self.subTest(runner_arg=runner_arg):
                metadata.write_text(
                    original + f"runner_arg={runner_arg}\n",
                    encoding="utf-8",
                )

                result = self.verify()

                self.assertEqual("FAIL", result["outcome"])
                self.assertIn("value_mismatch", error_codes(result))
                self.assertTrue(
                    any(
                        "metadata.runner_arg" in detail
                        for detail in error_details(result)
                    )
                )

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
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("expected_rows_mismatch", error_codes(result))
        self.assertNotIn("noncanonical_guest_prefix_input", error_codes(result))

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

    def test_guest_metadata_lock_and_patch_hashes_are_joined(self) -> None:
        metadata_path = self.fixture.artifact / "guest-build-meta.txt"
        replace_key_value(
            metadata_path, "guest_lock_sha256", "1" * 64
        )
        replace_key_value(
            metadata_path, "overlay_patch_sha256", "2" * 64
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        details = error_details(result)
        self.assertTrue(
            any("guest_metadata.guest_lock_sha256" in item for item in details)
        )
        self.assertTrue(
            any(
                "guest_metadata.overlay_patch_sha256" in item
                for item in details
            )
        )

    def test_same_size_guest_image_replacement_is_rejected(self) -> None:
        original = self.fixture.disk_image.stat()
        replacement = self.fixture.disk_image.with_name("replacement-image")
        replacement.write_bytes(b"X" * original.st_size)
        os.utime(
            replacement,
            ns=(original.st_atime_ns, original.st_mtime_ns),
        )
        os.replace(replacement, self.fixture.disk_image)
        self.assertEqual(
            original.st_size, self.fixture.disk_image.stat().st_size
        )
        self.assertEqual(
            original.st_mtime_ns,
            self.fixture.disk_image.stat().st_mtime_ns,
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))
        self.assertTrue(
            any(
                "guest_metadata.image_sha256" in item
                for item in error_details(result)
            )
        )

    def test_guest_pre_stat_drift_is_rejected(self) -> None:
        pre_path = self.fixture.artifact / "guest-base-stat-pre.json"
        pre_stat = json.loads(pre_path.read_text(encoding="utf-8"))
        pre_stat["image"]["inode"] += 1
        pre_path.write_text(
            json.dumps(pre_stat, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "guest_pre_stat.image.inode" in item
                for item in error_details(result)
            )
        )

    def test_guest_post_stat_drift_is_rejected(self) -> None:
        post_path = self.fixture.artifact / "guest-base-stat-post.json"
        post_stat = json.loads(post_path.read_text(encoding="utf-8"))
        post_stat["matches_pre"] = False
        post_path.write_text(
            json.dumps(post_stat, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "guest_post_stat.matches_pre" in item
                for item in error_details(result)
            )
        )

    def test_truncated_run_preflight_is_rejected(self) -> None:
        preflight_path = self.fixture.artifact / "preflight" / "preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        preflight["checks"] = [
            check
            for check in preflight["checks"]
            if check["id"]
            in {"run.gem5_provenance", "run.qemu_provenance"}
        ]
        preflight_path.write_text(
            json.dumps(preflight, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_run_preflight", error_codes(result))

    def test_invalid_guest_metadata_timestamp_and_artifacts_are_rejected(
        self,
    ) -> None:
        metadata_path = self.fixture.artifact / "guest-build-meta.txt"
        replace_key_value(metadata_path, "timestamp", "not-a-time")
        replace_key_value(metadata_path, "artifacts", "/tmp/forged-build")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_metadata", error_codes(result))

    def test_invalid_guest_seal_timestamp_is_rejected(self) -> None:
        replace_key_value(
            self.fixture.artifact / "guest-content-seal.txt",
            "sealed_at",
            "not-a-time",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_content_seal", error_codes(result))

    def test_invalid_guest_lock_fingerprint_is_rejected(self) -> None:
        replace_key_value(
            self.fixture.artifact / "guest.lock",
            "ROCM_KEY_FINGERPRINT",
            "lowercase-is-not-valid",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_lock", error_codes(result))

    def test_invalid_preflight_timestamp_is_rejected(self) -> None:
        preflight_path = self.fixture.artifact / "preflight" / "preflight.json"
        preflight = json.loads(preflight_path.read_text(encoding="utf-8"))
        preflight["generated_at"] = "not-a-time"
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

    def test_guest_lifecycle_timestamps_must_be_ordered(self) -> None:
        post_path = self.fixture.artifact / "guest-base-stat-post.json"
        post = json.loads(post_path.read_text(encoding="utf-8"))
        post["captured_at"] = "2026-01-01T00:00:00.500000000Z"
        post_path.write_text(
            json.dumps(post, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_lifecycle_order", error_codes(result))

    def test_preflight_timestamp_must_precede_guest_validation(self) -> None:
        for report_path in (
            self.fixture.artifact / "guest-provenance.json",
            self.fixture.artifact / "preflight/guest-provenance.json",
        ):
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["validated_at"] = "2026-01-01T00:00:00.050000000Z"
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_lifecycle_order", error_codes(result))

    def test_same_binary_qemu_metadata_refresh_after_guest_is_allowed(self) -> None:
        for metadata_path in (
            self.fixture.qemu_build_meta,
            self.fixture.artifact / "qemu-build-meta.txt",
        ):
            replace_key_value(
                metadata_path,
                "timestamp",
                "2026-01-01T00:00:00.075000000Z",
            )

        result = self.verify()
        self.assertEqual("PASS", result["outcome"])

    def test_qemu_metadata_timestamp_must_precede_preflight(self) -> None:
        for metadata_path in (
            self.fixture.qemu_build_meta,
            self.fixture.artifact / "qemu-build-meta.txt",
        ):
            replace_key_value(
                metadata_path,
                "timestamp",
                "2026-01-01T00:00:00.150000000Z",
            )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_guest_lifecycle_order", error_codes(result))

    def test_guest_builder_identity_is_joined_to_head(self) -> None:
        replace_key_value(
            self.fixture.artifact / "guest-build-meta.txt",
            "build_script_sha256",
            "f" * 64,
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "metadata.build_script_sha256" in detail
                for detail in error_details(result)
            )
        )

    def test_qemu_configure_fingerprint_is_independently_rebuilt(self) -> None:
        for path in (
            self.fixture.qemu_build_meta,
            self.fixture.artifact / "qemu-build-meta.txt",
        ):
            replace_key_value(path, "configure_fingerprint", "f" * 64)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "qemu_meta.configure_fingerprint" in detail
                for detail in error_details(result)
            )
        )

    def test_qemu_source_tree_fingerprint_is_independently_rebuilt(self) -> None:
        (self.fixture.qemu_source / "README.rst").write_text(
            "mutated qemu source\n", encoding="utf-8"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "qemu_lock.source_fingerprint" in detail
                for detail in error_details(result)
            )
        )

    def test_qemu_source_rewrite_cannot_rekey_mutable_metadata(self) -> None:
        (self.fixture.qemu_source / "README.rst").write_text(
            "coordinated qemu source rewrite\n", encoding="utf-8"
        )
        source_fingerprint = _directory_fingerprint(
            self.fixture.qemu_source, {}
        )
        configure_fingerprint = _array_fingerprint(
            (
                f"--prefix={self.fixture.root / '.local/cosim/qemu/10.1.5'}",
                "--target-list=x86_64-softmmu",
                "--disable-download",
                "--disable-docs",
                "--disable-gtk",
                "--disable-sdl",
                "--disable-werror",
                "--enable-kvm",
                "--enable-slirp",
                "--enable-tools",
                "--enable-virtfs",
            )
        )
        build_fingerprint = _array_fingerprint(
            ("1" * 64, source_fingerprint, configure_fingerprint)
        )
        for path in (
            self.fixture.qemu_build_meta,
            self.fixture.artifact / "qemu-build-meta.txt",
        ):
            replace_key_value(
                path, "initial_source_fingerprint", source_fingerprint
            )
            replace_key_value(path, "source_fingerprint", source_fingerprint)
            replace_key_value(path, "build_fingerprint", build_fingerprint)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "qemu_lock.source_fingerprint" in detail
                for detail in error_details(result)
            )
        )

    def test_qemu_toolchain_lock_must_equal_head(self) -> None:
        for path in (
            self.fixture.toolchain_lock,
            self.fixture.artifact / "toolchain.lock",
        ):
            replace_key_value(path, "QEMU_SOURCE_SHA256", "2" * 64)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertTrue(
            any(
                "qemu_toolchain_lock.archive_head_sha256" in detail
                for detail in error_details(result)
            )
        )

    def test_qemu_binary_hash_is_independently_verified(self) -> None:
        self.fixture.qemu_binary.write_bytes(b"mutated qemu binary\n")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("hash_mismatch", error_codes(result))

    def test_gem5_build_timestamp_must_be_rfc3339nano(self) -> None:
        replace_key_value(
            self.fixture.patch / "gem5-build-meta.txt", "timestamp", "not-a-time"
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_build_metadata", error_codes(result))

    def test_nested_duplicate_json_key_is_rejected(self) -> None:
        verdict_path = self.fixture.artifact / "verdict.json"
        text = verdict_path.read_text(encoding="utf-8")
        verdict_path.write_text(
            text.replace(
                '"effective_environment": {',
                '"effective_environment": {"ok": false,',
                1,
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("json_read_error", error_codes(result))

    def test_unknown_tsv_column_is_rejected(self) -> None:
        rows = [{**row, "forged": "value"} for row in self.fixture.manifest_rows]
        write_tsv(
            self.fixture.manifest,
            (*MANIFEST_FIELDS, "forged"),
            rows,
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("manifest_error", error_codes(result))

    def test_stale_qemu_run_marker_is_rejected_after_hashes_are_coordinated(
        self,
    ) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                "  Run-ID:     unit-vector", "  Run-ID:     stale-vector"
            ),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("qemu_run_identity_mismatch", error_codes(result))

    def test_qemu_pass_after_sigterm_is_rejected_after_hashes_are_coordinated(
        self,
    ) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        text = qemu_log.read_text(encoding="utf-8")
        text = text.replace("[PASS] vector_add\n", "")
        text += "[PASS] vector_add\n"
        qemu_log.write_text(text, encoding="utf-8")
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_qemu_sequence", error_codes(result))

    def test_qemu_completion_token_for_another_run_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        qemu_log.write_text(
            qemu_log.read_text(encoding="utf-8").replace(
                UNIT_RUN_SHA256, "f" * 64
            ),
            encoding="utf-8",
        )
        self.synchronize_qemu_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_qemu_sequence", error_codes(result))

    def test_qemu_log_symlink_is_rejected(self) -> None:
        qemu_log = self.fixture.artifact / "qemu.log"
        target = self.fixture.artifact / "qemu-target.log"
        qemu_log.rename(target)
        qemu_log.symlink_to(target)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("symlink_not_allowed", error_codes(result))

    def test_native_gem5_abort_after_gpu_completion_is_rejected(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8")
            + "2026-01-01T00:00:01.500000000Z Program aborted at tick 42\n",
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("simulator_fatal", error_codes(result))

    def test_native_gem5_segfault_before_gpu_completion_is_rejected(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        text = gem5_log.read_text(encoding="utf-8")
        needle = "2026-01-01T00:00:01.400000000Z 13: dispatcher: Completed kernel 0\n"
        gem5_log.write_text(
            text.replace(
                needle,
                "2026-01-01T00:00:01.350000000Z "
                "gem5 has encountered a segmentation fault!\n"
                + needle,
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("simulator_fatal", error_codes(result))

    def test_gem5_command_and_client_after_gpu_sequence_are_rejected(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        lines = gem5_log.read_text(encoding="utf-8").splitlines()
        command = lines.pop(0).replace(
            "2026-01-01T00:00:00.100000000Z",
            "2026-01-01T00:00:01.500000000Z",
        )
        client = lines.pop(0).replace(
            "2026-01-01T00:00:00.200000000Z",
            "2026-01-01T00:00:01.600000000Z",
        )
        gem5_log.write_text(
            "\n".join([*lines, command, client]) + "\n", encoding="utf-8"
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_causal_chain_unproven", error_codes(result))

    def test_warning_text_cannot_supply_gpu_acceptance_events(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
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
        self.synchronize_gem5_log_hash()
        evidence = self.fixture.artifact / "gem5-evidence.tsv"
        evidence.write_text(
            "schema\trun_id\tseq\ttick\tevent\tgpu\tdispatch\twg\tcu\n"
            "COSIM_GPU_EVIDENCE_V1\tunit-vector\t0\t0\t"
            "session_start\t-1\t-1\t-1\t-1\n",
            encoding="ascii",
        )
        replace_key_value(
            self.fixture.artifact / "runner-metadata.txt",
            "gem5_evidence_start_seq",
            "0",
        )
        self.synchronize_gem5_evidence_hash("0")

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("gem5_client_not_connected", error_codes(result))
        self.assertIn("gem5_gpu_execution_unproven", error_codes(result))

    def test_gem5_timestamp_regression_is_rejected(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        gem5_log.write_text(
            gem5_log.read_text(encoding="utf-8").replace(
                "2026-01-01T00:00:01.200000000Z",
                "2026-01-01T00:00:01.050000000Z",
            ),
            encoding="utf-8",
        )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_timestamp", error_codes(result))

    def test_warning_timestamp_regression_is_rejected(self) -> None:
        gem5_log = self.fixture.artifact / "gem5.log"
        with gem5_log.open("a", encoding="utf-8") as handle:
            handle.write(
                "2026-01-01T00:00:01.350000000Z "
                "src/gpu-compute/gpu_command_processor.cc:799: "
                "warn: Ignoring vendor packet\n"
            )
        self.synchronize_gem5_log_hash()

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("invalid_gem5_timestamp", error_codes(result))

    def test_nested_duplicate_docker_json_key_is_rejected(self) -> None:
        inspect_path = self.fixture.artifact / "docker-inspect.json"
        text = inspect_path.read_text(encoding="utf-8")
        inspect_path.write_text(
            text.replace(
                '"State": {',
                '"State": {"Running": false,',
                1,
            ),
            encoding="utf-8",
        )

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("json_read_error", error_codes(result))

    def test_unknown_top_matrix_column_is_rejected(self) -> None:
        rows = [{**row, "forged": "value"} for row in self.fixture.matrix_rows]
        write_tsv(self.fixture.matrix, (*MATRIX_FIELDS, "forged"), rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("matrix_error", error_codes(result))

    def test_unknown_local_matrix_column_is_rejected(self) -> None:
        local_path = self.fixture.artifact / "matrix.tsv"
        with local_path.open("r", encoding="utf-8", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        rows[0]["forged"] = "value"
        write_tsv(local_path, (*LOCAL_MATRIX_FIELDS, "forged"), rows)

        result = self.verify()
        self.assertEqual("FAIL", result["outcome"])
        self.assertIn("local_matrix_error", error_codes(result))


if __name__ == "__main__":
    unittest.main()
