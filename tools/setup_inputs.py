"""Collect first-run model inputs locally; never probe providers or expose secrets."""

import getpass
import os
import re
import unicodedata
from urllib.parse import unquote, urlsplit
import warnings


# Keep prompts bilingual without guessing a provider-specific URL or model name.
_PROMPTS = {
    "base_url": "模型 Base URL / Model base URL (保留服务商路径 / keep provider path): ",
    "model": "模型 ID / Model ID: ",
    "api_key": "模型 API Key（隐藏输入）/ Model API key (hidden): ",
}


def _has_control(value):
    """Reject Unicode control characters before parsers can silently discard them."""
    return any(unicodedata.category(character) == "Cc" for character in value)


def validate_model_field(name, value):
    """Validate one model field without normalization or including its value in errors."""
    if name not in _PROMPTS:
        # Unknown names are programming mistakes, not an excuse to echo user data.
        raise ValueError("unsupported model field")
    if not isinstance(value, str) or not value.strip() or _has_control(value):
        raise ValueError("model " + name + " must be nonempty and contain no control characters / 必须非空且不含控制字符")
    if name != "base_url":
        # Keys and model IDs are opaque provider-defined strings.
        return value
    try:
        url = urlsplit(value)
        port = url.port  # Access triggers stdlib validation of malformed/range-invalid ports.
        valid = (
            url.scheme in ("http", "https")
            and bool(url.hostname)
            and "@" not in url.netloc
            and "?" not in value
            and "#" not in value
            and not any(character.isspace() for character in value)
            and not url.netloc.endswith(":")
            and (port is None or 1 <= port <= 65535)
        )
    except ValueError:
        # URL parser exceptions may include input, so replace them with a safe message.
        valid = False
    if not valid:
        raise ValueError("model base URL must be http(s), with a valid host/port and no credentials, query, fragment or whitespace / 地址格式无效") from None
    if unquote(url.path).rstrip("/").lower().endswith("/chat/completions"):
        # A full request endpoint is a common first-run mistake; never auto-rewrite it.
        raise ValueError("请输入 Base URL，不要包含 /chat/completions；保留服务商要求的路径 / Enter a base URL, not the full /chat/completions endpoint")
    return value


def validate_owner_ids(values):
    """Accept optional positive 5-20 ASCII-digit QQ IDs without coercion or deduplication."""
    if not isinstance(values, list) or any(
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{5,20}", value)
        or int(value) <= 0
        for value in values
    ):
        raise ValueError("owner QQs must be positive 5-20 ASCII digits / QQ 号必须是 5-20 位正数 ASCII 数字")
    return values


def _read_hidden_key():
    """Fail closed if getpass would fall back to echoing a credential on this terminal."""
    try:
        with warnings.catch_warnings():
            # getpass warns immediately before its insecure input fallback.
            warnings.simplefilter("error", getpass.GetPassWarning)
            return getpass.getpass(_PROMPTS["api_key"])
    except getpass.GetPassWarning:
        raise ValueError("无法安全隐藏 API Key；请使用交互终端或 --api-key-env / Hidden input unavailable; use an interactive terminal or --api-key-env") from None


def _prompt_model_field(name):
    """Retry only the current interactive field; ordinary prompts strip surrounding spaces."""
    while True:
        value = _read_hidden_key() if name == "api_key" else input(_PROMPTS[name]).strip()
        try:
            return validate_model_field(name, value)
        except ValueError as error:
            # The shared validator emits only generic, secret-free feedback.
            print(str(error))


def _prompt_owner_ids():
    """Let users skip QQ ownership with Enter, retrying invalid comma-separated input."""
    while True:
        raw = input("主人 QQ（逗号分隔，回车跳过）/ Owner QQ IDs (comma-separated, Enter to skip): ").strip()
        values = [value.strip() for value in raw.split(",")] if raw else []
        try:
            return validate_owner_ids(values)
        except ValueError as error:
            print(str(error))


def collect_model_inputs(args):
    """Return (base, key, model, owners), retrying prompts but never invalid supplied flags.

    This function performs no writes and no network access. CLI strings and environment
    credentials remain verbatim. Missing environment keys may be entered securely in
    interactive mode; non-interactive mode never prompts. Persona belongs to bootstrap.
    """
    values = {"base_url": args.model_base, "model": args.model}
    # Read credentials only from the explicitly named environment variable, never argv.
    values["api_key"] = os.environ.get(args.api_key_env) if args.api_key_env else None
    if values["api_key"] == "":
        values["api_key"] = None
    owners = args.owner_qq
    # Validate all supplied values first so broken automation never starts prompting.
    for name, value in values.items():
        if value is not None:
            validate_model_field(name, value)
    if owners is not None:
        validate_owner_ids(owners)
    for name in ("base_url", "model", "api_key"):
        if values[name] is None:
            if args.non_interactive:
                validate_model_field(name, None)
            values[name] = _prompt_model_field(name)
    if owners is None:
        # Owners are genuinely optional, including in unattended setup.
        owners = [] if args.non_interactive else _prompt_owner_ids()
    return values["base_url"], values["api_key"], values["model"], owners
