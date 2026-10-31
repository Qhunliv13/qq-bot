"""Offline native-asset tests: no real pip installs, models, servers, or vendor edits."""

import ast
from collections import deque
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error
import venv
import zipfile

import pytest

from tools import native_assets as assets

# Keep the real read-only probe available when provisioning fixtures stub readiness.
_REAL_DEPENDENCY_PROBE = assets._dependencies_valid


@pytest.fixture
def vendor_repo(tmp_path):
    """Create a miniature upstream checkout with both frontend and backend dirs."""
    repo = tmp_path / "repo"
    root = repo / "vendor" / "astrbot"
    files = {
        "pyproject.toml": '[project]\nname="AstrBot"\nversion="4.28.1"\ndependencies=["quart>=0.20.0", "audioop-lts ; python_full_version >= \'3.13\'"]\n',
        "main.py": 'print("runtime")\n',
        "astrbot/__init__.py": '__version__="4.28.1"\n',
        "astrbot/dashboard/server.py": '"""Python dashboard server, not Node source."""\n',
        "README.md": "Upstream documentation\n",
        "LICENSE": "AGPL upstream license\n",
        "dashboard/package.json": '{}\n',
        "dashboard/node_modules/example/a.js": "frontend\n",
        "tests/test_upstream.py": "pass\n",
        ".git/config": "private path must not be copied\n",
        "astrbot/__pycache__/cached.pyc": "cache\n",
    }
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return repo


@pytest.fixture
def fake_run(monkeypatch):
    """Record isolated commands and synthesize a dedicated venv without executing."""
    calls = []

    def run(arguments, cwd, label):
        """Create only the local files normally produced by Python's venv step."""
        calls.append((list(arguments), Path(cwd), label))
        if label == "venv creation":
            venv = Path(arguments[-1])
            python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            python.parent.mkdir(parents=True)
            python.write_bytes(b"fake venv interpreter")
            (venv / "pyvenv.cfg").write_text("include-system-site-packages = false\nversion = " + ".".join(map(str, sys.version_info[:3])) + "\n", encoding="utf-8")

    monkeypatch.setattr(assets, "_run", run)
    # Ordinary provisioning tests use a synthetic interpreter. Dedicated probe
    # tests below execute the real isolated Python against metadata fixtures.
    monkeypatch.setattr(assets, "_dependencies_valid", lambda *args: True)
    if os.name == "nt":
        from plugin import local_runtime

        def private_cache(path, *, create=True):
            """Stub only the shared cache-leaf ACL boundary, never real OS tooling."""
            path.mkdir(exist_ok=True)
            return path

        monkeypatch.setattr(local_runtime, "ensure_private_windows_path", private_cache)
    return calls


@pytest.fixture
def official_download(monkeypatch):
    """Supply a tiny official-shaped wheel and metadata with a matching test pin."""
    requests = []

    def configure(entries=None, version="v4.28.1", digest=None):
        """Install a deterministic fake download transport without changing production pins."""
        contents = {
            "astrbot/dashboard/dist/index.html": '<html><script type="module" src="/assets/main.js"></script><link rel="stylesheet" href="/assets/main.css"></html>',
            "astrbot/dashboard/dist/assets/main.js": 'console.log("bundled");',
            "astrbot/dashboard/dist/assets/main.css": "body{}",
            "astrbot/dashboard/dist/assets/version": version,
            # Official wheels also contain Python; it must never be extracted.
            "astrbot/__init__.py": "UNTRUSTED WHEEL CORE MUST NOT REPLACE VENDOR",
            "astrbot-4.28.1.dist-info/METADATA": "Name: AstrBot\nVersion: 4.28.1\n",
        }
        contents.update(entries or {})
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, content in contents.items():
                # ZipInfo normalizes backslashes on Windows at construction time;
                # assign the raw name afterward to model malicious archive input.
                entry = zipfile.ZipInfo()
                entry.filename = name
                archive.writestr(entry, content)
        payload = buffer.getvalue()
        expected = hashlib.sha256(payload).hexdigest()
        monkeypatch.setattr(assets, "WHEEL_SHA256", expected)
        metadata = {"info": {"name": "AstrBot", "version": "4.28.1"}, "urls": [{
            "filename": assets.WHEEL_FILENAME, "packagetype": "bdist_wheel", "yanked": False,
            "url": "https://files.pythonhosted.org/packages/official/" + assets.WHEEL_FILENAME,
            "size": len(payload), "digests": {"sha256": digest or expected}}]}

        def download(url, maximum, hostname):
            """Return only official-shaped metadata or the matching fake wheel."""
            requests.append((url, maximum, hostname))
            return json.dumps(metadata).encode() if url == assets.PYPI_JSON_URL else payload

        monkeypatch.setattr(assets, "_download", download)
        return metadata, payload

    configure()
    return requests, configure


def test_provision_copies_full_core_without_frontend_or_vendor_mutation(vendor_repo, tmp_path, fake_run, official_download):
    """Preserve licenses/core and require all dependencies from the sole pyproject."""
    data = tmp_path / "state"
    vendor = vendor_repo / "vendor" / "astrbot"
    before = {str(path.relative_to(vendor)): path.read_bytes() for path in vendor.rglob("*") if path.is_file()}
    result = assets.prepare_assets(data, vendor_repo)
    root = result["astrbot_root"]
    assert root == data / "runtime" / "astrbot"
    assert result["dashboard"] == root / "astrbot" / "dashboard" / "dist"
    assert set(result) == {"python", "astrbot_root", "dashboard"}
    for name in ("main.py", "pyproject.toml", "README.md", "LICENSE", "astrbot/dashboard/server.py"):
        assert (root / name).read_bytes() == (vendor / name).read_bytes()
    for name in ("dashboard", "tests", ".git", "astrbot/__pycache__"):
        assert not (root / name).exists()
    assert (root / "astrbot/__init__.py").read_bytes() == (vendor / "astrbot/__init__.py").read_bytes()
    install = next(arguments for arguments, _, label in fake_run if label == "dependency installation")
    assert install[0] == result["python"]
    assert install[1:4] == ["-m", "pip", "--isolated"]
    assert install[-2:] == ["quart>=0.20.0", "audioop-lts ; python_full_version >= '3.13'"]
    assert "--index-url" in install and "https://pypi.org/simple" in install
    assert install[install.index("--cache-dir") + 1] == str(data / "runtime" / "pip-cache")
    assert "--no-cache-dir" not in install
    assert (data / "runtime" / "pip-cache").is_dir()
    assert [label for _, _, label in fake_run] == ["venv creation", "dependency installation", "dependency verification"]
    assert assets.inspect_assets(data, vendor_repo)["ready"]
    assert before == {str(path.relative_to(vendor)): path.read_bytes() for path in vendor.rglob("*") if path.is_file()}
    assert assets.provision is assets.prepare_assets


