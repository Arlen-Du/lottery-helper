#!/usr/bin/env bash
set -euo pipefail

# Build a native x86_64 fnOS .fpk package for lottery-helper.
# Must be run on a Linux x86_64 host (or inside a Linux x86_64 container / GitHub Actions runner).

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PKG_DIR="${ROOT_DIR}/packaging/fnos/lottery-helper"
OUT_DIR="${ROOT_DIR}/dist"
FNPACK_VERSION="${FNPACK_VERSION:-1.2.3}"
FNPACK_BIN="${FNPACK_BIN:-${ROOT_DIR}/build/fnpack}"

ARCH="$(uname -m)"
OS="$(uname -s)"
if [[ "${OS}" != "Linux" || "${ARCH}" != "x86_64" ]]; then
    echo "ERROR: Native x86_64 binary must be built on Linux x86_64 (current: ${OS} ${ARCH})."
    echo "Tip: Run this script on your x86_64 fnOS/Linux machine, in a linux/amd64 container, or via GitHub Actions."
    exit 1
fi

cd "${ROOT_DIR}"

echo "==> 1. Building standalone Linux x86_64 server binary with PyInstaller..."
python3 -m PyInstaller --noconfirm --clean LotteryHelperWeb.spec

echo "==> 2. Staging binary into fnOS package layout..."
mkdir -p "${PKG_DIR}/app/server" "${OUT_DIR}"
cp -f "${ROOT_DIR}/dist/lottery-helper-server" "${PKG_DIR}/app/server/lottery-helper-server"
chmod +x "${PKG_DIR}/app/server/lottery-helper-server"
chmod +x "${PKG_DIR}/cmd/"*

if ! command -v fnpack >/dev/null 2>&1 && [[ ! -x "${FNPACK_BIN}" ]]; then
    echo "==> 3. Downloading official fnpack (${FNPACK_VERSION}) for linux-amd64..."
    mkdir -p "$(dirname "${FNPACK_BIN}")"
    curl -fsSL "https://static2.fnnas.com/fnpack/fnpack-${FNPACK_VERSION}-linux-amd64" -o "${FNPACK_BIN}"
    chmod +x "${FNPACK_BIN}"
fi

FNPACK_CMD="$(command -v fnpack || echo "${FNPACK_BIN}")"

echo "==> 4. Building .fpk package with ${FNPACK_CMD}..."
"${FNPACK_CMD}" build --directory "${PKG_DIR}"

BUILT_FPK="$(ls -t "${PKG_DIR}".fpk "${ROOT_DIR}"/lottery-helper*.fpk "${PKG_DIR}"/../lottery-helper*.fpk 2>/dev/null | head -n 1 || true)"
if [[ -z "${BUILT_FPK}" ]]; then
    echo "ERROR: fnpack did not produce a .fpk file!"
    exit 1
fi

VERSION="$(grep '^version=' "${PKG_DIR}/manifest" | cut -d= -f2)"
FINAL_FPK="${OUT_DIR}/lottery-helper-${VERSION}-x86_64.fpk"
mv -f "${BUILT_FPK}" "${FINAL_FPK}"
echo "==> Done! Native fnOS package created at: ${FINAL_FPK}"
ls -lh "${FINAL_FPK}"
