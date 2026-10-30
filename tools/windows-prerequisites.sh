#!/usr/bin/env bash
# Windows wizard prerequisite stage only; bot setup stays in bootstrap.py.
# Called explicitly as WSL root; never sudo, change mirrors, delete or upgrade Docker.
set -euo pipefail
mode="${1:---check}"
if [[ "$mode" != --check && "$mode" != --install ]]; then
    echo 'Unsupported prerequisite operation.' >&2; exit 2
fi
if [[ "$(id -u)" != 0 ]]; then
    echo 'The setup wizard must run this prerequisite stage as WSL root.' >&2; exit 2
fi
python_ready() {
    # Keep the host minimum aligned with the shared bootstrap (container uses 3.12).
    command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3,10))'
}
docker_ready() {
    # A working Desktop integration is reused; no second daemon is installed.
    command -v docker >/dev/null && docker compose version >/dev/null 2>&1
}
if [[ "$mode" == --install ]]; then
    # Only the wizard's explicit consent enables package or service modifications.
    if ! python_ready || ! docker_ready; then
        if ! command -v apt-get >/dev/null; then
            echo 'Automatic prerequisites require an apt-based Linux distribution. See docs/WINDOWS.md.' >&2; exit 2
        fi
        packages=()
        if ! python_ready; then packages+=(python3); fi
        if ! docker_ready; then
            # Never replace an existing Docker/Desktop installation with another engine.
            if command -v docker >/dev/null; then
                echo 'Existing Docker lacks Compose v2. Enable Desktop integration or install its matching Compose plugin; no replacement was attempted.' >&2; exit 2
            fi
            packages+=(docker.io docker-compose-v2)
        fi
        apt-get update
        # Official configured signed distribution repositories only, no curl-to-shell.
        for package in "${packages[@]}"; do
            if ! apt-cache show "$package" >/dev/null 2>&1; then
                echo 'Required package is unavailable from configured distribution repositories. See docs/WINDOWS.md.' >&2; exit 2
            fi
        done
        DEBIAN_FRONTEND=noninteractive apt-get install --no-upgrade -y "${packages[@]}"
    fi
    if ! docker info >/dev/null 2>&1; then
        # Refuse remote/overridden endpoints rather than silently starting the wrong service.
        if [[ -n "${DOCKER_HOST:-}" || -n "${DOCKER_CONTEXT:-}" ]]; then
            echo 'Selected Docker endpoint is unavailable; no service was started.' >&2; exit 2
        fi
        if command -v systemctl >/dev/null && [[ -d /run/systemd/system ]]; then
            systemctl start docker
        elif command -v service >/dev/null; then
            service docker start
        else
            echo 'Docker is installed but no supported service manager is available.' >&2; exit 2
        fi
    fi
fi
if ! python_ready; then echo 'Python 3.10+ is missing.'; exit 10; fi
if ! docker_ready; then echo 'Docker and Compose v2 are missing or not integrated.'; exit 11; fi
if ! docker info >/dev/null 2>&1; then echo 'Docker daemon is unavailable.'; exit 12; fi
echo 'WSL prerequisites are ready; no bot or QQ was started by this check.'
