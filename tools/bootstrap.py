#!/usr/bin/env python3
"""Fresh-install helper: stdlib only, external state, no production migration.

Run ``python tools/bootstrap.py up`` for a first-run model setup and startup.
``init`` prepares state without Docker. Docker Desktop (Windows, WSL2/Linux
containers) or Docker Engine (Linux), with Compose v2 supporting --wait and
--wait-timeout, is required. ``up`` waits for local runtime health, not model
connectivity or QQ login; no NapCat installation is managed. Set BOT_WEB_PORT
(default 6185) and BOT_ONEBOT_PORT (default 6199) to distinct host ports.
"""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

# Never infer state from a production checkout or legacy /root/higan directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "qhunliv-bot"
MARKER = ".bootstrap.json"


def data_path(raw):
    """Resolve state outside the checkout, rejecting compose-env injection."""
    path = Path(raw).expanduser().resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents or path in REPO_ROOT.parents:
        raise ValueError("data directory must be outside (not an ancestor of) this repository")
    if any(ord(c) < 32 for c in str(path)) or "'" in str(path):
        raise ValueError("data directory contains unsupported control or quote characters")
    return path


def runtime_name(raw, data):
    """Namespace every managed project; hash external state to isolate installs."""
    name = raw or "qhunliv-bot-" + hashlib.sha256(str(data).encode()).hexdigest()[:10]
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,62}", name):
        raise ValueError("runtime name must use lowercase letters, digits, _ or - (1-63 characters)")
    return name


def validate_settings(settings):
    """Validate fresh-install fields without disclosing credentials in errors."""
    if not isinstance(settings, dict) or settings.get("schema_version") != 1:
        raise ValueError("unsupported settings schema_version")
    model = settings.get("model")
    if not isinstance(model, dict):
        raise ValueError("settings.model must be an object")
    for field in ("base_url", "model", "api_key"):
        value = model.get(field)
        if not isinstance(value, str) or not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("model " + field + " must be nonempty and contain no control characters")
    try:
        url = urlsplit(model["base_url"])
        valid = url.scheme in ("https", "http") and bool(url.hostname) and not (
            url.username or url.password or url.query or url.fragment
        )
        url.port  # Reject malformed ports before a runtime is built.
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("model base URL must be http(s), without credentials, query or fragment")
    owners = settings.get("owner_qqs")
    if not isinstance(owners, list) or any(
        not isinstance(owner, str) or not re.fullmatch(r"[0-9]{5,20}", owner) for owner in owners
    ):
        raise ValueError("owner QQs must be 5-20 ASCII digits")
    if not isinstance(settings.get("persona"), str):
        raise ValueError("persona must be text")
    if not isinstance(settings.get("dashboard_password"), str) or len(settings["dashboard_password"]) < 16:
        raise ValueError("dashboard password must have at least 16 characters")
    return settings


