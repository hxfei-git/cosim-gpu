#!/bin/bash
# Fast, offline contract checks for scripts/cosim_build.sh.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COSIM_DIR="$(dirname "$SCRIPT_DIR")"
BUILD_SCRIPT="${SCRIPT_DIR}/cosim_build.sh"
TOOLCHAIN_LOCK="${COSIM_DIR}/configs/cosim/toolchain.lock"
GEM5_BASELINE_LOCK="${COSIM_DIR}/configs/cosim/gem5-baseline.lock"
DOCKERFILE_RUN="${SCRIPT_DIR}/Dockerfile.run"

fail() {
    echo "[FAIL] $*" >&2
    exit 1
}

assert_contains() {
    local needle="$1"
    local file="$2"
    grep -F -- "$needle" "$file" >/dev/null || \
        fail "missing contract text '$needle' in $file"
}

bash -n "$BUILD_SCRIPT"

assert_contains 'QEMU_VERSION="10.1.5"' "$BUILD_SCRIPT"
assert_contains 'QEMU_SOURCE_SHA256 is empty' "$BUILD_SCRIPT"
assert_contains '"--disable-download"' "$BUILD_SCRIPT"
assert_contains '"--enable-tools"' "$BUILD_SCRIPT"
assert_contains 'VALIDSIG' "$BUILD_SCRIPT"
assert_contains 'SOURCE_FINGERPRINT_ALGORITHM=2' "$BUILD_SCRIPT"
assert_contains 'source_fingerprint_algorithm=' "$BUILD_SCRIPT"
assert_contains 'source_fingerprint=' "$BUILD_SCRIPT"
assert_contains 'docker_build_recipe_fingerprint=' "$BUILD_SCRIPT"
assert_contains 'initial_source_fingerprint=' "$BUILD_SCRIPT"
assert_contains 'source_pristine=' "$BUILD_SCRIPT"
assert_contains 'configure_fingerprint=' "$BUILD_SCRIPT"
assert_contains 'build_fingerprint=' "$BUILD_SCRIPT"
assert_contains 'binary_sha256=' "$BUILD_SCRIPT"
assert_contains 'GEM5_BASELINE_LOCK=' "$BUILD_SCRIPT"
assert_contains 'refresh_gem5_baseline_lock' "$BUILD_SCRIPT"
assert_contains 'qemu_img_sha256=' "$BUILD_SCRIPT"
assert_contains 'CONFIG_VFIO_USER=y' "$BUILD_SCRIPT"
assert_contains 'CONFIG_VFIO_PCI=y' "$BUILD_SCRIPT"
assert_contains "-name '*-config-devices.mak'" "$BUILD_SCRIPT"
assert_contains 'vfio-user-pci' "$BUILD_SCRIPT"
assert_contains 'virtio-net-pci' "$BUILD_SCRIPT"
assert_contains 'virtio-blk-pci' "$BUILD_SCRIPT"
assert_contains 'virtio-9p-pci' "$BUILD_SCRIPT"

for lock_field in schema gem5_commit source_fingerprint_algorithm \
                  source_fingerprint binary_sha256 docker_image; do
    grep -Eq "^${lock_field}=.+$" "$GEM5_BASELINE_LOCK" || \
        fail "gem5 baseline lock is missing ${lock_field}"
done
[[ "$(awk 'NF {count++} END {print count + 0}' "$GEM5_BASELINE_LOCK")" -eq 6 ]] || \
    fail "gem5 baseline lock contains unexpected fields"
# shellcheck disable=SC2016
[[ "$(grep -Fc 'refresh_gem5_baseline_lock "$commit" "$fingerprint"' \
    "$BUILD_SCRIPT")" -eq 2 ]] || \
    fail "gem5 build does not refresh the baseline lock on both success paths"
if grep -Eq '(^|[[:space:]])git[[:space:]]+commit([[:space:]]|$)' "$BUILD_SCRIPT"; then
    fail "build wrapper must never commit the refreshed baseline lock"
