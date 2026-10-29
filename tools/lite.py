"""Portable, text-only OpenAI-compatible chat using the reusable bot_core HTTP host."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path
import re
import sys

# Direct script invocation starts with tools/ on sys.path; resolve this checkout only.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bot_core.http_host import ChatCompletionHost  # noqa: E402

# A key argument names an environment variable; credential values never enter argv.
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z", re.ASCII)
_ERROR = "lite: configuration or model completion failed"


class _SafeParser(argparse.ArgumentParser):
    """Suppress argument echoes, which could contain accidental credentials."""

    def error(self, message: str) -> None:
        """Avoid printing input or upstream details on invalid arguments."""
        raise ValueError("invalid command arguments")


def _positive_turns(value: str) -> int:
    """Require a strictly positive bound on remembered successful exchanges."""
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError("invalid history limit") from None
    if number < 1:
        raise argparse.ArgumentTypeError("invalid history limit")
    return number


def _parser() -> argparse.ArgumentParser:
    """Define only portable, opt-in text chat inputs; never accept a literal key."""
    parser = _SafeParser(description=__doc__)
    parser.add_argument("--base-url", help="OpenAI-compatible /v1 base URL; defaults to BOT_MODEL_BASE_URL")
    parser.add_argument("--model", help="model ID; defaults to BOT_MODEL")
    parser.add_argument("--api-key-env", default="BOT_MODEL_API_KEY",
                        help="credential environment variable NAME (not a key)")
    persona = parser.add_mutually_exclusive_group(required=True)
    persona.add_argument("--system", help="explicit system message")
    persona.add_argument("--system-file", help="explicit UTF-8 system message file")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--prompt", help="one-shot message; otherwise read stdin")
    mode.add_argument("--interactive", action="store_true", help="in-memory multi-turn chat")
    parser.add_argument("--history-turns", type=_positive_turns, default=12,
                        help="successful turns retained in interactive mode (default: 12)")
    return parser


def _configure_utf8() -> None:
    """Support Unicode prompts and responses on Windows and POSIX terminals."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        configure = getattr(stream, "reconfigure", None)
        if callable(configure):
            configure(encoding="utf-8")


def _reply(host: ChatCompletionHost, system: str, prompt: str,
           history: list[tuple[str, str]], limit: int) -> str:
    """Send bounded local context; refuse tool requests instead of executing them."""
    messages = [{"role": "system", "content": system}]
    for user, answer in history[-limit:]:
        messages.extend(({"role": "user", "content": user},
                         {"role": "assistant", "content": answer}))
    messages.append({"role": "user", "content": prompt})
    # ChatCompletionHost owns the sole provider implementation, validates its URL,
    # drops separate reasoning_content, and does not advertise any tool schemas.
    text, calls = asyncio.run(host(messages, allow_tools=False))
    if calls or not isinstance(text, str) or not text.strip():
        raise ValueError("no public text completion")
    return text


def _interactive(host: ChatCompletionHost, system: str, limit: int) -> None:
    """Keep one session's bounded conversation in memory, without file writes."""
    history: list[tuple[str, str]] = []
    while True:
        # Prompt labels only in a terminal, keeping piped stdout machine-friendly.
        if sys.stdin.isatty() and sys.stdout.isatty():
            print("You> ", end="", flush=True)
        line = sys.stdin.readline()
        if not line:
            return
        prompt = line.rstrip("\r\n")
        if prompt.strip() == "/exit":
            return
        if not prompt.strip():
            continue
        answer = _reply(host, system, prompt, history, limit)
        print(answer, flush=True)
        # Failed requests do not poison the next turn; old turns are discarded.
        history.append((prompt, answer))
        if len(history) > limit:
            del history[:-limit]


def main(argv: list[str] | None = None) -> int:
    """Run one-shot or REPL chat with fixed, non-disclosing failure output."""
    try:
        _configure_utf8()
        args = _parser().parse_args(argv)
        if not _ENV_NAME.fullmatch(args.api_key_env):
            raise ValueError("invalid credential environment name")
        system = (Path(args.system_file).read_text(encoding="utf-8")
                  if args.system_file is not None else args.system)
        base_url = args.base_url if args.base_url is not None else os.environ.get("BOT_MODEL_BASE_URL")
        model = args.model if args.model is not None else os.environ.get("BOT_MODEL")
        # The existing host rejects missing models, unsafe URLs and missing HTTPS keys.
        host = ChatCompletionHost(base_url, model, os.environ.get(args.api_key_env, ""))
        if args.interactive:
            _interactive(host, system, args.history_turns)
        else:
            prompt = args.prompt if args.prompt is not None else sys.stdin.read()
            if not prompt.strip():
                raise ValueError("prompt is required")
            print(_reply(host, system, prompt, [], args.history_turns))
        return 0
    except (EOFError, KeyboardInterrupt):
        # A canceled terminal session is not a model error or persisted history.
        return 0
    except Exception:
        # Never print raw provider exceptions, response bodies, URLs, or keys.
        print(_ERROR, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