def test_second_provision_is_offline_and_noop(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Completed assets must not spawn pip or access the network on repeat use."""
    data = tmp_path / "state"
    expected = assets.prepare_assets(data, vendor_repo)
    original = {str(path.relative_to(data)): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in data.rglob("*") if path.is_file()}

    def forbidden(*args, **kwargs):
        """Fail any attempted network or child-process invocation on a ready snapshot."""
        raise AssertionError("unexpected network/process operation")

    monkeypatch.setattr(assets, "_run", forbidden)
    monkeypatch.setattr(assets, "_download", forbidden)
    assert assets.prepare_assets(data, vendor_repo) == expected
    assert assets.inspect_assets(data, vendor_repo)["ready"]
    assert original == {str(path.relative_to(data)): (path.read_bytes(), path.stat().st_mtime_ns)
                        for path in data.rglob("*") if path.is_file()}


def test_doctor_missing_assets_is_strictly_read_only(vendor_repo, tmp_path, monkeypatch):
    """Doctor reports missing steps without creating state or accessing processes/net."""
    data = tmp_path / "not-created"

    def forbidden(*args, **kwargs):
        """Prohibit any side effect during read-only inspection."""
        raise AssertionError("side effect in doctor")

    monkeypatch.setattr(assets, "_run", forbidden)
    monkeypatch.setattr(assets, "_download", forbidden)
    report = assets.inspect_assets(data, vendor_repo)
    assert not report["ready"]
    assert report["checks"] == {"setup_cleanup": True, **dict.fromkeys(("source", "venv", "dependencies", "dashboard"), False)}
    assert not data.exists()


@pytest.mark.parametrize("changed", ["vendor", "snapshot", "unmanaged", "added"])
def test_different_source_is_never_autopatched(vendor_repo, tmp_path, fake_run, official_download, changed):
    """Source provenance changes must stop before pip/download and retain existing files."""
    data = tmp_path / "state"
    root = assets.prepare_assets(data, vendor_repo)["astrbot_root"]
    target = root / "main.py"
    if changed == "vendor":
        (vendor_repo / "vendor/astrbot/main.py").write_text("# changed vendor\n", encoding="utf-8")
    elif changed == "snapshot":
        target.write_text("# user override\n", encoding="utf-8")
    elif changed == "added":
        (root / "astrbot/unexpected_module.py").write_text("# injected source\n", encoding="utf-8")
    else:
        (root / assets.SOURCE_MARKER).unlink()
    original = target.read_bytes()
    count = len(fake_run)
    with pytest.raises(assets.AssetError, match="runtime source differs"):
        assets.prepare_assets(data, vendor_repo)
    assert target.read_bytes() == original
    assert len(fake_run) == count
    assert not assets.inspect_assets(data, vendor_repo)["checks"]["source"]


def test_empty_root_is_supported_without_erasing_persistent_state(vendor_repo, tmp_path, fake_run, official_download):
    """An empty bootstrap directory is valid; runtime data remains parent-owned."""
    data = tmp_path / "state"
    (data / "runtime" / "astrbot").mkdir(parents=True)
    root = assets.prepare_assets(data, vendor_repo)["astrbot_root"]
    state = root / "data" / "settings.json"
    state.parent.mkdir()
    state.write_text('{"credential":"local-user-secret"}', encoding="utf-8")
    assets.prepare_assets(data, vendor_repo)
    assert json.loads(state.read_text(encoding="utf-8"))["credential"] == "local-user-secret"


def test_failed_dependencies_are_not_marked_complete_and_can_retry(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Pip cache survives a clean venv retry; every vendor requirement is installed again."""
    data = tmp_path / "state"
    cache = data / "runtime" / "pip-cache"
    wheel = cache / "completed-wheel.whl"
    global_cache = tmp_path / "global-cache"
    global_cache.mkdir()
    sentinel = global_cache / "untouched"
    sentinel.write_bytes(b"global cache must not change")
    original = sentinel.stat().st_mtime_ns
    monkeypatch.setenv("PIP_CACHE_DIR", str(global_cache))
    successful = assets._run
    failed_install = []

    def fail(arguments, cwd, label):
        """Model pip's completed cache artifact without running pip or downloading."""
        if label == "dependency installation":
            failed_install.extend(arguments)
            wheel.write_bytes(b"retained completed download")
            raise RuntimeError("https://private-token-secret@untrusted.invalid/package")
        successful(arguments, cwd, label)

    monkeypatch.setattr(assets, "_run", fail)
    with pytest.raises(assets.AssetError) as caught:
        assets.prepare_assets(data, vendor_repo)
    assert "private-token-secret" not in str(caught.value)
    assert not (data / "runtime" / assets.DEPENDENCY_MARKER).exists()
    assert not assets.inspect_assets(data, vendor_repo)["ready"]
    # Model an explicit cleanup of this test's incomplete venv, not a product repair.
    shutil.rmtree(data / "runtime" / ".venv")
    assert wheel.read_bytes() == b"retained completed download"
    monkeypatch.setattr(assets, "_run", successful)
    assert assets.prepare_assets(data, vendor_repo)
    install = next(arguments for arguments, _, label in fake_run if label == "dependency installation")
    assert install == failed_install
    assert install[install.index("--cache-dir") + 1] == str(cache)
    assert install[-2:] == ["quart>=0.20.0", "audioop-lts ; python_full_version >= '3.13'"]
    assert wheel.read_bytes() == b"retained completed download"
    assert list(global_cache.iterdir()) == [sentinel]
    assert sentinel.read_bytes() == b"global cache must not change"
    assert sentinel.stat().st_mtime_ns == original
    assert assets.inspect_assets(data, vendor_repo)["ready"]


@pytest.mark.parametrize("version", ["4.28.0", "v4.28.0", "", "v4.28.1-dev"])
def test_dashboard_version_mismatch_never_publishes(vendor_repo, tmp_path, fake_run, official_download, version):
    """The wheel version is not sufficient: its UI marker must match the core pin."""
    _, configure = official_download
    configure(version=version)
    data = tmp_path / "state"
    with pytest.raises(assets.AssetError, match="incomplete or version-mismatched"):
        assets.prepare_assets(data, vendor_repo)
    assert not (data / "runtime/astrbot/astrbot/dashboard/dist").exists()


def test_bad_wheel_digest_is_rejected_before_extraction(vendor_repo, tmp_path, fake_run, official_download):
    """Do not trust an altered live digest for an already pinned release."""
    _, configure = official_download
    configure(digest="0" * 64)
    with pytest.raises(assets.AssetError, match="metadata is invalid"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)


def test_wheel_payload_must_match_official_digest(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """A correctly named HTTPS wheel still needs a byte-for-byte SHA256 match."""
    metadata, payload = official_download[1]()

    def changed_download(url, maximum, hostname):
        """Return a corrupt equal-sized payload to exercise actual digest verification."""
        return json.dumps(metadata).encode() if url == assets.PYPI_JSON_URL else payload[:-1] + bytes([payload[-1] ^ 1])

    monkeypatch.setattr(assets, "_download", changed_download)
    with pytest.raises(assets.AssetError, match="SHA256 verification"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)


@pytest.mark.parametrize("name", ["../outside.txt", "assets/../../outside.txt", "/absolute", "assets\\outside.js", "assets/CON", "assets/a:b", "assets/file.", "assets/./dot.js"])
def test_zip_slip_and_windows_aliases_are_rejected(vendor_repo, tmp_path, fake_run, official_download, name):
    """Validate every extracted relative name on all platforms, not just Windows."""
    official_download[1]({assets.WHEEL_PREFIX + name: "bad"})
    with pytest.raises(assets.AssetError, match="unsafe path"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)
    assert not (tmp_path / "outside.txt").exists()


def test_wheel_case_collision_is_rejected(vendor_repo, tmp_path, fake_run, official_download):
    """Case-insensitive filesystem collisions must fail identically on Linux/Windows."""
    official_download[1]({assets.WHEEL_PREFIX + "assets/MAIN.js": "collision"})
    with pytest.raises(assets.AssetError, match="unsafe asset entry"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)


def test_zip_symlink_is_rejected(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Wheel symlinks cannot make a later extracted path leave the staging tree."""
    metadata, payload = official_download[1]()
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(payload)) as original, zipfile.ZipFile(buffer, "w") as target:
        for item in original.infolist():
            target.writestr(item, original.read(item))
        symlink = zipfile.ZipInfo(assets.WHEEL_PREFIX + "assets/link")
        symlink.create_system = 3
        symlink.external_attr = (stat.S_IFLNK | 0o777) << 16
        target.writestr(symlink, "../../outside")
    payload = buffer.getvalue()
    digest = hashlib.sha256(payload).hexdigest()
    metadata["urls"][0]["digests"]["sha256"] = digest
    metadata["urls"][0]["size"] = len(payload)
    monkeypatch.setattr(assets, "WHEEL_SHA256", digest)

    def download(url, maximum, hostname):
        """Provide a hash-valid archive containing a disallowed symlink."""
        return json.dumps(metadata).encode() if url == assets.PYPI_JSON_URL else payload

    monkeypatch.setattr(assets, "_download", download)
    with pytest.raises(assets.AssetError, match="unsafe asset entry"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)


def test_mutated_dashboard_is_not_silently_replaced(vendor_repo, tmp_path, fake_run, official_download):
    """Saved asset hashes make drift visible without an automatic repair download."""
    data = tmp_path / "state"
    dashboard = assets.prepare_assets(data, vendor_repo)["dashboard"]
    entry = dashboard / "assets/main.js"
    entry.write_text("user modified UI", encoding="utf-8")
    assert not assets.inspect_assets(data, vendor_repo)["checks"]["dashboard"]
    with pytest.raises(assets.AssetError, match="existing native dashboard differs"):
        assets.prepare_assets(data, vendor_repo)
    assert entry.read_text(encoding="utf-8") == "user modified UI"


@pytest.mark.parametrize("directory", ["runtime", "runtime/astrbot", "runtime/.venv", "runtime/pip-cache"])
def test_redirected_runtime_paths_are_rejected(vendor_repo, tmp_path, directory):
    """Do not write through symlinked runtime ownership boundaries."""
    data = tmp_path / "state"
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    path = data / directory
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.symlink_to(foreign, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks require Windows developer mode")
    with pytest.raises(assets.AssetError, match="symlinks or junctions"):
        assets.prepare_assets(data, vendor_repo)
    assert not list(foreign.iterdir())


@pytest.mark.parametrize("kind", ["symlink", "reparse"])
def test_existing_cache_link_attributes_are_rejected(vendor_repo, tmp_path, monkeypatch, kind):
    """Reject link/reparse metadata without requiring Windows symlink privileges."""
    data = tmp_path / "state"
    cache = data / "runtime" / "pip-cache"
    cache.mkdir(parents=True)
    sentinel = cache / "foreign"
    sentinel.write_bytes(b"do not repair")
    original_lstat = Path.lstat

    def lstat(path, *args, **kwargs):
        """Model an existing Windows reparse point without creating one on the host."""
        info = original_lstat(path, *args, **kwargs)
        if path == cache:
            return SimpleNamespace(st_mode=stat.S_IFLNK if kind == "symlink" else info.st_mode,
                                   st_file_attributes=stat.FILE_ATTRIBUTE_REPARSE_POINT if kind == "reparse" else 0)
        return info

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(assets.AssetError, match="symlinks or junctions"):
        assets.prepare_assets(data, vendor_repo)
    assert sentinel.read_bytes() == b"do not repair"
    assert sorted(path.name for path in cache.parent.iterdir()) == ["pip-cache"]


@pytest.mark.skipif(os.name != "nt", reason="Windows private-leaf ACL contract")
def test_existing_unsafe_cache_is_not_repaired(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Delegate cache privacy to the existing fail-closed helper before any pip call."""
    from plugin import local_runtime
    data = tmp_path / "state"
    cache = data / "runtime" / "pip-cache"
    cache.mkdir(parents=True)
    calls = []

    def refuse(path, *, create=True):
        """Model an unsafe preexisting DACL; do not grant or repair anything."""
        calls.append(path)
        raise RuntimeError("local_windows_path_insecure")

    monkeypatch.setattr(local_runtime, "ensure_private_windows_path", refuse)
    with pytest.raises(assets.AssetError, match="existing state was retained"):
        assets.prepare_assets(data, vendor_repo)
    assert calls == [cache]
    assert not any(label.startswith("dependency") for _, _, label in fake_run)
    assert not (cache.parent / assets.DEPENDENCY_MARKER).exists()


def test_global_or_incomplete_venv_is_not_reused(vendor_repo, tmp_path, fake_run, official_download):
    """Presence of python alone cannot authorize a pip install into foreign Python."""
    data = tmp_path / "state"
    venv = data / "runtime" / ".venv"
    python = venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"not an isolated interpreter")
    (venv / "pyvenv.cfg").write_text("include-system-site-packages = true\n", encoding="utf-8")
    with pytest.raises(assets.AssetError, match="not isolated"):
        assets.prepare_assets(data, vendor_repo)
    assert not fake_run


@pytest.mark.parametrize("version", [(3, 11), (3, 14)])
def test_only_supported_current_python_is_used(vendor_repo, tmp_path, monkeypatch, version):
    """Do not choose another interpreter, launcher, WSL, or Docker implicitly."""
    monkeypatch.setattr(assets.sys, "version_info", version)
    with pytest.raises(assets.AssetError, match="3.12 or 3.13"):
        assets.prepare_assets(tmp_path / "state", vendor_repo)
    assert not (tmp_path / "state").exists()


def test_repository_cannot_be_data_destination(vendor_repo):
    """Data must be external so provisioning can never mutate vendor or the checkout."""
    with pytest.raises(assets.AssetError, match="outside the repository"):
        assets.prepare_assets(vendor_repo / "runtime", vendor_repo)


def test_process_environment_and_log_are_secret_safe(tmp_path, monkeypatch):
    """Sanitize child environment and discard raw stdout/stderr even on failures."""
    runtime = tmp_path / "runtime"
    root = runtime / "astrbot"
    root.mkdir(parents=True)
    monkeypatch.setenv("PIP_TARGET", "global-location-secret")
    monkeypatch.setenv("PIP_CACHE_DIR", "global-cache-location-secret")
    monkeypatch.setenv("PYTHONPATH", "global-modules-secret")
    monkeypatch.setenv("PIP_INDEX_URL", "https://credential-secret@invalid.example/simple")
    captured = {}

    def run(arguments, **kwargs):
        """Record the exact subprocess boundary without executing package tools."""
        captured.update(kwargs)
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert not kwargs["shell"]
        return SimpleNamespace(returncode=1)

    def contained(arguments, cwd, environment, timeout):
        """Capture the Windows supervisor boundary without launching a process."""
        assert timeout == assets.DEFAULT_SETUP_TIMEOUT
        captured["env"] = environment
        captured["cwd"] = cwd
        return 1

    monkeypatch.setattr(assets.subprocess, "run", run)
    monkeypatch.setattr(assets, "run_setup_step", contained)
    with pytest.raises(assets.AssetError) as caught:
        assets._run(["fake-python", "-m", "pip"], root, "dependency installation")
    assert "secret" not in str(caught.value)
    assert "PIP_TARGET" not in captured["env"]
    assert "PIP_CACHE_DIR" not in captured["env"]
    assert "PIP_INDEX_URL" not in captured["env"]
    assert "PYTHONPATH" not in captured["env"]
    assert captured["env"]["PIP_CONFIG_FILE"] == os.devnull
    assert captured["env"]["PYTHONNOUSERSITE"] == "1"
    log = (runtime / "setup.log").read_text(encoding="utf-8")
    assert "secret" not in log and "exit 1" in log


def test_process_launch_exception_is_not_exposed(tmp_path, monkeypatch):
    """Even OS/subprocess exception strings must not appear in user-safe failures."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    def fail(*args, **kwargs):
        """Pretend a local tool emitted an error containing a credential."""
        raise OSError("raw-private-secret")

    monkeypatch.setattr(assets.subprocess, "run", fail)
    monkeypatch.setattr(assets, "run_setup_step", fail)
    with pytest.raises(assets.AssetError) as caught:
        assets._run([sys.executable], runtime, "venv creation")
    assert "raw-private-secret" not in str(caught.value)
    assert "raw-private-secret" not in (runtime / "setup.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("value", ["", "0", "86401", "-1", "1.5", "secret-value", "999999999"])
def test_setup_timeout_invalid_values_never_spawn(tmp_path, monkeypatch, value):
    """Validate the explicit deadline before tool launch or any safe-progress log write."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv(assets.SETUP_TIMEOUT_ENV, value)

    def forbidden(*args, **kwargs):
        """Refuse tool execution when deadline configuration is invalid."""
        raise AssertionError("unexpected spawn")

    monkeypatch.setattr(assets, "run_setup_step", forbidden)
    monkeypatch.setattr(assets.subprocess, "run", forbidden)
    with pytest.raises(assets.AssetError, match="1 to 86400"):
        assets._run([sys.executable], runtime, "dependency installation")
    assert not (runtime / "setup.log").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows contained installer contract")
@pytest.mark.parametrize("value", ["1", "86400"])
def test_setup_timeout_is_configurable_and_secret_safe(tmp_path, monkeypatch, value):
    """Only the supervisor receives the deadline; a timed-out step retains cache and no marker."""
    runtime = tmp_path / "runtime"
    cache = runtime / "pip-cache"
    cache.mkdir(parents=True)
    sentinel = cache / "retained"
    sentinel.write_bytes(b"already cached")
    monkeypatch.setenv(assets.SETUP_TIMEOUT_ENV, value)

    def timed_out(arguments, cwd, environment, timeout):
        """Represent an already-confirmed owned process-tree cleanup."""
        assert timeout == int(value)
        raise subprocess.TimeoutExpired("private-secret-command", timeout, output=b"secret", stderr=b"secret")

    monkeypatch.setattr(assets, "run_setup_step", timed_out)
    with pytest.raises(assets.AssetError, match="explicitly retry") as caught:
        assets._run([sys.executable], runtime, "dependency installation")
    assert "secret" not in str(caught.value)
    assert sentinel.read_bytes() == b"already cached"
    assert not (runtime / assets.DEPENDENCY_MARKER).exists()
    assert not (runtime / assets.SETUP_REVIEW_MARKER).exists()
    log = (runtime / "setup.log").read_text(encoding="utf-8")
    assert "timed out" in log and "secret" not in log


@pytest.mark.skipif(os.name != "nt", reason="Windows contained installer contract")
def test_uncertain_setup_cleanup_blocks_future_prepare(vendor_repo, tmp_path, monkeypatch):
    """Do not overlap another installer after job-empty status could not be proven."""
    data = tmp_path / "state"
    runtime = data / "runtime"
    runtime.mkdir(parents=True)
    calls = []

    def uncertain(*args, **kwargs):
        """Model unconfirmed cleanup without launching or leaving any actual process."""
        calls.append(True)
        raise assets.SetupCleanupError("raw-private-secret")

    monkeypatch.setattr(assets, "run_setup_step", uncertain)
    with pytest.raises(assets.AssetError, match="retry is blocked") as caught:
        assets._run([sys.executable], runtime, "dependency installation")
    assert "secret" not in str(caught.value)
    assert json.loads((runtime / assets.SETUP_REVIEW_MARKER).read_text(encoding="utf-8")) == {
        "schema": 1, "review_required": True}
    assert not assets.inspect_assets(data, vendor_repo)["checks"]["setup_cleanup"]
    with pytest.raises(assets.AssetError, match="ownership review"):
        assets.prepare_assets(data, vendor_repo)
    with pytest.raises(assets.AssetError, match="ownership review"):
        assets._run([sys.executable], runtime, "dependency installation")
    assert len(calls) == 1
    assert not (runtime / "astrbot").exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows contained installer contract")
def test_review_marker_failure_prevents_any_spawn(tmp_path, monkeypatch):
    """A full disk or permission error arming the guard must fail before child creation."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()

    def failed_write(*args, **kwargs):
        """Model a failure to durably arm without needing real filesystem damage."""
        raise OSError("raw-private-secret")

    def forbidden(*args, **kwargs):
        """No subprocess may exist without the durable retry guard."""
        raise AssertionError("unexpected spawn")

    monkeypatch.setattr(assets, "_write_json", failed_write)
    monkeypatch.setattr(assets, "run_setup_step", forbidden)
    with pytest.raises(assets.AssetError) as caught:
        assets._run([sys.executable], runtime, "dependency installation")
    assert "secret" not in str(caught.value)
    assert not (runtime / assets.DEPENDENCY_MARKER).exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows contained installer contract")
def test_review_marker_survives_unconfirmed_cleanup_and_blocks_success(tmp_path, monkeypatch):
    """A failed guard removal remains a failed operation, not a misleading success."""
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    guard = runtime / assets.SETUP_REVIEW_MARKER
    original_unlink = Path.unlink

    def retain_guard(path, *args, **kwargs):
        """Leave only the exact guard in place; do not change unrelated cleanup."""
        if path == guard:
            raise OSError("guard_removal_failed")
        return original_unlink(path, *args, **kwargs)

    def completed(*args, **kwargs):
        """Only a simulated fully completed step can reach guard removal."""
        assert guard.exists()
        return 0

    monkeypatch.setattr(assets, "run_setup_step", completed)
    monkeypatch.setattr(Path, "unlink", retain_guard)
    with pytest.raises(OSError, match="guard_removal_failed"):
        assets._run([sys.executable], runtime, "dependency installation")
    assert guard.exists() and not (runtime / assets.DEPENDENCY_MARKER).exists()
    assert "exit 0" not in (runtime / "setup.log").read_text(encoding="utf-8")


@pytest.mark.parametrize("url", ["http://files.pythonhosted.org/a.whl", "https://evil.example/a.whl", "https://files.pythonhosted.org.evil.example/a.whl", "https://user:secret@files.pythonhosted.org/a.whl", "https://files.pythonhosted.org:444/a.whl", "https://files.pythonhosted.org/a.whl?override=secret"])
def test_download_rejects_unofficial_urls_before_request(url, monkeypatch):
    """Only exact official HTTPS hosts are eligible, with no credential/port tricks."""
    def forbidden(*args, **kwargs):
        """Ensure a rejected URL is never opened."""
        raise AssertionError("opened untrusted URL")

    monkeypatch.setattr(assets.urllib.request, "build_opener", forbidden)
    with pytest.raises(assets.AssetError, match="URL is invalid"):
        assets._download(url, 100, "files.pythonhosted.org")


def test_download_rejects_final_host_change(monkeypatch):
    """Guard final response URLs even when custom handlers/proxies bypass redirect code."""
    class Response(io.BytesIO):
        """Model a response whose effective URL violates the host allowlist."""
        headers = {}

        def geturl(self):
            """Expose a disallowed effective URL."""
            return "https://untrusted.example/data"

    opener = SimpleNamespace(open=lambda *args, **kwargs: Response(b"secret"))
    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *args: opener)
    with pytest.raises(assets.AssetError, match="unexpectedly changed"):
        assets._download("https://files.pythonhosted.org/a.whl", 100, "files.pythonhosted.org")


@pytest.mark.parametrize("header", [None, "101", "-1", "invalid"])
def test_download_size_cap_covers_stream_and_header(monkeypatch, header):
    """Enforce the actual bytes cap, even when Content-Length is absent or false."""
    url = "https://files.pythonhosted.org/a.whl"

    class Response(io.BytesIO):
        """Provide an oversized stream with optional misleading response metadata."""
        headers = {} if header is None else {"Content-Length": header}

        def geturl(self):
            """Return the expected unchanged official URL."""
            return url

    opener = SimpleNamespace(open=lambda *args, **kwargs: Response(b"x" * 101))
    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *args: opener)
    with pytest.raises(assets.AssetError, match="size limit"):
        assets._download(url, 100, "files.pythonhosted.org")


def test_redirect_handler_refuses_before_following():
    """A redirect must be denied before urllib contacts its new destination."""
    with pytest.raises(assets.AssetError, match="redirected"):
        assets._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://evil.example/")


def test_missing_vendor_and_raw_network_failures_are_safe(tmp_path, monkeypatch):
    """Filesystem and transport exceptions are never serialized into caller errors."""
    with pytest.raises(assets.AssetError) as caught:
        assets.prepare_assets(tmp_path / "state", tmp_path / "missing-repository")
    assert "missing-repository" not in str(caught.value)

    def fail(*args, **kwargs):
        """Simulate urllib revealing an authenticated proxy URL in its error."""
        raise urllib.error.URLError("https://proxy-private-secret@proxy.invalid")

    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=fail))
    with pytest.raises(assets.AssetError) as caught:
        assets._download(assets.PYPI_JSON_URL, assets.MAX_METADATA_BYTES, "pypi.org")
    assert "proxy-private-secret" not in str(caught.value)


@pytest.mark.parametrize("configuration", [
    "include-system-site-packages = false\ninclude-system-site-packages = true\n",
    "include-system-site-packages = true\ninclude-system-site-packages = false\n",
    "include-system-site-packages = false\nINCLUDE-SYSTEM-SITE-PACKAGES = false\n",
    "include-system-site-packages = false\nversion = 3.13.12\n",
])
def test_duplicate_venv_fields_are_never_accepted(tmp_path, configuration):
    """Any repeated critical field is ambiguous, including equal-value duplicates."""
    python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"isolated fixture")
    (python.parent.parent / "pyvenv.cfg").write_text(
        configuration + "version = " + ".".join(map(str, sys.version_info[:3])) + "\n", encoding="utf-8")
    assert not assets._venv_valid(python)


@pytest.mark.parametrize("version", ["3.13.", "3.13.12garbage", "3.13.12.1", "3.13.12 #comment", "3.12.1", "3.14.0"])
def test_venv_version_requires_complete_matching_release(tmp_path, monkeypatch, version):
    """A matching regex prefix cannot authorize malformed or different-minor configs."""
    monkeypatch.setattr(assets.sys, "version_info", (3, 13, 12))
    python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    (python.parent.parent / "pyvenv.cfg").write_text(
        "include-system-site-packages = false\nversion = " + version + "\n", encoding="utf-8")
    assert not assets._venv_valid(python)


def test_venv_unique_isolation_fields_allow_ordinary_spacing(tmp_path):
    """Keep normal venv-generated configuration compatible without weakening duplicates."""
    python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    (python.parent.parent / "pyvenv.cfg").write_text(
        "home = safe fixture\n Include-System-Site-Packages = FALSE \n version = "
        + ".".join(map(str, sys.version_info[:3])) + "\n", encoding="utf-8")
    assert assets._venv_valid(python)


@pytest.mark.parametrize("value", ["", "0", "3601", "-1", "1.5", "secret", "9999999999"])
def test_download_budget_invalid_never_opens(monkeypatch, value):
    """Validate explicit budget before making even an allowlisted connection."""
    monkeypatch.setenv(assets.DOWNLOAD_TIMEOUT_ENV, value)

    def forbidden(*args, **kwargs):
        """Any opener construction would violate early configuration validation."""
        raise AssertionError("unexpected open")

    monkeypatch.setattr(assets.urllib.request, "build_opener", forbidden)
    with pytest.raises(assets.AssetError, match="1 to 3600"):
        assets._download(assets.PYPI_JSON_URL, 100, "pypi.org")


@pytest.mark.parametrize("framing", ["length", "chunked", "header", "chunk-header"])
def test_actual_http_response_drip_shares_single_budget(monkeypatch, framing):
    """Real http.client read/header/chunk loops cannot hide slow raw receives."""
    elapsed = [0.0]
    timeouts = []
    streams = []
    url = "https://files.pythonhosted.org/a.whl"
    if framing == "length":
        fragments = [b"HTTP/1.1 200 OK\r\nContent-Length: 4\r\n\r\n", b"a", b"b", b"c", b"d"]
    elif framing == "chunked":
        fragments = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n",
                     b"4\r\n", b"a", b"b", b"c", b"d", b"\r\n0\r\n\r\n"]
    elif framing == "chunk-header":
        fragments = [b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n", b"0", b"0", b"0", b"0", b"4\r\n"]
    else:
        fragments = [b"HTTP/1.1 200 OK\r\nX-Slow: ", b"a", b"b", b"c", b"d", b"\r\n\r\n"]
    pieces = deque(fragments)

    class Drip(io.RawIOBase):
        """Model successful socket receives that each finish before the idle timeout."""

        def readable(self):
            """Allow the genuine HTTP parser's BufferedReader to consume fragments."""
            return True

        def readinto(self, buffer):
            """Advance virtual time while honoring the current dynamic socket timeout."""
            if not pieces:
                return 0
            wait = 0.3
            if timeouts[-1] < wait:
                elapsed[0] += timeouts[-1]
                raise TimeoutError("private-secret timeout")
            elapsed[0] += wait
            fragment = pieces.popleft()
            buffer[:len(fragment)] = fragment
            return len(fragment)

    class Socket:
        """Expose only the ordinary socket APIs needed by the guarded HTTP reader."""

        def settimeout(self, timeout):
            """Capture shrinking timeouts rather than forgiving the last received byte."""
            timeouts.append(timeout)

        def makefile(self, mode, buffering):
            """Require unbuffered raw access so every underlying receive is guarded."""
            assert buffering == 0
            raw = Drip()
            streams.append(raw)
            return raw

    def open_response(request, timeout):
        """Build the same response factory used by the real HTTPS connection offline."""
        assert timeout == 1
        connection = assets._DeadlineHTTPSConnection("files.pythonhosted.org", deadline=1)
        response = connection._response(Socket())
        try:
            response.begin()
        except BaseException:
            response.close()
            raise
        response.url = url
        return response

    monkeypatch.setenv(assets.DOWNLOAD_TIMEOUT_ENV, "1")
    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_response))
    with pytest.raises(assets.AssetError) as caught:
        assets._download(url, 100, "files.pythonhosted.org")
    assert "private-secret" not in str(caught.value)
    assert elapsed[0] == pytest.approx(1)
    assert timeouts == sorted(timeouts, reverse=True)
    assert all(stream.closed for stream in streams)
    assert pieces  # The over-budget body/line was not drained after failure.