fi

assert_contains 'QEMU_VERSION=10.1.5' "$TOOLCHAIN_LOCK"
assert_contains 'QEMU_SOURCE_URL=https://download.qemu.org/qemu-10.1.5.tar.xz' "$TOOLCHAIN_LOCK"
assert_contains 'QEMU_SIGNATURE_URL=https://download.qemu.org/qemu-10.1.5.tar.xz.sig' "$TOOLCHAIN_LOCK"
assert_contains 'QEMU_RELEASE_KEY_FINGERPRINT=CEACC9E15534EBABB82D3FA03353C9CEF108B584' "$TOOLCHAIN_LOCK"
assert_contains 'QEMU_SOURCE_SHA256=1f1209b4db82e6c4417eaf6e7e0b073563572a042d9fb7492b084ba65a9c0693' "$TOOLCHAIN_LOCK"
grep -Eq '^FROM ghcr\.io/gem5/gpu-fs@sha256:[0-9a-f]{64}$' "$DOCKERFILE_RUN" || \
    fail "Dockerfile.run must pin the gem5 GPU base image by digest"
if grep -Eq '^FROM .*:latest([[:space:]]|$)' "$DOCKERFILE_RUN"; then
    fail "Dockerfile.run must not use a mutable latest tag"
fi

TEST_TMP="$(mktemp -d)"
trap 'rm -rf -- "$TEST_TMP"' EXIT
COSIM_LOCAL_ROOT="${TEST_TMP}/status-local" bash "$BUILD_SCRIPT" status \
    >"${TEST_TMP}/status-output" 2>&1
grep -F 'QEMU status: not ready' "${TEST_TMP}/status-output" >/dev/null || \
    fail "status did not report the missing binary"
[[ ! -e "${TEST_TMP}/status-local/downloads" ]] || \
    fail "status attempted a source download"

# Source the build wrapper without executing main, then exercise the two
# fingerprints that control incremental skip/reconfigure decisions.
# Functions and globals from the build wrapper are the contract under test.
# shellcheck disable=SC1090,SC1091
source "$BUILD_SCRIPT"
EMPTY_LOCK="${TEST_TMP}/empty-toolchain.lock"
sed 's/^QEMU_SOURCE_SHA256=.*/QEMU_SOURCE_SHA256=/' "$TOOLCHAIN_LOCK" > "$EMPTY_LOCK"
ORIGINAL_LOCK="$TOOLCHAIN_LOCK"
TOOLCHAIN_LOCK="$EMPTY_LOCK"
if (validate_qemu_lock) >"${TEST_TMP}/stdout" 2>"${TEST_TMP}/stderr"; then
    fail "lock validation unexpectedly accepted an empty archive SHA"
fi
grep -F 'QEMU_SOURCE_SHA256 is empty' "${TEST_TMP}/stderr" >/dev/null || \
    fail "empty archive SHA failure was not explicit"
TOOLCHAIN_LOCK="$ORIGINAL_LOCK"

QEMU_BUILD_DIR="${TEST_TMP}/qemu-build"
QEMU_META="${QEMU_BUILD_DIR}/.cosim-build-meta"
FORCE=0
mkdir -p "$QEMU_BUILD_DIR"

qemu_needs_configure fingerprint-a || \
    fail "a missing build.ninja did not require configure"
touch "${QEMU_BUILD_DIR}/build.ninja"
write_metadata "$QEMU_META" 'build_fingerprint=fingerprint-a'
if qemu_needs_configure fingerprint-a; then
    fail "matching configure provenance unexpectedly required configure"
fi
qemu_needs_configure fingerprint-b || \
    fail "a changed build fingerprint did not require configure"
# shellcheck disable=SC2034
FORCE=1
qemu_needs_configure fingerprint-a || \
    fail "--force semantics did not require configure"

