"""Provision the pinned native runtime without modifying the checkout or global Python.

The vendored ``pyproject.toml`` is the only core dependency list. The official
AstrBot 4.28.1 wheel is used *only* for prebuilt WebUI files, never for Python
code. Its recorded SHA256 pin must agree with the exact-version PyPI JSON over
HTTPS and with the downloaded bytes before extraction. This is transport-backed integrity, not an author
signature or an independently authenticated release signature.

Public entry points return pathlib paths. ``inspect_assets`` is offline and
normally read-only; uncertain probe cleanup alone persists the retry guard.
``prepare_assets`` installs only into data/runtime/.venv.
Persistent AstrBot state belongs in the returned astrbot_root / "data".
"""

from __future__ import annotations

from functools import partial
import hashlib
from html.parser import HTMLParser
import http.client
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
from urllib.parse import unquote, urlsplit
import urllib.request
import zipfile

from tools.native_setup_process import SetupCleanupError, run_setup_step

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
# A finite per-child-step policy, not Bot readiness or the asset HTTP deadline.
# Slow networks may explicitly choose up to one day; timeout never retries itself.
SETUP_TIMEOUT_ENV = "BOT_NATIVE_SETUP_TIMEOUT"
DEFAULT_SETUP_TIMEOUT = 1800
SETUP_REVIEW_MARKER = "setup-review-required.json"
# Each metadata/wheel request receives its own budget, with no redirect/retry reset.
# DNS resolution is OS-owned and not interruptible here; the budget is enforced
# after it returns, and on every socket receive (headers, chunks and body alike).
DOWNLOAD_TIMEOUT_ENV = "BOT_NATIVE_DOWNLOAD_TIMEOUT"
DEFAULT_DOWNLOAD_TIMEOUT = 300
# Read-only metadata probes are local, short-lived trusted code, not pip installs.
DEPENDENCY_PROBE_TIMEOUT = 20
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
    # Include pip's persistent cache in the same owned boundary as the runtime.
    for target in (runtime, root, venv, runtime / "pip-cache",
                   root / "astrbot", root / "astrbot" / "dashboard", dashboard):
        try:
            info = target.lstat()
        except FileNotFoundError:
            continue  # Missing owned directories are created only during preparation.
        # Reparse tags can redirect writes even when resolve() leaves the spelling alone.
        if (stat.S_ISLNK(info.st_mode) or target.resolve() != target
                or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
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
    """Parse unique isolation/version fields, rejecting ambiguity before any child."""
    venv = python.parent.parent
    try:
        fields = {}
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8").splitlines():
            key, separator, value = line.partition("=")
            key = key.strip().lower()
            if separator and key in {"version", "include-system-site-packages"}:
                # site.py uses the final duplicate, so an earlier false is not proof.
                if key in fields:
                    return False
                fields[key] = value.strip()
        version = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", fields.get("version", ""))
        return (python.is_file() and version is not None
                and tuple(map(int, version.groups()[:2])) == sys.version_info[:2]
                and fields.get("include-system-site-packages", "").lower() == "false")
    except (OSError, UnicodeError):
        return False


# -I -S avoids user/global sites and executable .pth/customizer code. Python 3.12
# does not set sys.prefix for a venv under -S, so select only its owned site path.
# pip already supplies packaging in supported venvs; no host dependency is needed.
_DEPENDENCY_PROBE = r'''
import csv
import importlib.metadata as metadata
import io
import json
import os
from pathlib import Path
import sys

python, expected_version, requirements = json.loads(sys.argv[1])
assert os.path.abspath(sys.executable) == os.path.abspath(python)
assert list(sys.version_info[:2]) == expected_version
venv = Path(python).parent.parent
site = venv / ("Lib/site-packages" if os.name == "nt" else
               "lib/python%d.%d/site-packages" % tuple(expected_version))
sys.path.insert(0, str(site))
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.utils import canonicalize_name
from pip._vendor.packaging.version import Version

installed = {}
for distribution in metadata.distributions(path=[str(site)]):
    name = canonicalize_name(distribution.metadata["Name"])
    assert name and name not in installed
    installed[name] = distribution
pending = [(Requirement(item), frozenset({""})) for item in requirements]
visited = set()
checked_files = set()
while pending:
    requirement, parent_extras = pending.pop()
    if requirement.marker and not any(requirement.marker.evaluate({"extra": extra})
                                      for extra in parent_extras):
        continue
    name = canonicalize_name(requirement.name)
    distribution = installed[name]
    assert not requirement.url
    version = Version(distribution.version)
    assert requirement.specifier.contains(version, prereleases=True)
    if name not in checked_files:
        checked_files.add(name)
        # Check one recorded importable file per top-level package. This catches
        # retained dist-info with deleted package roots without an expensive full
        # RECORD hash/data-file audit or importing dependency code/binary modules.
        record = distribution.read_text("RECORD")
        assert record
        roots = set()
        for row in csv.reader(io.StringIO(record)):
            relative = Path(row[0])
            if (relative.parts and relative.parts[0] not in roots
                    and relative.parts[0] != ".." and not relative.is_absolute()
                    and relative.suffix.lower() in {".py", ".pyd", ".so"}):
                roots.add(relative.parts[0])
                assert distribution.locate_file(relative).is_file()
    extras = frozenset({"", *requirement.extras})
    key = (name, extras)
    if key not in visited:
        visited.add(key)
        pending.extend((Requirement(item), extras) for item in distribution.requires or ())
'''


def _dependencies_valid(python, dependencies):
    """Check all applicable requirement/extras closures in the actual interpreter.

    This is a read-only, offline metadata/recorded-entrypoint check, not an
    install, import smoke test or full RECORD hash audit. Run on every readiness
    decision (no TTL), so a replaced venv or removed distribution cannot reuse a
    stale marker. The -S probe imports only stdlib and pip's packaging parser,
    never runtime dependencies/.pth/customizers. Windows uses the existing owned
    job because even a venv python.exe redirector can start a base-Python child.
    Normal probes write no files/logs/markers and perform no installation.
    Unconfirmed Windows cleanup persists the existing fail-closed review guard.
    """
    review_marker = python.parent.parent.parent / SETUP_REVIEW_MARKER
    if review_marker.exists():
        raise AssetError("Native setup cleanup needs ownership review before dependency probing.")
    try:
        arguments = [str(python), "-I", "-S", "-B", "-c", _DEPENDENCY_PROBE,
                     json.dumps([str(python), list(sys.version_info[:2]), dependencies])]
        if os.name == "nt":
            environment = {key: value for key, value in os.environ.items()
                           if not key.upper().startswith(("PIP_", "PYTHON")) and key.upper() != "VIRTUAL_ENV"}
            status = run_setup_step(arguments, cwd=str(python.parent.parent), environment=environment,
                                    timeout=DEPENDENCY_PROBE_TIMEOUT)
        else:
            status = subprocess.run(
                arguments, cwd=str(python.parent.parent), stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                shell=False, check=False, timeout=DEPENDENCY_PROBE_TIMEOUT).returncode
        return status == 0
    except SetupCleanupError:
        # Uncertain ownership cleanup is not ordinary missing metadata. Persist
        # the same retry barrier used by installs, even on an inspection path.
        try:
            _write_json(review_marker, {"schema": 1, "review_required": True})
        except OSError:
            raise AssetError("Native dependency probe cleanup could not be confirmed and its review guard could not be saved; "
                             "do not retry before ownership review.") from None
        raise AssetError("Native dependency probe cleanup could not be confirmed; installation was not started.") from None
    except (OSError, subprocess.SubprocessError):
        # A missing/broken interpreter, metadata parser or timeout is not ready.
        return False


def _dependency_completion(manifest):
    """Record the new verification contract; actual interpreter checks stay mandatory."""
    return {"schema": 2, "source_sha256": manifest["source_sha256"],
            "python": list(sys.version_info[:2]), "complete": True,
            "verification": "isolated-metadata-v1"}


def _dependency_marker_matches(marker, completion):
    """Accept legacy completion only when the caller also checks actual packages."""
    legacy = {key: value for key, value in completion.items() if key != "verification"}
    legacy["schema"] = 1
    return marker in (completion, legacy)


def _run(arguments, cwd, label):
    """Run without a shell or raw child output, recording safe local milestones.

    PIP_* and PYTHON* environment settings cannot redirect installs to global
    paths. Full child output is deliberately discarded, not logged: package
    tooling can echo inherited proxy credentials or authenticated download URLs.
    The owner-only log therefore records only fixed step names and exit status.
    """
    runtime = cwd if cwd.name == "runtime" else cwd.parent
    if (runtime / SETUP_REVIEW_MARKER).exists():
        raise AssetError("Native setup cleanup needs ownership review before another installation can start.")
    raw_timeout = os.environ.get(SETUP_TIMEOUT_ENV, str(DEFAULT_SETUP_TIMEOUT))
    if not re.fullmatch(r"[0-9]{1,5}", raw_timeout) or not 1 <= int(raw_timeout) <= 86400:
        raise AssetError("BOT_NATIVE_SETUP_TIMEOUT must be an integer from 1 to 86400 seconds.")
    timeout = int(raw_timeout)
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
        review_marker = runtime / SETUP_REVIEW_MARKER
        marker_armed = False
        cleanup_confirmed = False
        try:
            if os.name == "nt":
                # Arm the fail-closed marker BEFORE a process exists. If teardown
                # later cannot be proved, no last-minute marker write is needed.
                _write_json(review_marker, {"schema": 1, "review_required": True})
                marker_armed = True
                # Assign at process creation, before even the venv redirector can
                # spawn. The shared supervisor owns only finite installer work.
                try:
                    status = run_setup_step([str(item) for item in arguments], cwd=str(cwd),
                                            environment=environment, timeout=timeout)
                except SetupCleanupError:
                    raise
                except BaseException:
                    # The supervisor contract proves an empty job before any
                    # non-cleanup exception escapes, including cancellation.
                    cleanup_confirmed = True
                    raise
                cleanup_confirmed = True
            else:
                # This is not the Linux/Compose installer and makes no Unix
                # descendant containment claim; preserve the test/compatibility path.
                status = subprocess.run([str(item) for item in arguments], cwd=str(cwd), env=environment,
                                        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                        stderr=subprocess.DEVNULL, shell=False, check=False, timeout=timeout).returncode
        except SetupCleanupError:
            # The pre-armed marker remains. It contains no PID, argv or secrets
            # and is never auto-cleared after cleanup uncertainty.
            log.write(label + ": cleanup unconfirmed\n")
            raise AssetError("Native setup cleanup could not be confirmed; retry is blocked pending ownership review.") from None
        except subprocess.TimeoutExpired:
            log.write(label + ": timed out\n")
            raise AssetError("Native setup step timed out; owned setup work was ended on Windows. "
                             "Cached files were retained; explicitly retry or adjust BOT_NATIVE_SETUP_TIMEOUT.") from None
        except (OSError, subprocess.SubprocessError):
            log.write(label + ": could not start\n")
            raise AssetError("Native setup could not start a required local Python step.") from None
        finally:
            if marker_armed and cleanup_confirmed:
                # Fail closed if removal fails; never hide a retained guard or
                # mark dependencies complete while another setup remains blocked.
                review_marker.unlink()
        log.write(label + ": exit " + str(status) + "\n")
        if status:
            raise AssetError("Native setup failed during " + label + "; see runtime/setup.log.")


def _remaining(deadline):
    """Return the shrinking socket budget; never reset it when bytes drip in."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise AssetError("The official asset download exceeded its total time budget.")
    return remaining


class _DeadlineReader(io.RawIOBase):
    """Bound every raw receive, including HTTP header and chunk-framing loops."""

    def __init__(self, stream, sock, deadline):
        super().__init__()
        self.stream, self.sock, self.deadline = stream, sock, deadline

    def readable(self):
        """Allow http.client's BufferedReader to consume this guarded transport."""
        return True

    def readinto(self, buffer):
        """Limit a single receive to the remaining total, not a fresh idle timeout."""
        self.sock.settimeout(_remaining(self.deadline))
        count = self.stream.readinto(buffer)
        _remaining(self.deadline)
        return count

    def close(self):
        """Close the owning socket file even when parsing or the deadline fails."""
        try:
            self.stream.close()
        finally:
            super().close()


class _DeadlineSocket:
    """Preserve the real socket lifetime while wrapping only its response reader."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def makefile(self, mode):
        """Wrap unbuffered socket reads, not HTTPResponse.read's multi-recv loop."""
        raw = self.sock.makefile(mode, buffering=0)
        return io.BufferedReader(_DeadlineReader(raw, self.sock, self.deadline))


class _DeadlineHTTPSConnection(http.client.HTTPSConnection):
    """Keep standard TLS/proxy policy while budgeting each HTTP response receive."""

    def __init__(self, *args, deadline, **kwargs):
        self.deadline = deadline
        super().__init__(*args, **kwargs)
        # A bound factory also covers proxy CONNECT responses before TLS starts.
        self.response_class = self._response

    def _response(self, sock, *args, **kwargs):
        """Use the same absolute deadline for CONNECT, headers and response body."""
        return http.client.HTTPResponse(_DeadlineSocket(sock, self.deadline), *args, **kwargs)

    def connect(self):
        """Charge DNS/TCP/TLS time to the budget and bound TLS after TCP returns."""
        self.timeout = _remaining(self.deadline)
        original = self._create_connection
        self._create_connection = self._connect_socket
        try:
            super().connect()
            self.sock.settimeout(_remaining(self.deadline))
        finally:
            self._create_connection = original

    def _tunnel(self):
        """Shrink TLS's timeout after a proxy CONNECT has consumed its budget."""
        super()._tunnel()
        self.sock.settimeout(_remaining(self.deadline))

    def _connect_socket(self, address, _timeout, source_address):
        """Resolve once and charge every candidate TCP connection to one budget."""
        host, port = address
        candidates = socket.getaddrinfo(host, port, 0, socket.SOCK_STREAM)
        _remaining(self.deadline)  # OS resolver latency is charged, never forgiven.
        last_error = OSError("No asset endpoint addresses were available.")
        for family, kind, protocol, _, target in candidates:
            sock = None
            try:
                # Resolver candidates may include a family disabled by local OS
                # policy. Construction failure must fall through just like a
                # failed connect, without resetting the shared time budget.
                _remaining(self.deadline)
                sock = socket.socket(family, kind, protocol)
                sock.settimeout(_remaining(self.deadline))
                if source_address:
                    sock.bind(source_address)
                sock.connect(target)
                sock.settimeout(_remaining(self.deadline))
                return sock
            except OSError as error:
                # A second address must not receive a fresh full TCP timeout.
                if sock is not None:
                    sock.close()
                last_error = error
            except BaseException:
                if sock is not None:
                    sock.close()
                raise
        raise last_error


class _DeadlineHTTPSHandler(urllib.request.HTTPSHandler):
    """Install a deadline only on this opener; never patch global urllib state."""

    def __init__(self, deadline):
        super().__init__()
        self.deadline = deadline

    def https_open(self, request):
        """Retain urllib proxy handling and default certificate/hostname validation."""
        return self.do_open(partial(_DeadlineHTTPSConnection, deadline=self.deadline),
                            request, context=self._context)


def _download(url, maximum, hostname):
    """Fetch pinned HTTPS with bounded bytes and a configurable monotonic budget.

    Every raw socket receive uses the shrinking budget, so even a single body
    read, header line or chunk header cannot drip forever. OS DNS resolution is
    the explicit exception: it cannot be interrupted by socket.settimeout, but
    an over-budget resolver result is rejected before TLS or body consumption.
    No background download worker, automatic retry or global opener is used.
    """
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or parsed.hostname != hostname or parsed.port not in (None, 443)
            or parsed.username or parsed.password or parsed.fragment or parsed.query):
        raise AssetError("The official asset URL is invalid.")
    raw_timeout = os.environ.get(DOWNLOAD_TIMEOUT_ENV, str(DEFAULT_DOWNLOAD_TIMEOUT))
    if not re.fullmatch(r"[0-9]{1,4}", raw_timeout) or not 1 <= int(raw_timeout) <= 3600:
        raise AssetError("BOT_NATIVE_DOWNLOAD_TIMEOUT must be an integer from 1 to 3600 seconds.")
    deadline = time.monotonic() + int(raw_timeout)
    request = urllib.request.Request(url, headers={"User-Agent": "qhunliv-native-assets/1"})
    opener = urllib.request.build_opener(_NoRedirect(), _DeadlineHTTPSHandler(deadline))
    try:
        with opener.open(request, timeout=_remaining(deadline)) as response:
            _remaining(deadline)
            final = urlsplit(response.geturl())
            if final.scheme != "https" or final.hostname != hostname or response.geturl() != url:
                raise AssetError("The official asset endpoint unexpectedly changed.")
            length = response.headers.get("Content-Length")
            if length is not None and (not length.isdigit() or int(length) > maximum):
                raise AssetError("The official asset exceeds the download size limit.")
            chunks = []
            total = 0
            while True:
                _remaining(deadline)
                chunk = response.read(min(1024 * 1024, maximum - total + 1))
                _remaining(deadline)
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
    """Report local readiness without download, install or ordinary file writes.

    A bounded isolated interpreter probe checks installed metadata, including
    applicable transitive requirements/extras, without importing runtime modules.
    Only uncertain Windows probe cleanup writes the existing review guard to
    block unsafe retries; normal inspections never create or upgrade markers.

    Returns:
        The three path keys plus ``ready``, boolean ``checks``, and fixed ``issues``.
        No settings, environment values, subprocess output, or raw exceptions.
    """
    try:
        paths = _paths(data, repo)
        _, manifest, dependencies = _source(repo)
        runtime = paths["astrbot_root"].parent
        dependency = _json_file(runtime / DEPENDENCY_MARKER)
        venv_valid = _venv_valid(paths["python"])
        completion = _dependency_completion(manifest)
        cleanup_valid = not (runtime / SETUP_REVIEW_MARKER).exists()
        checks = {"setup_cleanup": cleanup_valid,
                  "source": _source_matches(paths["astrbot_root"], manifest),
                  "venv": venv_valid,
                  "dependencies": (cleanup_valid and venv_valid and _dependency_marker_matches(dependency, completion)
                                   and _dependencies_valid(paths["python"], dependencies)),
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
        if (runtime / SETUP_REVIEW_MARKER).exists():
            raise AssetError("Native setup cleanup needs ownership review before another installation can start.")
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
        completion = _dependency_completion(manifest)
        previous = _json_file(runtime / DEPENDENCY_MARKER)
        if not (_dependency_marker_matches(previous, completion) and _dependencies_valid(python, dependencies)):
            # Invalidate stale proof before repair; a failed retry must stay not-ready.
            marker_path = runtime / DEPENDENCY_MARKER
            if marker_path.exists():
                marker_path.unlink()
            cache = runtime / "pip-cache"
            # Reuse the owned-leaf ACL helper; never repair an existing foreign cache.
            if os.name == "nt":
                from plugin.local_runtime import ensure_private_windows_path
                ensure_private_windows_path(cache)
            else:
                cache.mkdir(mode=0o700, exist_ok=True)
            # Pip owns cache contents across failures; the full vendor list still owns installs.
            _run([python, "-m", "pip", "--isolated", "--disable-pip-version-check", "install",
                  "--no-input", "--cache-dir", str(cache), "--index-url", "https://pypi.org/simple",
                  *dependencies], root, "dependency installation")
            _run([python, "-m", "pip", "--isolated", "check"], root, "dependency verification")
            if not _dependencies_valid(python, dependencies):
                raise AssetError("Native dependency metadata verification did not complete.")
            _write_json(runtime / DEPENDENCY_MARKER, completion)
        elif previous != completion:
            # Healthy schema-1 installs upgrade their proof without reinstalling.
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