def test_download_budgets_reset_only_between_distinct_requests(monkeypatch):
    """Metadata and wheel each get a finite budget, never an unbounded aggregate retry."""
    elapsed = [0.0]
    deadlines = []
    url = assets.PYPI_JSON_URL

    class Response(io.BytesIO):
        """Return a small complete response without any actual transport."""
        headers = {}

        def geturl(self):
            """Keep endpoint allowlisting active in the normal download path."""
            return url

    def opener(*handlers):
        """Inspect only per-opener deadlines; never mutate the global urllib opener."""
        deadline = next(handler.deadline for handler in handlers if isinstance(handler, assets._DeadlineHTTPSHandler))
        deadlines.append(deadline)
        return SimpleNamespace(open=lambda *args, **kwargs: Response(b"ok"))

    monkeypatch.setenv(assets.DOWNLOAD_TIMEOUT_ENV, "2")
    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.urllib.request, "build_opener", opener)
    before = assets.urllib.request._opener
    assert assets._download(url, 100, "pypi.org") == b"ok"
    elapsed[0] = 5
    assert assets._download(url, 100, "pypi.org") == b"ok"
    assert deadlines == [2, 7]
    assert assets.urllib.request._opener is before


def test_download_rejects_budget_consumed_during_open(monkeypatch):
    """Header/connect time is not forgiven when the opener finally returns a body."""
    elapsed = [0.0]
    response = io.BytesIO(b"should not be consumed")

    def open_response(*args, **kwargs):
        """Model an OS resolver returning after its uninterruptible budget boundary."""
        elapsed[0] = 3
        return response

    monkeypatch.setenv(assets.DOWNLOAD_TIMEOUT_ENV, "2")
    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.urllib.request, "build_opener", lambda *args: SimpleNamespace(open=open_response))
    with pytest.raises(assets.AssetError, match="total time budget"):
        assets._download(assets.PYPI_JSON_URL, 100, "pypi.org")
    assert response.closed


