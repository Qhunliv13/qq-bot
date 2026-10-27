"""Offline sidecar-lock contracts; fake fcntl makes POSIX locking testable on Windows."""

import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Load storage alone and redirect every file to the isolated test directory."""
    source = Path(__file__).resolve().parents[1] / "plugin" / "tasks.py"
    spec = importlib.util.spec_from_file_location("process_lock_tasks", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "STORE", str(tmp_path / "tasks.json"))
    # Replace only this module's os reference, not the process-global os module.
    proxy = SimpleNamespace(**{name: getattr(module.os, name) for name in dir(module.os)})
    proxy.name = "posix"
    monkeypatch.setattr(module, "os", proxy)
    return module


def test_nested_transactions_acquire_one_sidecar_lock(storage, monkeypatch):
    """A decorated function calling another public API shares one flock rather than deadlocking."""
    calls = []
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8, flock=lambda descriptor, operation: calls.append((descriptor, operation))))

    @storage._locked
    def nested():
        """Exercise genuine nested decorators using the public listing API."""
        return storage.listing(only_active=False)

    assert nested() == []
    assert [operation for _, operation in calls] == [2, 8]
    assert calls[0][0] == calls[1][0]
    assert Path(storage.STORE + ".lock").is_file()
    assert storage._TRANSACTION_LOCAL.paths == set()


def test_failed_transaction_releases_lock_and_next_transaction_reacquires(storage, monkeypatch):
    """Errors cannot leak advisory ownership or skip the next process-lock acquisition."""
    calls = []
    monkeypatch.setitem(sys.modules, "fcntl", SimpleNamespace(
        LOCK_EX=2, LOCK_UN=8, flock=lambda descriptor, operation: calls.append(operation)))

    @storage._locked
    def fail():
        """Model a storage validation failure inside an acquired transaction."""
        raise ValueError("offline failure")

    with pytest.raises(ValueError, match="offline failure"):
        fail()
    assert storage._TRANSACTION_LOCAL.paths == set()
    assert storage.listing() == []
    assert calls == [2, 8, 2, 8]


def test_windows_fallback_does_not_import_fcntl_or_create_sidecar(storage):
    """The existing thread lock remains usable where POSIX flock is unavailable."""
    storage.os.name = "nt"
    assert storage.listing() == []
    assert not Path(storage.STORE + ".lock").exists()
