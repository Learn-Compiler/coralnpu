#!/usr/bin/env bash
# Build the single-core Coral ELF simulator from any working directory.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec "$repo_dir/utils/coral_sim/build.sh" "$@"
