#!/usr/bin/env python3
"""Fresh-install helper: stdlib only, external state, no production migration.

Run ``python tools/bootstrap.py wizard`` for visual first-run setup and startup.
Explicit ``up`` retains the existing terminal-based setup for advanced users.
``init`` prepares state without Docker. Docker Desktop (Windows, WSL2/Linux
containers) or Docker Engine (Linux), with Compose v2 supporting --wait and
--wait-timeout, is required. ``up`` waits for local runtime health, not model
connectivity or QQ login; no NapCat installation is managed. ``info`` shows local
access details without Docker (passwords hidden unless explicitly requested).
``stop`` only stops the owned project and retains state. First-init BOT_WEB_PORT
(default 6185) and BOT_ONEBOT_PORT (default 6199) are saved as distinct host ports;
later explicit environment overrides are temporary.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
# Direct script execution and source-package imports share the same setup helpers.
if __package__:
    from .setup_inputs import collect_model_inputs, validate_model_field, validate_owner_ids
    from .setup_ports import first_install_ports
else:
    try:
        from setup_inputs import collect_model_inputs, validate_model_field, validate_owner_ids
        from setup_ports import first_install_ports
    except ModuleNotFoundError:
        from tools.setup_inputs import collect_model_inputs, validate_model_field, validate_owner_ids
        from tools.setup_ports import first_install_ports

# Never infer state from a production checkout or legacy /root/higan directory.
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "qhunliv-bot"
MARKER = ".bootstrap.json"


def data_path(raw, *, compose_env=True):
    """Resolve external state; apply quote restrictions only for Compose serialization."""
    path = Path(raw).expanduser().resolve()
    if path == REPO_ROOT or REPO_ROOT in path.parents or path in REPO_ROOT.parents:
        raise ValueError("data directory must be outside (not an ancestor of) this repository")
    if any(ord(c) < 32 for c in str(path)) or (compose_env and "'" in str(path)):
        # Native argv accepts apostrophes; only the single-quoted Compose env file
        # requires this extra restriction. Controls stay invalid in both modes.
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
        validate_model_field(field, model.get(field))
    validate_owner_ids(settings.get("owner_qqs"))
    if not isinstance(settings.get("persona"), str):
        raise ValueError("persona must be text")
    if not isinstance(settings.get("dashboard_password"), str) or len(settings["dashboard_password"]) < 16:
        raise ValueError("dashboard password must have at least 16 characters")
    deployment = settings.get("deployment", {})
    if not isinstance(deployment, dict):
        raise ValueError("settings.deployment must be an object")
    ports = [deployment.get("web_port", 6185), deployment.get("onebot_port", 6199)]
    if any(type(port) is not int or not 1 <= port <= 65535 for port in ports) or ports[0] == ports[1]:
        raise ValueError("deployment ports must be distinct integers from 1 to 65535")
    return settings


def private_write(path, content):
    """Create private files exclusively; never replace user-owned configuration."""
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def install_info(data, name, *, show_password=False, as_json=False):
    """Show actionable local access details without Docker, model requests or secret dumps."""
    settings = load_install(data, name)
    environment = docker_environment(data, settings)
    if as_json:
        # The launcher may consume this branch; secrets are deliberately absent.
        print(json.dumps({"runtime_name": name, "data_dir": str(data),
                          "settings_file": str(data / "settings.json"),
                          "dashboard_url": "http://127.0.0.1:" + environment["BOT_WEB_PORT"],
                          "initial_dashboard_username": "astrbot", "readiness_checked": False}))
        return
    print("Runtime name: " + name)
    print("Data directory: " + str(data))
    print("Private settings file: " + str(data / "settings.json"))
    print("Dashboard URL (configured, not a readiness check): http://127.0.0.1:" + environment["BOT_WEB_PORT"])
    print("Dashboard username (initial setup): astrbot")
    if show_password:
        # Explicit local disclosure of only this one secret; never print model/API configuration.
        print("Dashboard password (initial setup; private, do not share): " + settings["dashboard_password"])
    else:
        print("Dashboard password: hidden. Use info --show-password (Windows: info -ShowPassword) to reveal it locally.")
    print("Username/password are initial setup values; dashboard changes may supersede them. This command does not reset credentials.")
    print("No runtime/model/QQ check performed. Use health or status for the selected installation.")


def load_install(data, name):
    """Reject unmanaged state and name drift before any Docker operation."""
    if not (data / MARKER).is_file():
        raise ValueError("No initialized installation at the selected data directory. Run up for first setup, or reuse the original --data-dir and --runtime-name.")
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
    # Interactive mistakes retry only that field; automation never receives a prompt.
    base, key, model, owners = collect_model_inputs(args)
    persona = Path(args.persona_file).read_text(encoding="utf-8") if args.persona_file else ""
    initialize_settings(data, name, {"base_url": base, "model": model, "api_key": key}, owners, persona)


def initialize_settings(data, name, model, owners, persona):
    """Use one exclusive state writer for CLI and the local visual first-run wizard.

    Validated existing installs are never reconfigured by this first-run path.
    Browser credentials stay in memory until this same private-file writer runs.
    """
    if (data / MARKER).exists():
        load_install(data, name)
        return
    if data.exists() and any(data.iterdir()):
        raise ValueError("refusing unmanaged nonempty data directory")
    ports = docker_environment(data, {})
    settings = validate_settings({
        "schema_version": 1,
        "model": model,
        "owner_qqs": list(dict.fromkeys(owners)),
        "persona": persona,
        "dashboard_password": secrets.token_urlsafe(24),
        "deployment": {"web_port": int(ports["BOT_WEB_PORT"]), "onebot_port": int(ports["BOT_ONEBOT_PORT"])},
    })
    # Reject malformed Unicode before creating any directories or partial secret files.
    # JSON can contain escaped lone surrogates even though the HTTP body is UTF-8.
    try:
        json.dumps(settings, ensure_ascii=False).encode("utf-8")
    except UnicodeError:
        raise ValueError("settings must contain valid UTF-8 text") from None
    # Check and hold ports before any state is written. Only fresh defaults may
    # move; existing settings and explicit operator choices are never rewritten.
    with first_install_ports(int(ports["BOT_WEB_PORT"]), int(ports["BOT_ONEBOT_PORT"]),
                             web_explicit="BOT_WEB_PORT" in os.environ,
                             onebot_explicit="BOT_ONEBOT_PORT" in os.environ) as selected:
        settings["deployment"] = selected
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt":
            data.chmod(0o700)
        for directory in ("astrbot", "higan", "config"):
            (data / directory).mkdir(mode=0o700)
        private_write(data / "settings.json", json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        private_write(data / "compose.env", "BOT_DATA_DIR='" + data.as_posix() + "'\n")
        # The marker is last: partial initialization is never mistaken for completion.
        private_write(data / MARKER, json.dumps({"schema_version": 1, "runtime_name": name}) + "\n")
    if selected != {"web_port": int(ports["BOT_WEB_PORT"]), "onebot_port": int(ports["BOT_ONEBOT_PORT"])}:
        print("A default port was busy; free local ports were selected and saved. No existing service was stopped.")
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


def docker_environment(data, settings=None):
    """Use saved deployment ports with explicit env overrides, always on the selected state."""
    environment = os.environ.copy()
    environment["BOT_DATA_DIR"] = data.as_posix()
    if settings is None:
        settings_path = data / "settings.json"
        settings = validate_settings(json.loads(settings_path.read_text(encoding="utf-8"))) if settings_path.is_file() else {}
    deployment = settings.get("deployment", {})
    # One source determines Compose, access information and subsequent commands.
    web_port = validated_port("BOT_WEB_PORT", deployment.get("web_port", 6185))
    onebot_port = validated_port("BOT_ONEBOT_PORT", deployment.get("onebot_port", 6199))
    if web_port == onebot_port:
        raise ValueError("BOT_WEB_PORT and BOT_ONEBOT_PORT must be distinct")
    environment["BOT_WEB_PORT"] = str(web_port)
    environment["BOT_ONEBOT_PORT"] = str(onebot_port)
    return environment


def run_docker(command, data, *, quiet=False):
    """Run native argv; visual probes discard untrusted tool output, never log it."""
    options = {"stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "timeout": 30} if quiet else {}
    return subprocess.run(command, check=True, cwd=REPO_ROOT, env=docker_environment(data), shell=False, **options)


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
    check_engine(data)
    run_docker(compose_command(data, name, "config", "--quiet"), data)
    print("Prerequisites OK. This is not proof of model connectivity or QQ login.")


def check_engine(data, *, quiet=False):
    """Probe Docker/Compose read-only through the same path for CLI and visual setup."""
    if not shutil.which("docker"):
        raise ValueError(
            "Docker CLI not found. Windows: install/start Docker Desktop with the WSL2 "
            "backend and Linux containers (https://docs.docker.com/desktop/setup/install/windows-install/). "
            "Linux: install/start Docker Engine and the Compose v2 plugin "
            "(https://docs.docker.com/engine/install/ and https://docs.docker.com/compose/install/linux/). "
            "Use Compose v2 supporting up --wait --wait-timeout, reopen your terminal, then rerun doctor."
        )
    options = {"quiet": True} if quiet else {}
    run_docker(["docker", "compose", "version"], data, **options)
    run_docker(["docker", "info", "--format", "{{.ServerVersion}}"], data, **options)


def wait_timeout(value):
    """Reject signs, whitespace, fractions and non-ASCII CLI timeout integers."""
    if not re.fullmatch(r"[0-9]{1,4}", value) or not 1 <= int(value) <= 3600:
        raise argparse.ArgumentTypeError("must be an ASCII integer from 1 to 3600 seconds")
    return int(value)


def parse_args(argv):
    """Expose setup and same-instance client commands with shared simple flags."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("wizard", "init", "doctor", "up", "info", "stop", "logs", "status", "health", "chat"))
    parser.add_argument("--no-browser", action="store_true", help="wizard only: let the Windows launcher open its local UI")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR))
    parser.add_argument("--runtime-name", "--name")
    parser.add_argument("--non-interactive", action="store_true")
    parser.add_argument("--show-password", action="store_true", help="info only: reveal the dashboard password locally, never the model API key")
    parser.add_argument("--json", action="store_true", help="info only: machine-readable access information without any password or API key")
    parser.add_argument("--model-base")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", metavar="VARIABLE", help="read API key from this environment variable; never pass a key on argv")
    parser.add_argument("--owner-qq", action="append")
    parser.add_argument("--persona-file")
    parser.add_argument("--prompt", help="chat request text; omit to read standard input")
    parser.add_argument("--wait-timeout", type=wait_timeout, default=300, metavar="SECONDS",
                        help="up: wait for local runtime health, 1-3600 seconds (default: 300); not QQ/model verification")
    args = parser.parse_args(argv)
    if args.no_browser and args.command != "wizard":
        parser.error("--no-browser is only supported by wizard")
    if args.json and (args.command != "info" or args.show_password):
        parser.error("--json is only supported by info and cannot be combined with --show-password")
    if args.show_password and args.command != "info":
        parser.error("--show-password is only supported by info")
    if args.prompt is not None and args.command != "chat":
        parser.error("--prompt is only supported by chat")
    return args


def main(argv=None):
    """Execute one isolated local action; report failures without secret dumps."""
    args = parse_args(argv)
    try:
        data = data_path(args.data_dir)
        name = runtime_name(args.runtime_name, data)
        if args.command == "wizard":
            if __package__:
                from .setup_wizard import serve
            else:
                from setup_wizard import serve
            serve(sys.modules[__name__], data, name, open_browser=not args.no_browser)
        elif args.command == "init":
            initialize(args, data, name)
        elif args.command == "info":
            install_info(data, name, show_password=args.show_password, as_json=args.json)
        elif args.command in ("doctor", "up"):
            if args.command == "up" and not (data / MARKER).exists():
                # One entry point reuses init safety checks, never a second writer.
                initialize(args, data, name)
            doctor(data, name)
            if args.command == "up":
                check_project_ownership(data, name)
                run_docker(compose_command(data, name, "up", "-d", "--build", "--wait",
                                           "--wait-timeout", str(args.wait_timeout)), data)
                print("Local runtime ready (local health check passed; model/QQ unverified).")
                install_info(data, name)
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
                action = {"logs": ["logs", "--tail", "100", "--follow"],
                          "status": ["ps"], "stop": ["stop"]}[args.command]
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