def test_tcp_candidates_consume_one_deadline_and_close_failures(monkeypatch):
    """DNS plus multiple address attempts cannot each receive a fresh full budget."""
    elapsed = [0.0]
    sockets = []

    class Socket:
        """Provide deterministic offline connect latency without opening real sockets."""

        def __init__(self):
            self.timeouts = []
            self.closed = False
            sockets.append(self)

        def settimeout(self, timeout):
            """Record per-attempt remaining budgets before and after connection."""
            self.timeouts.append(timeout)

        def connect(self, target):
            """Fail the first candidate after spending half of the shared budget."""
            elapsed[0] += 1
            if len(sockets) == 1:
                raise OSError("private-secret connect")

        def close(self):
            """Record closure of only sockets owned by the failed attempt."""
            self.closed = True

    def resolve(*args):
        """Charge resolver latency while returning ordinary address candidates."""
        elapsed[0] += 0.5
        return [(2, 1, 6, "", ("fixture", 443))] * 2

    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(assets.socket, "socket", lambda *args: Socket())
    connection = assets._DeadlineHTTPSConnection("files.pythonhosted.org", deadline=3)
    assert connection._connect_socket(("fixture", 443), 3, None) is sockets[1]
    assert sockets[0].closed and not sockets[1].closed
    assert sockets[0].timeouts == [2.5]
    assert sockets[1].timeouts == [1.5, 0.5]


