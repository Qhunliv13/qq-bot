"""Portable state resolution without touching private bot files or live services."""
import importlib.util
from pathlib import Path

import pytest

from plugin import runtime_paths

ROOT = Path(__file__).resolve().parents[1]


def test_default_is_current_account_home_not_another_account(monkeypatch, tmp_path):
    """Root retains the legacy layout naturally; normal users get their own home."""
    monkeypatch.delenv('HIGAN_ROOT', raising=False)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    assert runtime_paths.data_root() == tmp_path / 'higan'
    assert not (tmp_path / 'higan').exists()


@pytest.mark.parametrize('value', ['', ' ', 'relative/data', 'bad\npath'])
def test_invalid_override_is_not_silently_redirected(monkeypatch, value):
    """A malformed deployment root must fail instead of creating a parallel store."""
    monkeypatch.setenv('HIGAN_ROOT', value)
    with pytest.raises(ValueError, match='HIGAN_ROOT'):
        runtime_paths.data_root()


@pytest.mark.parametrize('name,suffix,attribute', [
    ('tasks', 'tasks', 'ROOT'), ('vm_jobs', 'jobs', '_ROOT'),
    ('workspace_tools', '', '_ROOT'),
])
def test_all_stores_honor_the_same_explicit_root(monkeypatch, tmp_path, name, suffix, attribute):
    """Direct-file probe imports must share paths with actual package imports."""
    monkeypatch.setenv('HIGAN_ROOT', str(tmp_path))
    spec = importlib.util.spec_from_file_location('isolated_' + name, ROOT / 'plugin' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert Path(getattr(module, attribute)) == tmp_path / suffix
    assert not list(tmp_path.iterdir())


def test_default_history_resolves_configured_root_at_construction(monkeypatch, tmp_path):
    """History creates only the explicitly selected archive; no migration is implied."""
    from plugin.history import Recorder
    monkeypatch.setenv('HIGAN_ROOT', str(tmp_path))
    recorder = Recorder()
    assert Path(recorder.root) == tmp_path / 'history'
    assert (tmp_path / 'history').is_dir()
