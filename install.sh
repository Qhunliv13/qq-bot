#!/usr/bin/env bash
# Keep all setup and runtime behavior in the shared Python bootstrap.
if [ -z "${BASH_VERSION:-}" ]; then
    printf '%s\n' 'install.sh requires Bash; run: bash /path/to/install.sh [command ...]' >&2
    exit 2
fi
set -e

# Resolve the checkout without changing the caller's cwd or consuming stdin.
# Clearing CDPATH prevents shell directory-search output from corrupting the path.
repo_root="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'python3 is required (Python 3.10+ recommended); see docs/LINUX.md.' >&2
    exit 127
fi

# Only an omitted command defaults to up; all supplied arguments stay unchanged.
if [ "$#" -eq 0 ]; then
    set -- up
fi
exec python3 "$repo_root/tools/bootstrap.py" "$@"