SOURCE_TREE="${TEST_TMP}/source-tree"
mkdir -p "$SOURCE_TREE"
printf 'before\n' > "${SOURCE_TREE}/tracked.txt"
SOURCE_FINGERPRINT_BEFORE="$(directory_fingerprint "$SOURCE_TREE")"
printf 'after\n' > "${SOURCE_TREE}/tracked.txt"
SOURCE_FINGERPRINT_AFTER="$(directory_fingerprint "$SOURCE_TREE")"
[[ "$SOURCE_FINGERPRINT_BEFORE" != "$SOURCE_FINGERPRINT_AFTER" ]] || \
    fail "source-tree drift did not alter the source fingerprint"
SOURCE_FINGERPRINT_BEFORE="$(directory_fingerprint "$SOURCE_TREE")"
chmod u+x "${SOURCE_TREE}/tracked.txt"
SOURCE_FINGERPRINT_AFTER="$(directory_fingerprint "$SOURCE_TREE")"
[[ "$SOURCE_FINGERPRINT_BEFORE" != "$SOURCE_FINGERPRINT_AFTER" ]] || \
    fail "source-tree mode drift did not alter the source fingerprint"

# Exercise gem5's Git-aware source fingerprint with real parent/child
# repositories. This covers initialized and uninitialized gitlinks without
# relying on path quoting or line-oriented Git output.
FINGERPRINT_CHILD="${TEST_TMP}/fingerprint-child"
FINGERPRINT_PARENT="${TEST_TMP}/fingerprint-parent"
git init -q "$FINGERPRINT_CHILD"
git -C "$FINGERPRINT_CHILD" config user.name 'Contract Test'
git -C "$FINGERPRINT_CHILD" config user.email 'contract@example.invalid'
printf 'child baseline\n' > "${FINGERPRINT_CHILD}/child.txt"
printf 'ignored-output/\n' > "${FINGERPRINT_CHILD}/.gitignore"
git -C "$FINGERPRINT_CHILD" add .gitignore child.txt
git -C "$FINGERPRINT_CHILD" commit -q -m 'child baseline'

git init -q "$FINGERPRINT_PARENT"
git -C "$FINGERPRINT_PARENT" config user.name 'Contract Test'
git -C "$FINGERPRINT_PARENT" config user.email 'contract@example.invalid'
printf 'parent baseline\n' > "${FINGERPRINT_PARENT}/tracked.txt"
printf 'ignored-build/\n' > "${FINGERPRINT_PARENT}/.gitignore"
ODD_PATH=$'odd\tline\nname.txt'
printf 'odd baseline\n' > "${FINGERPRINT_PARENT}/${ODD_PATH}"
ln -s tracked.txt "${FINGERPRINT_PARENT}/source-link"
git -C "$FINGERPRINT_PARENT" add .gitignore tracked.txt "$ODD_PATH" source-link
git -C "$FINGERPRINT_PARENT" commit -q -m 'parent baseline'
git -c protocol.file.allow=always -C "$FINGERPRINT_PARENT" \
    submodule add -q "$FINGERPRINT_CHILD" nested
git -C "$FINGERPRINT_PARENT" commit -q -am 'add nested repository'

