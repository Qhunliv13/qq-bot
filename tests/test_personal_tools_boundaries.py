"""Offline checks for explicit private probe state and identity-free audit fixtures."""
import asyncio
import importlib.util
import json
from pathlib import Path
import sys
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def cache_probe(monkeypatch):
    """Load only standard-library probe definitions, never AstrBot or real config."""
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("cache_layout_boundary_test", ROOT / "tools" / "cache_layout_probe.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cache_probe_requires_explicit_private_data_root(cache_probe, monkeypatch, tmp_path):
    """Omitting the selected history root must fail before plugin import or networking."""
    monkeypatch.setattr(sys, "argv", ["cache_layout_probe.py", "--plugin", str(tmp_path), "--output", str(tmp_path / "result.json")])
    with pytest.raises(SystemExit) as error:
        cache_probe.main()
    assert error.value.code == 2


def test_cache_probe_reads_only_selected_state_and_reports_counts(cache_probe, monkeypatch, tmp_path):
    """Model all provider edges while exercising real root selection and layout comparison."""
    state = tmp_path / "selected-state"
    (state / "history").mkdir(parents=True)
    (state / "autonomy.json").write_text(json.dumps({"origins": {"42": "synthetic"}}), encoding="utf-8")
    (state / "history" / "42.jsonl").write_text(json.dumps({"line": "private fixture history"}) + "\n", encoding="utf-8")
    output = tmp_path / "result.json"
    requests = []
    archive_paths = []

    class Bot:
        """Expose non-secret model settings without loading real runtime configuration."""
        @staticmethod
        def _her_llm_config():
            """Use an inert reserved endpoint which must never be contacted."""
            return "https://provider.invalid/v1", "test-only", "fixture-model"

        @staticmethod
        def _load_persona():
            """Use synthetic persona text instead of operator data."""
            return "fixture persona"

        @staticmethod
        def _tool_schemas():
            """No fixture tools are advertised or executed."""
            return []

        @staticmethod
        def _llm_extra():
            """Leave production parameter merging behavior intact."""
            return {}

    class Archive:
        """Capture the exact selected archive path without opening live observations."""
        def __init__(self, path):
            archive_paths.append(Path(path))

        def recent(self, scope):
            """Supply local-only evidence for the selected fixture scope."""
            assert scope == "42"
            return [{"result": "private fixture observation"}]

    def build_context(**kwargs):
        """Keep compared layouts lossless while verifying selected history is used."""
        assert kwargs["history"] == ["private fixture history"]
        return json.dumps({"version": 1, "conversation": kwargs["history"],
                           "observations": kwargs["observations"], "trigger": kwargs["messages"]})

    modules = {
        "conversation_context": {"build_context": build_context, "system_prompt": lambda value: value},
        "history_window": {"history_snapshot": lambda history, offset: history},
        "tool_context": {"ToolContextArchive": Archive},
        "model_usage": {"usage_metrics": lambda response: response["usage"]},
    }
    for name, attributes in modules.items():
        module = ModuleType("release_probe_plugin." + name)
        module.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setattr(cache_probe, "load_plugin", lambda directory: type("Plugin", (), {"HiganPlugin": Bot}))

    class Response:
        """Return a local JSON stream without any HTTP or credential side effects."""
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            """Model only numeric usage telemetry from a provider."""
            return b'{"usage":{"prompt_tokens":12}}'

    def fake_urlopen(request, timeout):
        """Record the intended provider payload but never open the reserved endpoint."""
        assert request.full_url == "https://provider.invalid/v1/chat/completions"
        requests.append(json.loads(request.data))
        return Response()

    monkeypatch.setattr(cache_probe.urllib.request, "urlopen", fake_urlopen)
    asyncio.run(cache_probe.probe(tmp_path / "plugin", output, state))
    assert archive_paths == [state / "tool_observations"]
    assert len(requests) == 4
    report = output.read_text(encoding="utf-8")
    assert "private fixture" not in report and str(state) not in report
    assert json.loads(report)["parsed_data_equal"] is True


def test_contract_owner_is_synthetic_and_private_settings_are_restored(monkeypatch, tmp_path):
    """Audit identity must not depend on a real administrator or read private settings."""
    monkeypatch.syspath_prepend(str(ROOT / "tests"))
    monkeypatch.setenv("HIGAN_SETTINGS_FILE", str(tmp_path / "must-not-read.json"))
    monkeypatch.setenv("HIGAN_OWNER_QQS", '["999999999"]')
    spec = importlib.util.spec_from_file_location("identity_boundary_audit", ROOT / "tools" / "tool_contract_audit.py")
    audit = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(audit)
    with pytest.MonkeyPatch.context() as patch:
        harness = audit.AuditHarness(tmp_path / "audit", patch)
        _, event = harness.prepare("qq_set_signature")
        assert event.get_sender_id() == audit.AUDIT_OWNER
        assert harness.plugin._is_owner(event)
        event.get_sender_id = lambda: "999999999"
        assert not harness.plugin._is_owner(event)
    import os
    assert os.environ["HIGAN_SETTINGS_FILE"] == str(tmp_path / "must-not-read.json")
    assert os.environ["HIGAN_OWNER_QQS"] == '["999999999"]'


def test_simulated_owner_role_does_not_require_real_administrators(monkeypatch, tmp_path):
    """An empty real owner list must not break synthetic owner-role model audits."""
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location("simulation_identity_test", ROOT / "tools" / "model_simulation_audit.py")
    simulation = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(simulation)
    identities = []

    class Bot:
        """Use no framework, model or platform while retaining simulation fixture setup."""
        @staticmethod
        def _load_persona():
            """Use a synthetic persona and no private configuration."""
            return "fixture persona"

        @staticmethod
        def _policy_allows(event, scope):
            """Allow fixture turns only, without sending anything."""
            return True

        @staticmethod
        def _clock_context():
            """Return fixed non-private timing metadata."""
            return {}

        async def _handle_one(self, event, scope, *args):
            """Observe the role identity instead of invoking any real model transport."""
            identities.append((event.get_sender_id(), self._who(event)))

    class Archive:
        """Reserve no real evidence or history store."""
        def __init__(self, path):
            pass

    class Policy:
        """Model only the explicit fixture policy update."""
        def __init__(self, path):
            pass

        def update(self, *args):
            """Do not mutate any production policy."""
            return {}

    plugin = ModuleType("fixture_plugin")
    plugin.HiganPlugin = Bot
    plugin.OWNER_QQS = set()
    monkeypatch.setattr(simulation, "load_plugin", lambda directory: plugin)
    parent = ModuleType("release_probe_plugin")
    parent.__path__ = []
    tasks = ModuleType("release_probe_plugin.tasks")
    parent.tasks = tasks
    monkeypatch.setitem(sys.modules, "release_probe_plugin", parent)
    monkeypatch.setitem(sys.modules, "release_probe_plugin.tasks", tasks)
    context = ModuleType("release_probe_plugin.tool_context")
    context.ToolContextArchive, context.redact = Archive, lambda value: value
    policy = ModuleType("release_probe_plugin.group_policy")
    policy.GroupPolicyStore = Policy
    monkeypatch.setitem(sys.modules, context.__name__, context)
    monkeypatch.setitem(sys.modules, policy.__name__, policy)
    candidate = tmp_path / "plugin"
    candidate.mkdir()
    (candidate / "main.py").write_text("# inert fixture\n", encoding="utf-8")
    asyncio.run(simulation.audit(candidate, tmp_path / "simulation.json", False, role="owner"))
    assert identities
    assert all(identity == simulation.SIM_OWNER and simulation.SIM_OWNER in label for identity, label in identities)
    assert plugin.OWNER_QQS == {simulation.SIM_OWNER}
