"""Offline native-asset tests: no real pip installs, models, servers, or vendor edits."""

import ast
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error
import zipfile

import pytest

from tools import native_assets as assets


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
    assert report["checks"] == dict.fromkeys(("source", "venv", "dependencies", "dashboard"), False)
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
    """A partial pip step must not trick doctor or future setup into reporting ready."""
    data = tmp_path / "state"
    successful = assets._run

    def fail(arguments, cwd, label):
        """Simulate package tooling with a secret-bearing raw exception."""
        if label == "dependency installation":
            raise RuntimeError("https://private-token-secret@untrusted.invalid/package")
        successful(arguments, cwd, label)

    monkeypatch.setattr(assets, "_run", fail)
    with pytest.raises(assets.AssetError) as caught:
        assets.prepare_assets(data, vendor_repo)
    assert "private-token-secret" not in str(caught.value)
    assert not (data / "runtime" / assets.DEPENDENCY_MARKER).exists()
    assert not assets.inspect_assets(data, vendor_repo)["ready"]
    monkeypatch.setattr(assets, "_run", successful)
    assert assets.prepare_assets(data, vendor_repo)
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


@pytest.mark.parametrize("directory", ["runtime", "runtime/astrbot", "runtime/.venv"])
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

    monkeypatch.setattr(assets.subprocess, "run", run)
    with pytest.raises(assets.AssetError) as caught:
        assets._run(["fake-python", "-m", "pip"], root, "dependency installation")
    assert "secret" not in str(caught.value)
    assert "PIP_TARGET" not in captured["env"]
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
    with pytest.raises(assets.AssetError) as caught:
        assets._run([sys.executable], runtime, "venv creation")
    assert "raw-private-secret" not in str(caught.value)
    assert "raw-private-secret" not in (runtime / "setup.log").read_text(encoding="utf-8")


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


def test_module_and_tests_parse_and_have_documented_functions():
    """Run explicit syntax/docstring checks on only the two authorized new files."""
    for path in (Path(assets.__file__), Path(__file__)):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name != "__init__":
                assert ast.get_docstring(node), f"Missing function docstring: {node.name}"