FINGERPRINT_BASE="$(source_fingerprint "$FINGERPRINT_PARENT")"
[[ "$FINGERPRINT_BASE" == "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "a clean source fingerprint was not stable"

mkdir -p "${FINGERPRINT_PARENT}/ignored-build"
printf 'generated\n' > "${FINGERPRINT_PARENT}/ignored-build/output.bin"
[[ "$FINGERPRINT_BASE" == "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "ignored build output altered the source fingerprint"

printf 'odd changed\n' > "${FINGERPRINT_PARENT}/${ODD_PATH}"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "a tracked filename containing tab/newline was not fingerprinted"
git -C "$FINGERPRINT_PARENT" restore -- "$ODD_PATH"

ln -sfn "$ODD_PATH" "${FINGERPRINT_PARENT}/source-link"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "a changed symlink target did not alter the source fingerprint"
git -C "$FINGERPRINT_PARENT" restore -- source-link

rm -f -- "${FINGERPRINT_PARENT}/tracked.txt"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "a deleted tracked file did not alter the source fingerprint"
git -C "$FINGERPRINT_PARENT" restore -- tracked.txt

chmod u+x "${FINGERPRINT_PARENT}/tracked.txt"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "an unstaged executable-mode change did not alter the source fingerprint"
git -C "$FINGERPRINT_PARENT" restore -- tracked.txt

printf 'staged change\n' > "${FINGERPRINT_PARENT}/tracked.txt"
git -C "$FINGERPRINT_PARENT" add tracked.txt
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "a staged change did not alter the source fingerprint"
git -C "$FINGERPRINT_PARENT" reset -q HEAD -- tracked.txt
git -C "$FINGERPRINT_PARENT" restore -- tracked.txt

FINGERPRINT_NESTED="${FINGERPRINT_PARENT}/nested"
NESTED_BASE="$(git -C "$FINGERPRINT_NESTED" rev-parse HEAD)"
git -C "$FINGERPRINT_NESTED" config user.name 'Contract Test'
git -C "$FINGERPRINT_NESTED" config user.email 'contract@example.invalid'
printf 'nested commit\n' > "${FINGERPRINT_NESTED}/child.txt"
git -C "$FINGERPRINT_NESTED" add child.txt
git -C "$FINGERPRINT_NESTED" commit -q -m 'nested head change'
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "an initialized gitlink HEAD change was not fingerprinted"
git -C "$FINGERPRINT_NESTED" checkout -q "$NESTED_BASE"

printf 'nested tracked change\n' > "${FINGERPRINT_NESTED}/child.txt"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "an initialized gitlink tracked change was not fingerprinted"
git -C "$FINGERPRINT_NESTED" restore -- child.txt

printf 'nested untracked\n' > "${FINGERPRINT_NESTED}/untracked.txt"
[[ "$FINGERPRINT_BASE" != "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "an initialized gitlink untracked file was not fingerprinted"
rm -f -- "${FINGERPRINT_NESTED}/untracked.txt"

mkdir -p "${FINGERPRINT_NESTED}/ignored-output"
printf 'generated\n' > "${FINGERPRINT_NESTED}/ignored-output/result.bin"
[[ "$FINGERPRINT_BASE" == "$(source_fingerprint "$FINGERPRINT_PARENT")" ]] || \
    fail "ignored output in an initialized gitlink altered the source fingerprint"

UNINITIALIZED_PARENT="${TEST_TMP}/fingerprint-parent-uninitialized"
git clone -q "$FINGERPRINT_PARENT" "$UNINITIALIZED_PARENT"
UNINITIALIZED_FINGERPRINT="$(source_fingerprint "$UNINITIALIZED_PARENT")"
git -c protocol.file.allow=always -C "$UNINITIALIZED_PARENT" \
    submodule update -q --init --recursive
INITIALIZED_FINGERPRINT="$(source_fingerprint "$UNINITIALIZED_PARENT")"
[[ "$UNINITIALIZED_FINGERPRINT" != "$INITIALIZED_FINGERPRINT" ]] || \
    fail "initialized and uninitialized gitlinks had the same fingerprint"
[[ "$INITIALIZED_FINGERPRINT" == "$(source_fingerprint "$UNINITIALIZED_PARENT")" ]] || \
    fail "an initialized recursive fingerprint was not stable"

# Old fingerprint algorithms and changed Docker build recipes must invalidate
# gem5's incremental-build metadata.
GEM5_BIN="${TEST_TMP}/gem5.opt"
GEM5_META="${TEST_TMP}/gem5-meta"
printf '#!/bin/sh\n' > "$GEM5_BIN"
chmod +x "$GEM5_BIN"
GEM5_COMMIT='1111111111111111111111111111111111111111'
GEM5_SOURCE_FINGERPRINT='2222222222222222222222222222222222222222222222222222222222222222'
GEM5_RECIPE_FINGERPRINT='gem5-recipe-fingerprint'
GEM5_BINARY_SHA="$(sha256sum "$GEM5_BIN" | awk '{print $1}')"
GEM5_IMAGE_ID='sha256:3333333333333333333333333333333333333333333333333333333333333333'
docker() {
    [[ "$*" == "image inspect -f {{.Id}} ${GEM5_IMAGE}" || \
       "$*" == "image inspect -f {{.Id}} ${GEM5_RUN_IMAGE}" ]] || return 1
    printf '%s\n' "$GEM5_IMAGE_ID"
}
write_metadata "$GEM5_META" \
    "commit=${GEM5_COMMIT}" \
    "source_fingerprint_algorithm=${SOURCE_FINGERPRINT_ALGORITHM}" \
    "source_fingerprint=${GEM5_SOURCE_FINGERPRINT}" \
    "docker_build_recipe_fingerprint=${GEM5_RECIPE_FINGERPRINT}" \
    "binary_sha256=${GEM5_BINARY_SHA}" \
    "docker_image=${GEM5_IMAGE_ID}"
gem5_metadata_matches \
    "$GEM5_COMMIT" "$GEM5_SOURCE_FINGERPRINT" "$GEM5_RECIPE_FINGERPRINT" || \
    fail "matching gem5 provenance metadata was rejected"
if gem5_metadata_matches \
    "$GEM5_COMMIT" "$GEM5_SOURCE_FINGERPRINT" 'changed-recipe'; then
    fail "a changed Docker build recipe was accepted as an incremental-build hit"
fi
printf '# tampered\n' >> "$GEM5_BIN"
if gem5_metadata_matches \
    "$GEM5_COMMIT" "$GEM5_SOURCE_FINGERPRINT" "$GEM5_RECIPE_FINGERPRINT"; then
    fail "a tampered gem5 binary was accepted as an incremental-build hit"
fi
printf '#!/bin/sh\n' > "$GEM5_BIN"
chmod +x "$GEM5_BIN"
GEM5_BINARY_SHA="$(sha256sum "$GEM5_BIN" | awk '{print $1}')"
write_metadata "$GEM5_META" \
    "commit=${GEM5_COMMIT}" \
    'source_fingerprint_algorithm=1' \
    "source_fingerprint=${GEM5_SOURCE_FINGERPRINT}" \
    "docker_build_recipe_fingerprint=${GEM5_RECIPE_FINGERPRINT}" \
    "binary_sha256=${GEM5_BINARY_SHA}" \
    "docker_image=${GEM5_IMAGE_ID}"
if gem5_metadata_matches \
    "$GEM5_COMMIT" "$GEM5_SOURCE_FINGERPRINT" "$GEM5_RECIPE_FINGERPRINT"; then
    fail "legacy source-fingerprint metadata was accepted as an incremental-build hit"
fi

# 刷新 baseline lock 时必须原子替换完整的六字段 trust anchor。
GEM5_BASELINE_LOCK="${TEST_TMP}/gem5-baseline.lock"
printf 'stale=true\n' > "$GEM5_BASELINE_LOCK"
refresh_gem5_baseline_lock "$GEM5_COMMIT" "$GEM5_SOURCE_FINGERPRINT"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" schema)" == "1" ]] || \
    fail "refreshed gem5 baseline lock has the wrong schema"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" gem5_commit)" == "$GEM5_COMMIT" ]] || \
    fail "refreshed gem5 baseline lock has the wrong commit"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" source_fingerprint_algorithm)" == \
    "$SOURCE_FINGERPRINT_ALGORITHM" ]] || \
    fail "refreshed gem5 baseline lock has the wrong fingerprint algorithm"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" source_fingerprint)" == \
    "$GEM5_SOURCE_FINGERPRINT" ]] || \
    fail "refreshed gem5 baseline lock has the wrong source fingerprint"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" binary_sha256)" == \
    "$(sha256sum "$GEM5_BIN" | awk '{print $1}')" ]] || \
    fail "refreshed gem5 baseline lock has the wrong binary hash"
