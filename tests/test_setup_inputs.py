"""First-run collection is recoverable, offline, deterministic and secret-safe."""

from argparse import Namespace
import warnings

import pytest

from tools import setup_inputs


def make_args(**overrides):
    """Build only the public bootstrap arguments needed by the collector."""
    values = dict(model_base=None, model=None, api_key_env=None, non_interactive=False, owner_qq=None)
    values.update(overrides)
    return Namespace(**values)


def install_inputs(monkeypatch, ordinary, hidden=()):
    """Supply fake ordinary/hidden terminals and record prompts without real input."""
    ordinary_values = iter(ordinary)
    hidden_values = iter(hidden)
    prompts = []

    def read_ordinary(prompt):
        """Record a normal prompt and return its next simulated answer."""
        prompts.append(("ordinary", prompt))
        return next(ordinary_values)

    def read_hidden(prompt):
        """Record only a prompt, never the fake secret, just like getpass."""
        prompts.append(("hidden", prompt))
        return next(hidden_values)

    monkeypatch.setattr("builtins.input", read_ordinary)
    monkeypatch.setattr(setup_inputs.getpass, "getpass", read_hidden)
    return prompts


@pytest.mark.parametrize("base", [
    "https://provider.invalid", "http://localhost:8000/v1", "https://provider.invalid/vendor/custom/v2/",
    "https://provider.invalid/api/openai/v1", "http://[::1]:8080/custom", "HTTPS://provider.invalid/v1",
])
def test_valid_base_preserved(base):
    """Provider-defined prefixes, casing and trailing slashes are never rewritten."""
    assert setup_inputs.validate_model_field("base_url", base) == base


@pytest.mark.parametrize("base", [
    "", " ", "ftp://provider.invalid", "https:///v1", "https://user:secret@provider.invalid/v1",
    "https://@provider.invalid", "https://provider.invalid?", "https://provider.invalid#",
    "https://provider.invalid?key=private", "https://provider.invalid#private",
    "https://provider.invalid:bad", "https://provider.invalid:65536", "https://provider.invalid:0",
    "https://provider.invalid:", "https://[broken", "https://provider.invalid/a\nb",
    "https://provider.invalid/\x7f", "https://provider.invalid/\x85", "https://bad host.invalid/v1",
])
def test_invalid_bases_fail_without_echo(base):
    """Errors reject unsafe URLs without leaking their credentials or raw parser errors."""
    with pytest.raises(ValueError) as error:
        setup_inputs.validate_model_field("base_url", base)
    assert "private" not in str(error.value) and "secret" not in str(error.value)


@pytest.mark.parametrize("suffix", ["/chat/completions", "/chat/completions/", "/CHAT/COMPLETIONS", "/chat%2fcompletions"])
def test_full_endpoint_has_targeted_guidance(suffix):
    """Common endpoint pastes get an actionable retry hint, not automatic path surgery."""
    with pytest.raises(ValueError, match="/chat/completions"):
        setup_inputs.validate_model_field("base_url", "https://provider.invalid/provider/v1" + suffix)


@pytest.mark.parametrize("name", ["model", "api_key"])
@pytest.mark.parametrize("value", [None, 4, "", " \t", "text\nnext", "text\x00", "text\x7f", "text\x85"])
def test_model_and_key_require_control_free_text(name, value):
    """Opaque model IDs and credentials still reject missing values and controls."""
    with pytest.raises(ValueError):
        setup_inputs.validate_model_field(name, value)


@pytest.mark.parametrize("name,value", [("model", " vendor/model:latest "), ("api_key", " secret with spaces ")])
def test_opaque_supplied_values_are_verbatim(name, value):
    """Shared validation must not strip or normalize values supplied outside prompts."""
    assert setup_inputs.validate_model_field(name, value) == value


@pytest.mark.parametrize("owners", [[], ["12345"], ["00001", "12345678901234567890"], ["12345", "12345"]])
def test_valid_optional_owners_preserved(owners):
    """Optional owner lists remain ordered and are not normalized by validation."""
    assert setup_inputs.validate_owner_ids(owners) is owners


@pytest.mark.parametrize("owners", [None, "12345", [12345], ["1234"], ["1" * 21], ["00000"], ["１２３４５"], ["١٢٣٤٥"], ["+12345"], [" 12345"], [""]])
def test_invalid_owners_rejected(owners):
    """QQ identities must be explicit positive ASCII-digit strings, not coerced numbers."""
    with pytest.raises(ValueError):
        setup_inputs.validate_owner_ids(owners)


def test_first_run_retries_only_invalid_field(monkeypatch, capsys):
    """A typo never discards an earlier valid answer or accidentally echoes a secret."""
    prompts = install_inputs(monkeypatch, [
        "not-url", "https://provider.invalid/prefix/chat/completions", " https://provider.invalid/custom/v2/ ",
        "", " my-model ", "１２３４５", "12345, 67890",
    ], ["", "private\nkey", " private-key "])
    result = setup_inputs.collect_model_inputs(make_args())
    assert result == ("https://provider.invalid/custom/v2/", " private-key ", "my-model", ["12345", "67890"])
    assert [kind for kind, prompt in prompts] == ["ordinary"] * 5 + ["hidden"] * 3 + ["ordinary"] * 2
    output = capsys.readouterr()
    assert "/chat/completions" in output.out
    assert "private-key" not in output.out + output.err
    assert "private\nkey" not in output.out + output.err


