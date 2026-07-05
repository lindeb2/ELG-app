#!/usr/bin/env bash
# Waits for the running ELG process to exit, swaps install dir contents, relaunches.
set -euo pipefail

parent_pid="$1"
install_dir="$2"
new_dir="$3"
exe_path="$4"

while kill -0 "$parent_pid" 2>/dev/null; do
    sleep 0.2
done

if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete "${new_dir}/" "${install_dir}/"
else
  shopt -s dotglob nullglob
  rm -rf "${install_dir:?}"/*
  cp -a "${new_dir}/." "${install_dir}/"
fi

chmod +x "${exe_path}"
nohup "${exe_path}" >/dev/null 2>&1 &
disown || true