def test_tcp_socket_creation_failure_falls_back_without_resetting_deadline(monkeypatch):
    """A disabled first address family cannot hide a reachable later candidate."""
    elapsed = [0.0]
    attempted = []
    timeouts = []
    target = []

    class Socket:
        """Model only the successfully constructed IPv4 socket without real traffic."""

        def settimeout(self, timeout):
            """Record the remaining budget consumed by the failed construction."""
            timeouts.append(timeout)

        def bind(self, address):
            """Preserve source-address behavior across address-family fallback."""
            assert address == ("source", 0)

        def connect(self, address):
            """Connect only to the second approved resolver candidate."""
            target.append(address)

        def close(self):
            """The successful candidate must stay open for TLS ownership."""
            pytest.fail("closed successful connection")

    owned = Socket()

    def create(family, kind, protocol):
        """Simulate Windows WSAEAFNOSUPPORT before any socket handle exists."""
        attempted.append(family)
        if len(attempted) == 1:
            elapsed[0] += 0.5
            raise OSError(10047, "unsupported address family")
        return owned

    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.socket, "getaddrinfo", lambda *_args: [
        (23, 1, 6, "", ("ipv6-fixture", 443)), (2, 1, 6, "", ("ipv4-fixture", 443))])
    monkeypatch.setattr(assets.socket, "socket", create)
    connection = assets._DeadlineHTTPSConnection("files.pythonhosted.org", deadline=2)
    assert connection._connect_socket(("fixture", 443), 2, ("source", 0)) is owned
    assert attempted == [23, 2] and target == [("ipv4-fixture", 443)]
    assert timeouts == [1.5, 1.5]