[[ "$(metadata_value "$GEM5_BASELINE_LOCK" docker_image)" == "$GEM5_IMAGE_ID" ]] || \
    fail "refreshed gem5 baseline lock has the wrong Docker image"
[[ "$(awk 'NF {count++} END {print count + 0}' "$GEM5_BASELINE_LOCK")" -eq 6 ]] || \
    fail "refreshed gem5 baseline lock is not a complete six-field anchor"
if find "$(dirname "$GEM5_BASELINE_LOCK")" -maxdepth 1 -name \
    'gem5-baseline.lock.*' -print -quit | grep -q .; then
    fail "atomic gem5 baseline lock refresh left a temporary file"
fi

FAKE_PREFIX="${TEST_TMP}/fake-prefix"
mkdir -p "${FAKE_PREFIX}/bin"
QEMU_BIN="${FAKE_PREFIX}/bin/qemu-system-x86_64"
QEMU_IMG="${FAKE_PREFIX}/bin/qemu-img"
cat > "$QEMU_BIN" <<'EOF'
#!/bin/bash
case "${1:-}:${2:-}" in
    --version:) echo 'QEMU emulator version 10.1.5' ;;
    -device:help)
        printf '%s\n' \
            'name "vfio-user-pci"' \
            'name "virtio-net-pci"' \
            'name "virtio-blk-pci"' \
            'name "virtio-9p-pci"'
        ;;
    -machine:help) echo 'q35 Standard PC (Q35 + ICH9, 2009)' ;;
    -accel:help) echo 'kvm' ;;
    -netdev:help) echo 'user' ;;
    *) exit 2 ;;