def private_write(path, content):
    """Create private files exclusively; never replace user-owned configuration."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def load_install(data, name):
    """Reject unmanaged state and name drift before any Docker operation."""
    marker = json.loads((data / MARKER).read_text(encoding="utf-8"))
    if marker != {"schema_version": 1, "runtime_name": name}:
        raise ValueError("runtime marker mismatch; use the original --runtime-name and data directory")
    settings = validate_settings(json.loads((data / "settings.json").read_text(encoding="utf-8")))
    if (data / "compose.env").read_text(encoding="utf-8") != "BOT_DATA_DIR='" + data.as_posix() + "'\n":
        raise ValueError("compose.env does not match the selected data directory")
    return settings


def initialize(args, data, name):
    """Create a fresh runtime once; reruns validate but never rewrite secrets."""
    if (data / MARKER).exists():
        load_install(data, name)
        print("Already initialized; existing settings and secrets are unchanged.")
        return
    if data.exists() and any(data.iterdir()):
        raise ValueError("refusing unmanaged nonempty data directory; choose a NEW empty directory; no migration is performed")
    key = os.environ.get(args.api_key_env, "") if args.api_key_env else ""
    base = args.model_base or ""
    model = args.model or ""
    owners = args.owner_qq or []
    if not args.non_interactive:
        # EOF is handled by main, so piped input cannot silently create placeholders.
        base = base or input("OpenAI-compatible model base URL (include /v1 if required): ").strip()
        model = model or input("Model ID: ").strip()
        key = key or getpass.getpass("Model API key (hidden): ")
        if not owners:
            owners = input("Owner QQ IDs, comma-separated (empty disables QQ owner access): ").strip().split(",")
            owners = [owner.strip() for owner in owners if owner.strip()]
    persona = Path(args.persona_file).read_text(encoding="utf-8") if args.persona_file else ""
    settings = validate_settings({
        "schema_version": 1,
        "model": {"base_url": base, "model": model, "api_key": key},
        "owner_qqs": list(dict.fromkeys(owners)),
        "persona": persona,
        "dashboard_password": secrets.token_urlsafe(24),
    })
    # Validation precedes all writes. Exclusive creates also fail closed on races.
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        data.chmod(0o700)
    for directory in ("astrbot", "higan", "config"):
        (data / directory).mkdir(mode=0o700)
    private_write(data / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    private_write(data / "compose.env", "BOT_DATA_DIR='" + data.as_posix() + "'\n")
    # The marker is last: partial initialization is never mistaken for completion.
    private_write(data / MARKER, json.dumps({"schema_version": 1, "runtime_name": name}) + "\n")
    print("Initialized private external runtime: " + str(data))
    print("Dashboard credentials are in settings.json; never commit or share that file.")
    print("Next: doctor, then up. QQ login/NapCat is separate and is not configured automatically.")


def compose_command(data, name, *action):
    """Build explicit argv; never invoke a shell or guess a live project name."""
    return ["docker", "compose", "-p", name, "-f", str(REPO_ROOT / "deploy" / "compose.yaml"),
            "--env-file", str(data / "compose.env"), *action]


def validated_port(name, default):
    """Accept only ASCII decimal host ports; never echo untrusted environment text."""
    value = os.environ.get(name, str(default))
    if not re.fullmatch(r"[0-9]{1,5}", value) or not 1 <= int(value) <= 65535:
        raise ValueError(name + " must be an ASCII integer from 1 to 65535")
    return int(value)


def docker_environment(data):
    """Pin external state and validate distinct loopback ports before any Docker call."""
    environment = os.environ.copy()
    environment["BOT_DATA_DIR"] = data.as_posix()
    # Normalize ports once here so Compose and the reported dashboard URL agree.
    web_port = validated_port("BOT_WEB_PORT", 6185)
    onebot_port = validated_port("BOT_ONEBOT_PORT", 6199)
    if web_port == onebot_port:
        raise ValueError("BOT_WEB_PORT and BOT_ONEBOT_PORT must be distinct")
    environment["BOT_WEB_PORT"] = str(web_port)
    environment["BOT_ONEBOT_PORT"] = str(onebot_port)
    return environment


def run_docker(command, data):
    """Run bounded control commands, with inherited logs and no captured secrets."""
    return subprocess.run(command, check=True, cwd=REPO_ROOT, env=docker_environment(data), shell=False)


def check_project_ownership(data, name):
    """Refuse adopting containers whose project name belongs to another install."""
    result = subprocess.run([
        "docker", "ps", "-a", "--filter", "label=com.docker.compose.project=" + name,
        "--format", '{{.Label "io.qhunliv.bootstrap.data-dir"}}',
    ], check=True, cwd=REPO_ROOT, env=docker_environment(data), shell=False,
        capture_output=True, text=True)
    # A blank label is still a container and must not be ignored by strip().
    if any(label != data.as_posix() for label in result.stdout.splitlines()):
        raise ValueError("runtime name is already used by an unmanaged/different installation; choose another --runtime-name")


def doctor(data, name):
    """Check prerequisites read-only; never build, start, stop or send QQ messages."""
    load_install(data, name)
    for relative in ("deploy/compose.yaml", "deploy/Dockerfile", "vendor/astrbot/LICENSE"):
        if not (REPO_ROOT / relative).is_file():
            raise ValueError("missing bundled runtime file: " + relative)
    docker_environment(data)  # Fail on invalid ports before probing Docker.
    if not shutil.which("docker"):
        raise ValueError(
            "Docker CLI not found. Windows: install/start Docker Desktop with the WSL2 "
            "backend and Linux containers (https://docs.docker.com/desktop/setup/install/windows-install/). "
            "Linux: install/start Docker Engine and the Compose v2 plugin "
            "(https://docs.docker.com/engine/install/ and https://docs.docker.com/compose/install/linux/). "
            "Use Compose v2 supporting up --wait --wait-timeout, reopen your terminal, then rerun doctor."
        )
    run_docker(["docker", "compose", "version"], data)
    run_docker(["docker", "info", "--format", "{{.ServerVersion}}"], data)
    run_docker(compose_command(data, name, "config", "--quiet"), data)
    print("Prerequisites OK. This is not proof of model connectivity or QQ login.")


def wait_timeout(value):
    """Reject signs, whitespace, fractions and non-ASCII CLI timeout integers."""
    if not re.fullmatch(r"[0-9]{1,4}", value) or not 1 <= int(value) <= 3600:
        raise argparse.ArgumentTypeError("must be an ASCII integer from 1 to 3600 seconds")
    return int(value)


def parse_args(argv):
    """Expose setup and same-instance client commands with shared simple flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("init", "doctor", "up", "logs", "status", "health", "chat"))
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--runtime-name", "--name")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--model-base")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", metavar="VARIABLE", help="read API key from this environment variable; never pass a key on argv")
    parser.add_argument("--owner-qq", action="append")
    parser.add_argument("--persona-file")
    parser.add_argument("--prompt", help="chat request text; omit to read standard input")
    parser.add_argument("--wait-timeout", type=wait_timeout, default=300, metavar="SECONDS",
                        help="up: wait for local runtime health, 1-3600 seconds (default: 300); not QQ/model verification")
    args = parser.parse_args(argv)
    if args.prompt is not None and args.command != "chat":
        parser.error("--prompt is only supported by chat")
    return args


