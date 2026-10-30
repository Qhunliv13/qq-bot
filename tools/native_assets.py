"""Provision the pinned native runtime without modifying the checkout or global Python.

The vendored ``pyproject.toml`` is the only core dependency list. The official
AstrBot 4.28.1 wheel is used *only* for prebuilt WebUI files, never for Python
code. Its recorded SHA256 pin must agree with the exact-version PyPI JSON over
HTTPS and with the downloaded bytes before extraction. This is transport-backed integrity, not an author
signature or an independently authenticated release signature.

Public entry points return pathlib paths. ``inspect_assets`` is strictly
read-only/offline; ``prepare_assets`` installs only into data/runtime/.venv.
Persistent AstrBot state belongs in the returned astrbot_root / "data".
"""

from __future__ import annotations

import hashlib
from html.parser import HTMLParser
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tomllib
from urllib.parse import unquote, urlsplit
import urllib.request
import zipfile

# Both the Python core and bundled WebUI must match the committed upstream pin.
ASTRBOT_VERSION = "4.28.1"
PYPI_JSON_URL = "https://pypi.org/pypi/AstrBot/4.28.1/json"
WHEEL_FILENAME = "astrbot-4.28.1-py3-none-any.whl"
# Independently recorded from the exact-version official PyPI JSON on 2026-10-30.
# Require both this immutable pin and the live HTTPS metadata digest to agree.
WHEEL_SHA256 = "37eff8bcd33d25c39f9b2dd0c23f7354ab528c4ca594fa59a43ea0ce7d72bebf"
WHEEL_PREFIX = "astrbot/dashboard/dist/"
# Bound compressed downloads and expanded WebUI independently to limit disk use.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_METADATA_BYTES = 2 * 1024 * 1024
MAX_EXPANDED_BYTES = 300 * 1024 * 1024
MAX_ASSET_FILES = 20000
SOURCE_MARKER = ".native-source.json"
DEPENDENCY_MARKER = "dependencies.json"
DASHBOARD_MARKER = ".native-dashboard.json"
_EXCLUDED_DIRS = {".git", "tests", "node_modules", "__pycache__", ".venv", ".pytest_cache", ".ruff_cache"}