esac
EOF
cat > "$QEMU_IMG" <<'EOF'
#!/bin/bash
case "${1:-}" in
    --version) echo 'qemu-img version 10.1.5' ;;
    create)
        image="${5:?missing image path}"
        : > "$image"
        ;;
    info)
        case "${3:?missing image path}" in
            *probe-raw.img) echo '{"format":"raw"}' ;;
            *probe-qcow2.img) echo '{"format":"qcow2"}' ;;
            *) exit 2 ;;
        esac
        ;;
    *) exit 2 ;;
esac
EOF
chmod +x "$QEMU_BIN" "$QEMU_IMG"
printf '%s\n' 'CONFIG_VFIO_PCI=y' 'CONFIG_VFIO_USER=y' \
    > "${QEMU_BUILD_DIR}/x86_64-softmmu-config-devices.mak"

ARCHIVE_SHA="$(printf 'archive' | sha256sum | awk '{print $1}')"
SOURCE_SHA="$(printf 'source' | sha256sum | awk '{print $1}')"
CONFIGURE_SHA="$(printf 'configure' | sha256sum | awk '{print $1}')"
BUILD_SHA="$(printf 'build' | sha256sum | awk '{print $1}')"
write_metadata "$QEMU_META" \
    'version=10.1.5' \
    "source_sha256=${ARCHIVE_SHA}" \
    "source_fingerprint=${SOURCE_SHA}" \
    "configure_fingerprint=${CONFIGURE_SHA}" \
    "build_fingerprint=${BUILD_SHA}" \
    'signing_verified=true' \
    "binary_sha256=$(sha256sum "$QEMU_BIN" | awk '{print $1}')" \
    "qemu_img_sha256=$(sha256sum "$QEMU_IMG" | awk '{print $1}')"
verify_qemu_metadata "$ARCHIVE_SHA" "$SOURCE_SHA" "$CONFIGURE_SHA" "$BUILD_SHA" || \
    fail "matching QEMU features and metadata were rejected"
printf '%s\n' '# binary tamper' >> "$QEMU_BIN"
if verify_qemu_metadata "$ARCHIVE_SHA" "$SOURCE_SHA" "$CONFIGURE_SHA" "$BUILD_SHA"; then
    fail "a changed QEMU binary hash was accepted as an incremental-build hit"
fi

echo "[PASS] build wrapper contract"
