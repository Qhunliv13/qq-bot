"""Explicit standalone CLI, independent of QQ login and application config."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import logging
import math
import os
from pathlib import Path
import sys

from .harness import Harness
from .http_host import create_http_harness


def _positive_timeout(value: str) -> float:
    """Reject non-finite or non-positive caller timeouts before any network IO."""
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive and finite")
    return number


def _positive_rounds(value: str) -> int:
    """Allow only explicit positive round limits; omitted means no fixed limit."""
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("max rounds must be positive")
    return number


class _SafeParser(argparse.ArgumentParser):
    """Keep invalid arguments from echoing accidental secrets to stderr."""

    def error(self, message):
        """Route parsing errors through the same fixed CLI error boundary."""
        raise ValueError("invalid command arguments")


def _parser() -> argparse.ArgumentParser:
    """Define explicit inputs without reading any application or QQ secrets."""
    parser = _SafeParser(description=__doc__)
    parser.add_argument("--host", help="trusted MODULE:FACTORY returning a Harness")
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    system = parser.add_mutually_exclusive_group()
    system.add_argument("--system")
    system.add_argument("--system-file")
    parser.add_argument("--prompt", help="omit to read all stdin; empty text is valid")
    parser.add_argument("--api-key-env", default=None, help="environment variable name, not a key")
    parser.add_argument("--max-rounds", type=_positive_rounds, default=None)
    parser.add_argument("--timeout", type=_positive_timeout, default=None)
    return parser


def _harness(args: argparse.Namespace) -> Harness:
    """Build the operator-selected host, never guessing model or identity."""
    if args.host:
        # Explicit remote options cannot silently be ignored by an injected host.
        if any(getattr(args, key) is not None for key in (
                "base_url", "model", "system", "system_file", "api_key_env", "timeout", "max_rounds")):
            raise ValueError("host cannot be combined with remote configuration")
        module, separator, factory = args.host.partition(":")
        if not separator or not module or not factory:
            raise ValueError("invalid host factory")
        instance = getattr(importlib.import_module(module), factory)()
        if not isinstance(instance, Harness):
            raise TypeError("host factory must return Harness")
        return instance
    if args.system is None and args.system_file is None:
        raise ValueError("system or system-file is required")
    # Only the operator's explicitly named UTF-8 persona file is read.
    system = (Path(args.system_file).read_text(encoding="utf-8")
              if args.system_file is not None else args.system)
    base_url = args.base_url if args.base_url is not None else os.environ.get("BOT_MODEL_BASE_URL")
    model = args.model if args.model is not None else os.environ.get("BOT_MODEL")
    if not base_url or not model:
        raise ValueError("base URL and model are required")
    # The argument is an environment variable NAME, never a plaintext key option.
    key_env = args.api_key_env if args.api_key_env is not None else "BOT_MODEL_API_KEY"
    if not key_env:
        raise ValueError("key environment name is required")
    return create_http_harness(
        system=system, base_url=base_url, model=model,
        api_key=os.environ.get(key_env, ""),
        timeout=args.timeout if args.timeout is not None else 60,
        max_rounds=args.max_rounds,
    )


def main(argv: list[str] | None = None) -> int:
    """Emit a local-only result or a fixed error without provider diagnostics."""
    # The CLI protocol is UTF-8 on Windows as well as POSIX. Test/embedded streams
    # need not implement reconfigure, so only real text streams are adjusted.
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if callable(configure):
            configure(encoding="utf-8")
    try:
        args = _parser().parse_args(argv)
        harness = _harness(args)
        prompt = args.prompt if args.prompt is not None else sys.stdin.read()
        # The reusable loop logs causal exceptions, which a trusted injected
        # provider may populate with secrets. Suppress only its CLI-time logger.
        loop_logger = logging.getLogger("plugin.agent_loop")
        was_disabled = loop_logger.disabled
        loop_logger.disabled = True
        try:
            result = asyncio.run(harness.run(prompt))
        finally:
            loop_logger.disabled = was_disabled
        print(json.dumps({"text": result.text, "used_tools": list(result.used_tools),
                          "published": False, "destination": "local"}, ensure_ascii=False))
        return 0
    except Exception:
        # Includes ModelCompletionUnavailable: never expose its causal exception.
        print("bot_core: configuration or model completion failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