class AssetError(RuntimeError):
    """Expose only fixed, non-secret setup diagnostics to the native caller."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects before connecting to any destination outside the pin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        """Reject even same-host redirects; the release endpoints need none."""
        raise AssetError("The official asset endpoint unexpectedly redirected.")


class _Entries(HTMLParser):
    """Collect local entry references without importing any runtime dependency."""

    def __init__(self):
        super().__init__()
        self.references = []

    def handle_starttag(self, tag, attrs):
        """Collect entry scripts and styles, ignoring normal hyperlinks."""
        for key, value in attrs:
            if value and ((tag == "script" and key == "src") or (tag == "link" and key == "href")):
                self.references.append(value)


def _paths(data, repo):
    """Resolve owned paths and reject checkout writes or redirected runtime roots."""
    repo = Path(repo).expanduser().resolve()
    data = Path(data).expanduser().resolve()
    if data == repo or repo in data.parents or data in repo.parents:
        raise AssetError("Native state must be outside the repository.")
    runtime = data / "runtime"
    root = runtime / "astrbot"
    venv = runtime / ".venv"
    dashboard = root / "astrbot" / "dashboard" / "dist"
    # Existing junctions/symlinks must not turn an owned write into a foreign one.
    for target in (runtime, root, venv, root / "astrbot", root / "astrbot" / "dashboard", dashboard):
        if target.is_symlink() or target.resolve() != target:
            raise AssetError("Native asset paths must not be symlinks or junctions.")
    return {"python": venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            "astrbot_root": root, "dashboard": dashboard}


def _source(repo, *, source_root=None):
    """Hash a vendor/runtime snapshot with one exclusion policy and dependency list."""
    vendor = source_root or Path(repo).expanduser().resolve() / "vendor" / "astrbot"
    project = tomllib.loads((vendor / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    dependencies = project.get("dependencies")
    if (project.get("name", "").lower() != "astrbot" or project.get("version") != ASTRBOT_VERSION
            or not isinstance(dependencies, list) or not dependencies
            or any(not isinstance(item, str) or not item.strip() or item.startswith("-")
                   or "\n" in item or "\r" in item or "\x00" in item for item in dependencies)):
        raise AssetError("The vendored AstrBot metadata does not match the supported pin.")
    files = {}
    for directory, dirnames, filenames in os.walk(vendor, followlinks=False):
        relative_dir = Path(directory).relative_to(vendor)
        kept = []
        for name in sorted(dirnames):
            relative = relative_dir / name
            # Only the top-level dashboard is Node source; nested dashboard is Python.
            if name in _EXCLUDED_DIRS or relative.as_posix() in {"dashboard", "astrbot/dashboard/dist", "data"}:
                continue
            child = Path(directory) / name
            if child.is_symlink() or child.resolve() != child:
                raise AssetError("The vendored runtime contains a redirected source directory.")
            kept.append(name)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = Path(directory) / name
            relative = path.relative_to(vendor).as_posix()
            if name.endswith((".pyc", ".pyo")) or name in {".git", SOURCE_MARKER}:
                continue
            if path.is_symlink() or not path.is_file():
                raise AssetError("The vendored runtime contains a non-regular source file.")
            files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if not {"main.py", "pyproject.toml", "astrbot/__init__.py"}.issubset(files):
        raise AssetError("The vendored AstrBot runtime is incomplete.")
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return vendor, {"schema": 1, "version": ASTRBOT_VERSION,
                    "source_sha256": hashlib.sha256(encoded).hexdigest(), "files": files}, dependencies


def _json_file(path):
    """Read a completion marker without leaking its contents through failures."""
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, UnicodeError, ValueError):
        return {}


def _write_json(path, value):
    """Commit an owned marker atomically after its corresponding step succeeds."""
    descriptor, temporary = tempfile.mkstemp(prefix=".native-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        # Only remove this function's own staging artifact, never source or state.
        if Path(temporary).exists():
            Path(temporary).unlink()


def _source_matches(root, manifest):
    """Verify snapshot file contents so changed source is never silently patched."""
    if _json_file(root / SOURCE_MARKER) != manifest:
        return False
    # Reuse the authoritative traversal to detect deleted, changed AND added
    # source files, while allowing parent-managed state/plugins under root/data.
    try:
        _, actual, _ = _source(None, source_root=root)
        return actual == manifest
    except (AssetError, OSError, UnicodeError, ValueError, KeyError, TypeError):
        return False


def _venv_valid(python):
    """Require a dedicated venv that explicitly excludes global site-packages."""
    venv = python.parent.parent
    try:
        configuration = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
        version = re.search(r"(?mi)^version\s*=\s*(\d+)\.(\d+)\.", configuration)
        return (python.is_file() and version is not None
                and tuple(map(int, version.groups())) == sys.version_info[:2]
                and bool(re.search(r"(?mi)^include-system-site-packages\s*=\s*false\s*$", configuration)))
    except (OSError, UnicodeError):
        return False


def _run(arguments, cwd, label):
    """Run without a shell or raw child output, recording safe local milestones.

    PIP_* and PYTHON* environment settings cannot redirect installs to global
    paths. Full child output is deliberately discarded, not logged: package
    tooling can echo inherited proxy credentials or authenticated download URLs.
    The owner-only log therefore records only fixed step names and exit status.
    """
    runtime = cwd if cwd.name == "runtime" else cwd.parent
    log_path = runtime / "setup.log"
    descriptor = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as log:
        environment = {key: value for key, value in os.environ.items()
                       if not key.upper().startswith(("PIP_", "PYTHON")) and key.upper() != "VIRTUAL_ENV"}
        # An explicit null config defeats machine/user pip configuration files.
        environment["PIP_CONFIG_FILE"] = os.devnull
        environment["PYTHONNOUSERSITE"] = "1"
        log.write(label + ": started\n")
        log.flush()
        try:
            result = subprocess.run([str(item) for item in arguments], cwd=str(cwd), env=environment,
                                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL, shell=False, check=False)
        except (OSError, subprocess.SubprocessError):
            log.write(label + ": could not start\n")
            raise AssetError("Native setup could not start a required local Python step.") from None
        log.write(label + ": exit " + str(result.returncode) + "\n")
        if result.returncode:
            raise AssetError("Native setup failed during " + label + "; see runtime/setup.log.")


def _download(url, maximum, hostname):
    """Fetch one allowlisted HTTPS endpoint with no redirects and bounded memory."""
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != hostname or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment or parsed.query):
        raise AssetError("The official asset URL is invalid.")
    request = urllib.request.Request(url, headers={"User-Agent": "qhunliv-native-assets/1"})
    opener = urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != hostname or response.geturl() != url:
                raise AssetError("The official asset endpoint unexpectedly changed.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise AssetError("The official asset exceeds the download size limit.")
            chunks = []
            total = 0
            while True:
                chunk = response.read(min(1024 * 1024, maximum - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise AssetError("The official asset exceeds the download size limit.")
                chunks.append(chunk)
            return b"".join(chunks)
    except AssetError:
        raise
    except Exception:
        raise AssetError("The pinned official assets could not be downloaded.") from None


def _asset_path(name):
    """Reject zip traversal and Windows alternate streams/device-name aliases."""
    if not name or "\\" in name or "\x00" in name:
        raise AssetError("The dashboard wheel contains an unsafe path.")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {".", ".."} for part in name.split("/")):
        raise AssetError("The dashboard wheel contains an unsafe path.")
    for part in path.parts:
        if (part.endswith((".", " ")) or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
                or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)):
            raise AssetError("The dashboard wheel contains an unsafe path.")
    return path


def _dashboard_valid(dashboard):
    """Require matching version and all local JS/CSS entry files before launch."""
    try:
        if (dashboard / "assets" / "version").read_text(encoding="utf-8").strip() not in {ASTRBOT_VERSION, "v" + ASTRBOT_VERSION}:
            return False
        parser = _Entries()
        parser.feed((dashboard / "index.html").read_text(encoding="utf-8"))
        javascript = False
        for reference in parser.references:
            parsed = urlsplit(reference)
            if parsed.scheme or parsed.netloc:
                continue
            relative = unquote(parsed.path).lstrip("/")
            if not relative.lower().endswith((".js", ".css")):
                continue
            path = _asset_path(relative)
            target = dashboard.joinpath(*path.parts)
            if target.resolve() != target or not target.is_file():
                return False
            javascript = javascript or path.suffix.lower() == ".js"
        return javascript
    except (OSError, UnicodeError, ValueError, AssetError):
        return False


def _install_dashboard(dashboard):
    """Extract only vetted dist assets from the pinned official wheel, atomically."""
    metadata = json.loads(_download(PYPI_JSON_URL, MAX_METADATA_BYTES, "pypi.org"))
    if (metadata.get("info", {}).get("version") != ASTRBOT_VERSION
            or metadata.get("info", {}).get("name", "").lower() != "astrbot"):
        raise AssetError("The official release metadata does not match the AstrBot pin.")
    candidates = [item for item in metadata.get("urls", []) if item.get("filename") == WHEEL_FILENAME
                  and item.get("packagetype") == "bdist_wheel" and not item.get("yanked")]
    if len(candidates) != 1:
        raise AssetError("The pinned official dashboard wheel is unavailable.")
    release = candidates[0]
    digest = release.get("digests", {}).get("sha256", "")
    size = release.get("size")
    url = release.get("url", "")
    if (not re.fullmatch("[0-9a-f]{64}", digest) or digest != WHEEL_SHA256 or type(size) is not int
            or not 0 < size <= MAX_DOWNLOAD_BYTES or not isinstance(url, str)
            or not urlsplit(url).path.endswith("/" + WHEEL_FILENAME)):
        raise AssetError("The official dashboard wheel metadata is invalid.")
    payload = _download(url, MAX_DOWNLOAD_BYTES, "files.pythonhosted.org")
    if len(payload) != size or hashlib.sha256(payload).hexdigest() != digest:
        raise AssetError("The official dashboard wheel failed SHA256 verification.")
    with tempfile.TemporaryDirectory(prefix=".dashboard-", dir=dashboard.parent) as temporary:
        staging = Path(temporary) / "dist"
        staging.mkdir()
        hashes = {}
        aliases = set()
        expanded = 0
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            for item in archive.infolist():
                # ZipInfo normalizes Windows backslashes in filename; orig_filename
                # retains the actual archive bytes' spelling for security checks.
                original = item.orig_filename
                if not original.startswith(WHEEL_PREFIX):
                    continue
                relative = original[len(WHEEL_PREFIX):]
                if not relative or item.is_dir():
                    continue
                path = _asset_path(relative)
                alias = path.as_posix().casefold()
                # Case aliases collide on Windows; reject them also in Linux tests.
                if alias in aliases or stat.S_ISLNK(item.external_attr >> 16) or item.flag_bits & 1:
                    raise AssetError("The dashboard wheel contains an unsafe asset entry.")
                aliases.add(alias)
                expanded += item.file_size
                if expanded > MAX_EXPANDED_BYTES or len(aliases) > MAX_ASSET_FILES:
                    raise AssetError("The dashboard wheel exceeds the extraction limit.")
                target = staging.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                content = archive.read(item)
                hashes[path.as_posix()] = hashlib.sha256(content).hexdigest()
                with target.open("xb") as handle:
                    handle.write(content)
        if not _dashboard_valid(staging):
            raise AssetError("The official dashboard is incomplete or version-mismatched.")
        _write_json(staging / DASHBOARD_MARKER, {"schema": 1, "version": ASTRBOT_VERSION,
                    "wheel": WHEEL_FILENAME, "wheel_sha256": digest, "metadata_url": PYPI_JSON_URL,
                    "integrity": "PyPI HTTPS release metadata; not an author signature", "files": hashes})
        os.replace(staging, dashboard)


def _dashboard_matches(dashboard):
    """Check installed asset hashes as well as entrypoints without using the net."""
    marker = _json_file(dashboard / DASHBOARD_MARKER)
    files = marker.get("files")
    if (marker.get("version") != ASTRBOT_VERSION or marker.get("wheel") != WHEEL_FILENAME
            or not isinstance(files, dict) or not files or not _dashboard_valid(dashboard)):
        return False
    for relative, expected in files.items():
        path = _asset_path(relative)
        target = dashboard.joinpath(*path.parts)
        if target.resolve() != target or not target.is_file() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
            return False
    return True


def inspect_assets(data, repo):
    """Report local readiness only; never download, spawn, create, or repair files.

    Returns:
        The three path keys plus ``ready``, boolean ``checks``, and fixed ``issues``.
        No settings, environment values, subprocess output, or raw exceptions.
    """
    try:
        paths = _paths(data, repo)
        _, manifest, _ = _source(repo)
        runtime = paths["astrbot_root"].parent
        dependency = _json_file(runtime / DEPENDENCY_MARKER)
        checks = {"source": _source_matches(paths["astrbot_root"], manifest),
                  "venv": _venv_valid(paths["python"]),
                  "dependencies": dependency == {"schema": 1, "source_sha256": manifest["source_sha256"],
                                                   "python": list(sys.version_info[:2]), "complete": True},
                  "dashboard": _dashboard_matches(paths["dashboard"])}
        return {**paths, "ready": all(checks.values()), "checks": checks,
                "issues": [name + " is not prepared or does not match the pinned runtime" for name, valid in checks.items() if not valid]}
    except AssetError:
        raise
    except Exception:
        raise AssetError("Native assets could not be inspected safely.") from None


def prepare_assets(data, repo):
    """Prepare immutable local source, isolated full dependencies and pinned WebUI.

    Retries reuse completed stages. An existing different/modified snapshot,
    foreign venv, or mismatched dashboard is never overwritten automatically.
    Only Python 3.12/3.13 are supported; no WSL, Docker, Node, shell, or global
    pip install is used. Parent code owns configuration in astrbot_root/data.
    """
    try:
        if sys.version_info[:2] not in {(3, 12), (3, 13)}:
            raise AssetError("Native setup requires the current Python to be 3.12 or 3.13.")
        paths = _paths(data, repo)
        vendor, manifest, dependencies = _source(repo)
        root = paths["astrbot_root"]
        runtime = root.parent
        runtime.mkdir(parents=True, exist_ok=True)
        if root.exists() and any(root.iterdir()):
            if not _source_matches(root, manifest):
                raise AssetError("The runtime source differs; use a fresh data directory or explicitly migrate it.")
        else:
            with tempfile.TemporaryDirectory(prefix=".source-", dir=runtime) as temporary:
                staging = Path(temporary) / "astrbot"
                staging.mkdir()
                for relative in manifest["files"]:
                    destination = staging / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(vendor / relative, destination)
                _write_json(staging / SOURCE_MARKER, manifest)
                if not _source_matches(staging, manifest):
                    raise AssetError("The vendored source changed while preparing the snapshot.")
                if root.exists():
                    root.rmdir()  # This branch accepted only an empty, non-redirected directory.
                os.replace(staging, root)
        python = paths["python"]
        venv = python.parent.parent
        if not _venv_valid(python):
            if venv.exists():
                raise AssetError("The dedicated native virtual environment is incomplete or not isolated.")
            _run([sys.executable, "-m", "venv", str(venv)], runtime, "venv creation")
            if not _venv_valid(python):
                raise AssetError("The dedicated native virtual environment was not created correctly.")
        completion = {"schema": 1, "source_sha256": manifest["source_sha256"],
                      "python": list(sys.version_info[:2]), "complete": True}
        if _json_file(runtime / DEPENDENCY_MARKER) != completion:
            # Pass each authoritative PEP 508 requirement as one argv element.
            _run([python, "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                  "--no-input", "--no-cache-dir", "--index-url", "https://pypi.org/simple",
                  *dependencies], root, "dependency installation")
            _run([python, "-m", "pip", "--isolated", "check"], root, "dependency verification")
            _write_json(runtime / DEPENDENCY_MARKER, completion)
        dashboard = paths["dashboard"]
        if dashboard.exists():
            if not _dashboard_matches(dashboard):
                raise AssetError("The existing native dashboard differs; use a fresh data directory or explicitly migrate it.")
        else:
            _install_dashboard(dashboard)
        if not inspect_assets(data, repo)["ready"]:
            raise AssetError("Native asset verification did not complete.")
        return paths
    except AssetError:
        raise
    except Exception:
        # Never attach raw filesystem, urllib, pip, proxy or package error strings.
        raise AssetError("Native assets could not be prepared; existing state was retained.") from None


# The short name is a compatibility alias, not a second provisioning path.
provision = prepare_assets