def test_tcp_all_constructor_failures_report_last_error_without_fake_close(monkeypatch):
    """No created socket means nothing may be closed when every family is unavailable."""
    attempted = []

    def create(family, *_args):
        """Fail both families with distinct errors so last-candidate reporting is clear."""
        attempted.append(family)
        raise OSError(10047 if family == 23 else 10050, "offline socket construction")

    monkeypatch.setattr(assets.time, "monotonic", lambda: 0)
    monkeypatch.setattr(assets.socket, "getaddrinfo", lambda *_args: [
        (23, 1, 6, "", ("ipv6-fixture", 443)), (2, 1, 6, "", ("ipv4-fixture", 443))])
    monkeypatch.setattr(assets.socket, "socket", create)
    connection = assets._DeadlineHTTPSConnection("files.pythonhosted.org", deadline=2)
    with pytest.raises(OSError) as failure:
        connection._connect_socket(("fixture", 443), 2, None)
    assert attempted == [23, 2] and failure.value.errno == 10050


def test_tcp_rejects_late_dns_without_connecting(monkeypatch):
    """Document the OS-owned resolver exception but never continue its late result."""
    elapsed = [0.0]

    def resolve(*args):
        """Represent an OS resolver which cannot be interrupted by socket timeouts."""
        elapsed[0] = 5
        return [(2, 1, 6, "", ("fixture", 443))]

    def forbidden(*args):
        """No TCP socket may open after the resolver has exhausted the budget."""
        raise AssertionError("late socket creation")

    monkeypatch.setattr(assets.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(assets.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(assets.socket, "socket", forbidden)
    connection = assets._DeadlineHTTPSConnection("files.pythonhosted.org", deadline=3)
    with pytest.raises(assets.AssetError, match="total time budget"):
        connection._connect_socket(("fixture", 443), 3, None)


@pytest.fixture
def metadata_venv(tmp_path):
    """Create no-pip stdlib venv and copy only the existing packaging parser fixture."""
    environment = tmp_path / "metadata-venv"
    venv.EnvBuilder(with_pip=False).create(environment)
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    site = environment / ("Lib/site-packages" if os.name == "nt" else
                          "lib/python%d.%d/site-packages" % sys.version_info[:2])
    spec = importlib.util.find_spec("pip._vendor.packaging")
    assert spec and spec.origin
    packaging = site / "pip" / "_vendor" / "packaging"
    packaging.parent.mkdir(parents=True)
    shutil.copytree(Path(spec.origin).parent, packaging, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    return python, site


def _distribution(site, name, version, requirements=()):
    """Write only metadata fixtures, never install an actual runtime dependency."""
    directory = site / (name.replace("-", "_") + "-" + version + ".dist-info")
    directory.mkdir()
    (directory / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: " + name + "\nVersion: " + version + "\n"
        + "".join("Requires-Dist: " + item + "\n" for item in requirements), encoding="utf-8")
    package = site / name.replace("-", "_")
    package.mkdir(exist_ok=True)
    (package / "__init__.py").write_text("# Metadata-only fixture; never imported.\n", encoding="utf-8")
    (directory / "RECORD").write_text(package.name + "/__init__.py,,\n", encoding="utf-8")
    return directory


def test_real_metadata_probe_handles_markers_extras_versions_and_missing(metadata_venv):
    """Actual venv Python checks the entire applicable closure, not just import names."""
    python, site = metadata_venv
    _distribution(site, "root", "2.0", ["child>=3; extra == 'async'", "ignored; python_version < '3.0'"])
    _distribution(site, "child", "3.1", ["leaf~=1.0"])
    leaf = _distribution(site, "leaf", "1.2")
    assert assets._dependencies_valid(python, ["root[async]>=2", "missing; python_version < '3.0'"])
    assert not assets._dependencies_valid(python, ["root[async]>2"])
    shutil.rmtree(leaf)
    assert not assets._dependencies_valid(python, ["root[async]>=2"])
    assert assets._dependencies_valid(python, ["root>=2"])


@pytest.mark.parametrize("failure", ["empty", "duplicate", "malformed", "parser-missing", "direct-url", "record-missing", "package-missing"])
def test_real_metadata_probe_rejects_broken_environment(metadata_venv, failure):
    """Missing metadata/parser, duplicate installs and ambiguous requirement sources fail closed."""
    python, site = metadata_venv
    if failure != "empty":
        info = _distribution(site, "root", "1.0", ["bad requirement ; invalid === marker"] if failure == "malformed" else [])
        if failure == "record-missing":
            (info / "RECORD").unlink()
        elif failure == "package-missing":
            shutil.rmtree(site / "root")
    if failure == "duplicate":
        _distribution(site, "root", "2.0")
    if failure == "parser-missing":
        shutil.rmtree(site / "pip")
    requirement = "root @ https://private-secret.invalid/root.whl" if failure == "direct-url" else "root"
    assert not assets._dependencies_valid(python, [requirement])


def test_real_probe_does_not_load_pth_customizers_or_write_bytecode(metadata_venv, monkeypatch):
    """The read-only probe ignores global/python-path injection and executable site hooks."""
    python, site = metadata_venv
    _distribution(site, "root", "1.0")
    (site / "sitecustomize.py").write_text("raise SystemExit(33)\n", encoding="utf-8")
    (site / "evil.pth").write_text("import sys; sys.exit(34)\n", encoding="utf-8")
    monkeypatch.setenv("PYTHONPATH", str(site))
    before = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in site.rglob("*") if path.is_file()}
    assert assets._dependencies_valid(python, ["root"])
    after = {str(path): (path.read_bytes(), path.stat().st_mtime_ns) for path in site.rglob("*") if path.is_file()}
    assert before == after


def test_metadata_probe_has_fixed_finite_timeout_and_secret_safe_failure(tmp_path, monkeypatch):
    """A stalled trusted metadata child is reaped by subprocess, never reported ready."""
    calls = []

    def timeout(arguments, **kwargs):
        """Capture a bounded owner timeout without starting or orphaning any process."""
        calls.append((arguments, kwargs))
        raise subprocess.TimeoutExpired("private-secret", kwargs["timeout"])

    monkeypatch.setattr(assets.subprocess, "run", timeout)
    monkeypatch.setattr(assets, "run_setup_step", timeout)
    assert not assets._dependencies_valid(tmp_path / ".venv/Scripts/python.exe", ["root"])
    arguments, options = calls[0]
    assert arguments[1:5] == ["-I", "-S", "-B", "-c"]
    assert options["timeout"] == 20
    if os.name != "nt":
        assert not options["shell"]
        assert options["stdout"] is subprocess.DEVNULL and options["stderr"] is subprocess.DEVNULL


@pytest.mark.skipif(os.name != "nt", reason="Windows venv redirector process containment")
def test_metadata_probe_cleanup_uncertainty_never_starts_repair(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Unknown probe teardown must propagate failure, never become an install trigger."""
    data = tmp_path / "state"
    assets.prepare_assets(data, vendor_repo)
    monkeypatch.setattr(assets, "_dependencies_valid", _REAL_DEPENDENCY_PROBE)
    original_calls = len(fake_run)

    def uncertain(*args, **kwargs):
        """Model supervisor uncertainty without leaving any real child process."""
        raise assets.SetupCleanupError("private-secret")

    def forbidden(*args, **kwargs):
        """Neither pip repair nor download is safe while ownership remains unknown."""
        raise AssertionError("unexpected install/download")

    monkeypatch.setattr(assets, "run_setup_step", uncertain)
    monkeypatch.setattr(assets, "_run", forbidden)
    monkeypatch.setattr(assets, "_download", forbidden)
    marker = data / "runtime" / assets.DEPENDENCY_MARKER
    before = marker.read_bytes()
    with pytest.raises(assets.AssetError, match="cleanup could not be confirmed") as caught:
        assets.inspect_assets(data, vendor_repo)
    assert "private-secret" not in str(caught.value)
    guard = data / "runtime" / assets.SETUP_REVIEW_MARKER
    assert json.loads(guard.read_text(encoding="utf-8")) == {"schema": 1, "review_required": True}
    report = assets.inspect_assets(data, vendor_repo)
    assert not report["checks"]["setup_cleanup"] and not report["checks"]["dependencies"]
    with pytest.raises(assets.AssetError, match="ownership review"):
        assets.prepare_assets(data, vendor_repo)
    assert marker.read_bytes() == before and len(fake_run) == original_calls
    with pytest.raises(assets.AssetError, match="ownership review"):
        assets._dependencies_valid(assets._paths(data, vendor_repo)["python"], ["root"])


@pytest.mark.parametrize("failure", ["replaced", "missing", "incompatible"])
def test_stale_marker_is_checked_and_dependencies_repaired(vendor_repo, tmp_path, fake_run, official_download, monkeypatch, failure):
    """A same-minor empty/reduced venv can never be ready just because its marker survived."""
    data = tmp_path / "state"
    paths = assets.prepare_assets(data, vendor_repo)
    marker = data / "runtime" / assets.DEPENDENCY_MARKER
    states = [False]
    original_run = assets._run
    if failure == "replaced":
        environment = paths["python"].parent.parent
        shutil.rmtree(environment)
        original_run([sys.executable, "-m", "venv", str(environment)], environment.parent, "venv creation")

    def valid(python, dependencies):
        """Model actual package loss/version mismatch until a complete install succeeds."""
        assert python == paths["python"]
        assert dependencies == assets._source(vendor_repo)[2]
        return states[0]

    def repair(arguments, cwd, label):
        """The old completion must be invalidated before any repairing pip invocation."""
        if label == "dependency installation":
            assert not marker.exists()
            states[0] = True
        original_run(arguments, cwd, label)

    monkeypatch.setattr(assets, "_dependencies_valid", valid)
    assert not assets.inspect_assets(data, vendor_repo)["checks"]["dependencies"]
    monkeypatch.setattr(assets, "_run", repair)
    count = len(fake_run)
    assert assets.prepare_assets(data, vendor_repo) == paths
    assert [label for _, _, label in fake_run[count:]] == ["dependency installation", "dependency verification"]
    assert assets.inspect_assets(data, vendor_repo)["ready"]


def test_actual_empty_same_minor_venv_invalidates_existing_completion(vendor_repo, tmp_path, official_download, monkeypatch):
    """A real stdlib empty venv replacing a successful same-minor install is not ready."""
    data = tmp_path / "state"
    actual_probe = assets._dependencies_valid
    completed = [True]
    calls = []

    def setup(arguments, cwd, label):
        """Only create a genuine no-pip fixture venv; never run installation tools."""
        calls.append(label)
        if label == "venv creation":
            venv.EnvBuilder(with_pip=False).create(Path(arguments[-1]))
        elif label == "dependency installation" and not completed[0]:
            raise assets.AssetError("fixture repair intentionally stopped before pip")

    def valid(python, dependencies):
        """First model the old completed install, then inspect the real empty replacement."""
        return completed[0] or actual_probe(python, dependencies)

    monkeypatch.setattr(assets, "_run", setup)
    monkeypatch.setattr(assets, "_dependencies_valid", valid)
    if os.name == "nt":
        from plugin import local_runtime

        def private_cache(path, *, create=True):
            """Create fixture cache without changing any real Windows ACL."""
            path.mkdir(exist_ok=True)
            return path

        monkeypatch.setattr(local_runtime, "ensure_private_windows_path", private_cache)
    paths = assets.prepare_assets(data, vendor_repo)
    environment = paths["python"].parent.parent
    shutil.rmtree(environment)
    venv.EnvBuilder(with_pip=False).create(environment)
    completed[0] = False
    assert assets._venv_valid(paths["python"])
    assert not assets.inspect_assets(data, vendor_repo)["checks"]["dependencies"]
    with pytest.raises(assets.AssetError, match="intentionally stopped"):
        assets.prepare_assets(data, vendor_repo)
    assert calls[-1] == "dependency installation"
    assert not (data / "runtime" / assets.DEPENDENCY_MARKER).exists()


def test_legacy_marker_is_readable_then_upgrades_without_pip(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Existing healthy schema-1 installations remain usable and need no dependency reinstall."""
    data = tmp_path / "state"
    paths = assets.prepare_assets(data, vendor_repo)
    marker = data / "runtime" / assets.DEPENDENCY_MARKER
    legacy = json.loads(marker.read_text(encoding="utf-8"))
    legacy.pop("verification")
    legacy["schema"] = 1
    assets._write_json(marker, legacy)
    original = marker.read_bytes()
    assert assets.inspect_assets(data, vendor_repo)["ready"]
    assert marker.read_bytes() == original

    def forbidden(*args, **kwargs):
        """A healthy legacy install must not invoke pip or fetch dashboard assets."""
        raise AssertionError("unexpected installation")

    monkeypatch.setattr(assets, "_run", forbidden)
    monkeypatch.setattr(assets, "_download", forbidden)
    assert assets.prepare_assets(data, vendor_repo) == paths
    assert json.loads(marker.read_text(encoding="utf-8"))["schema"] == 2
    assert assets.inspect_assets(data, vendor_repo)["ready"]


def test_failed_dependency_repair_never_retains_old_completion(vendor_repo, tmp_path, fake_run, official_download, monkeypatch):
    """Even a successful pip exit cannot publish completion if actual metadata is incomplete."""
    data = tmp_path / "state"
    assets.prepare_assets(data, vendor_repo)
    monkeypatch.setattr(assets, "_dependencies_valid", lambda *args: False)
    with pytest.raises(assets.AssetError, match="metadata verification"):
        assets.prepare_assets(data, vendor_repo)
    assert not (data / "runtime" / assets.DEPENDENCY_MARKER).exists()
    assert not assets.inspect_assets(data, vendor_repo)["ready"]


def test_module_and_tests_parse_and_have_documented_functions():
    """Run explicit syntax/docstring checks on only the two authorized new files."""
    for path in (Path(assets.__file__), Path(__file__)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "__init__":
                assert ast.get_docstring(node), f"Missing function docstring: {node.name}"
