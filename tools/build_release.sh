#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 JI ENGINEERING <engineering@iwanus.eu>
#
# Builds the release archives from a clean checkout of HEAD.
#
#   toshiba_modbus.zip            what HACS downloads (hacs.json zip_release):
#                                 the component's own files at the archive root
#   toshiba_modbus-<ver>.zip      manual install: carries the custom_components/ path
#   toshiba_modbus-<ver>.tar.gz   same layout as the versioned zip
#   SHA256SUMS.txt                covers all three
#
# LICENSE and NOTICE go into every archive: Apache-2.0 section 4(a) wants the licence
# text travelling with a redistribution and 4(d) wants the NOTICE, and an archive is
# the only copy most users ever see.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
comp="$repo/custom_components/toshiba_modbus"
version="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["version"])' "$comp/manifest.json")"
out="${1:-$repo/dist}"

mkdir -p "$out"
find "$out" -maxdepth 1 -type f \( -name 'toshiba_modbus*' -o -name 'SHA256SUMS.txt' \) -delete
work="$(mktemp -d)"
cleanup() { chmod -R u+w "$work" 2>/dev/null || true; find "$work" -mindepth 1 -delete; rmdir "$work"; }
trap cleanup EXIT

# Only tracked files, so a stray __pycache__ or a scratch file cannot ship.
git -C "$repo" archive HEAD custom_components LICENSE NOTICE | tar -x -C "$work"
[ -d "$work/custom_components/toshiba_modbus" ] || { echo "component missing from HEAD" >&2; exit 1; }

# HACS layout: component files at the archive root, licence beside them.
flat="$work/flat"
mkdir -p "$flat"
cp -a "$work/custom_components/toshiba_modbus/." "$flat/"
cp -a "$work/LICENSE" "$work/NOTICE" "$flat/"
(cd "$flat" && zip -qr "$out/toshiba_modbus.zip" .)

# Manual-install layout: the path the user drops into /config.
(cd "$work" && zip -qr "$out/toshiba_modbus-$version.zip" custom_components LICENSE NOTICE)
(cd "$work" && tar -czf "$out/toshiba_modbus-$version.tar.gz" custom_components LICENSE NOTICE)

(cd "$out" && sha256sum toshiba_modbus.zip "toshiba_modbus-$version.zip" "toshiba_modbus-$version.tar.gz" > SHA256SUMS.txt)

echo "version $version"
ls -l "$out"