def main(argv=None):
    """Execute one isolated local action; report failures without secret dumps."""
    args = parse_args(argv)
    try:
        data = data_path(args.data_dir)
        name = runtime_name(args.runtime_name, data)
        if args.command == "init":
            initialize(args, data, name)
        elif args.command in ("doctor", "up"):
            if args.command == "up" and not (data / MARKER).exists():
                # One entry point reuses init safety checks, never a second writer.
                initialize(args, data, name)
            doctor(data, name)
            if args.command == "up":
                check_project_ownership(data, name)
                run_docker(compose_command(data, name, "up", "-d", "--build", "--wait",
                                           "--wait-timeout", str(args.wait_timeout)), data)
                print("Local runtime ready. Open http://127.0.0.1:"
                      + str(validated_port("BOT_WEB_PORT", 6185))
                      + "; read the private settings.json dashboard_password.")
                print("Model/QQ operation requires separate verification; no QQ login was performed.")
        else:
            load_install(data, name)
            if args.command in ("health", "chat"):
                # Reuse the existing root-local client against the same running bot.
                # Health is read-only; chat is one deliberate real model request.
                client = ["exec", "-T", "bot", "python", "/opt/qhunliv/local_runtime_client.py"]
                if args.command == "health":
                    client.append("--health")
                else:
                    prompt = args.prompt if args.prompt is not None else sys.stdin.read()
                    if not prompt.strip():
                        raise ValueError("chat prompt must not be empty")
                    client.extend(["--prompt", prompt])
                check_project_ownership(data, name)
                run_docker(compose_command(data, name, *client), data)
            else:
                # Read-only output can disclose another installation's private logs.
                check_project_ownership(data, name)
                action = ["logs", "--tail", "100", "--follow"] if args.command == "logs" else ["ps"]
                run_docker(compose_command(data, name, *action), data)
        return 0
    except (ValueError, OSError, EOFError, subprocess.CalledProcessError) as error:
        # Do not print command output or parsed documents: either may contain keys.
        detail = str(error) if isinstance(error, (ValueError, EOFError)) else type(error).__name__
        print("bootstrap: " + detail, file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