def test_empty_owner_prompt_skips_qq(monkeypatch):
    """Model-only use does not require a QQ identity."""
    install_inputs(monkeypatch, ["https://provider.invalid/v1", "model", ""], ["private-key"])
    assert setup_inputs.collect_model_inputs(make_args())[3] == []


def test_supplied_values_never_prompt_or_change(monkeypatch):
    """Fully supplied flags are deterministic even when interactive mode is the default."""
    prompts = install_inputs(monkeypatch, [])
    monkeypatch.setenv("SETUP_TEST_KEY", " private-key ")
    args = make_args(model_base="https://provider.invalid/path/", model=" model ", api_key_env="SETUP_TEST_KEY", owner_qq=[])
    assert setup_inputs.collect_model_inputs(args) == (args.model_base, " private-key ", " model ", [])
    assert prompts == []


@pytest.mark.parametrize("overrides", [{"model_base": "broken"}, {"model": ""}, {"owner_qq": ["bad"]}])
def test_invalid_explicit_flags_fail_before_prompts(monkeypatch, overrides):
    """A malformed script argument is never silently converted into an interactive wizard."""
    prompts = install_inputs(monkeypatch, [])
    with pytest.raises(ValueError):
        setup_inputs.collect_model_inputs(make_args(**overrides))
    assert prompts == []


def test_invalid_env_key_fails_before_prompts(monkeypatch, capsys):
    """A supplied invalid environment credential is rejected without disclosure."""
    prompts = install_inputs(monkeypatch, [])
    monkeypatch.setenv("SETUP_TEST_KEY", "private\nkey")
    with pytest.raises(ValueError) as error:
        setup_inputs.collect_model_inputs(make_args(api_key_env="SETUP_TEST_KEY"))
    assert "private" not in str(error.value) + capsys.readouterr().out
    assert prompts == []


@pytest.mark.parametrize("env_value", [None, ""])
def test_missing_env_key_gets_hidden_prompt(monkeypatch, env_value):
    """An unset or empty named environment variable is recoverable interactively."""
    if env_value is None:
        monkeypatch.delenv("SETUP_MISSING_KEY", raising=False)
    else:
        monkeypatch.setenv("SETUP_MISSING_KEY", env_value)
    prompts = install_inputs(monkeypatch, [], ["entered-key"])
    args = make_args(model_base="https://provider.invalid", model="model", api_key_env="SETUP_MISSING_KEY", owner_qq=[])
    assert setup_inputs.collect_model_inputs(args)[1] == "entered-key"
    assert [kind for kind, prompt in prompts] == ["hidden"]


@pytest.mark.parametrize("missing", ["model_base", "model", "api_key_env"])
def test_noninteractive_missing_fields_never_prompt(monkeypatch, missing):
    """Unattended setup fails closed instead of reading from piped input."""
    prompts = install_inputs(monkeypatch, [])
    monkeypatch.setenv("SETUP_TEST_KEY", "key")
    values = dict(model_base="https://provider.invalid", model="model", api_key_env="SETUP_TEST_KEY", non_interactive=True)
    values[missing] = None
    with pytest.raises(ValueError):
        setup_inputs.collect_model_inputs(make_args(**values))
    assert prompts == []


def test_noninteractive_owners_optional(monkeypatch):
    """Unattended users may configure just the model with no QQ identities."""
    prompts = install_inputs(monkeypatch, [])
    monkeypatch.setenv("SETUP_TEST_KEY", "key")
    result = setup_inputs.collect_model_inputs(make_args(model_base="https://provider.invalid", model="model", api_key_env="SETUP_TEST_KEY", non_interactive=True))
    assert result == ("https://provider.invalid", "key", "model", [])
    assert prompts == []


def test_getpass_echo_fallback_is_blocked(monkeypatch):
    """Do not suppress getpass warnings and then accidentally allow an echoed secret."""
    def unsafe_terminal(prompt):
        """Match the warning emitted immediately before getpass's echoing fallback."""
        warnings.warn("cannot hide input", setup_inputs.getpass.GetPassWarning)
        raise AssertionError("insecure fallback must never execute")

    monkeypatch.setattr(setup_inputs.getpass, "getpass", unsafe_terminal)
    args = make_args(model_base="https://provider.invalid", model="model", owner_qq=[])
    with pytest.raises(ValueError, match="--api-key-env"):
        setup_inputs.collect_model_inputs(args)


@pytest.mark.parametrize("failure", [EOFError, KeyboardInterrupt])
def test_terminal_cancellation_propagates(monkeypatch, failure):
    """Bootstrap owns cancellation handling; collection must not loop forever on EOF."""
    def cancelled(prompt):
        """Represent the terminal ending before any answer is entered."""
        raise failure()

    monkeypatch.setattr("builtins.input", cancelled)
    with pytest.raises(failure):
        setup_inputs.collect_model_inputs(make_args())
