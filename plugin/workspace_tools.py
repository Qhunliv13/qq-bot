"""DSH-style file tools executed ONLY through the bot's existing Linux VM executor.

This module never accesses the host filesystem. ``dispatch`` accepts an async
``execute(code)`` adapter for ``_vm_tool('vm_exec', {'code': code, 'lang': 'python'})``.
Generated scripts use only Python's standard library, one shared runtime, and
complete JSON smaller than the VM's 8000-character transport cap. VM account
permissions are NOT a security sandbox. No delete/move/shell tool is provided.
"""

import base64
import json

# State belongs to the bot VM, not the production application or the DSH host.
# Resolve at call time so offline tests can monkeypatch the root safely.
_ROOT = "/root/higan"
_NAMES = ("workspace_read", "workspace_write", "workspace_edit", "workspace_glob",
          "workspace_grep", "workspace_cwd", "workspace_present", "workspace_read_image")

# Every operation shares the same path, observation, CAS and JSON implementation.
_RUNTIME = r'''
import contextlib
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile

# 7000 includes JSON escaping and metadata, leaving transport headroom.
OUTPUT_LIMIT = 7000
TEXT_LIMIT = 3500


def encoded(value):
    """Measure the actual JSON wire representation, including escaped controls."""
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"))


def emit(value):
    """Never print a partial object, even when a path or exception is enormous."""
    payload = encoded(value)
    if len(payload) > OUTPUT_LIMIT:
        payload = encoded({"ok": False, "error": "result_too_large",
                           "message": "Use a narrower path/pattern or smaller page."})
    print(payload)


def integer(args, key, default, minimum, maximum):
    """Reject booleans, negative offsets and unbounded page requests explicitly."""
    value = args.get(key, default)
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(key + " must be an integer from " + str(minimum) + " to " + str(maximum))
    return value


def text(args, key, default=None):
    """Keep paths and literal edits typed; never interpret arguments as code."""
    value = args.get(key, default)
    if not isinstance(value, str) or (key != "content" and "\x00" in value):
        raise ValueError(key + " must be a string without NUL")
    return value


def sha256(path):
    """Hash the entire file incrementally instead of loading huge files in memory."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@contextlib.contextmanager
def locked(root):
    """Serialize cooperating tool requests; this does not lock unrelated VM programs."""
    root.mkdir(parents=True, exist_ok=True)
    with (root / "workspace.lock").open("a+b") as handle:
        if os.name == "nt":
            # Local offline tests also run on Windows; production uses flock.
            import msvcrt
            handle.seek(0)
            if handle.read(1) == b"":
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def load_state(root):
    """Read a bounded private settings file; never silently reset corrupt state."""
    path = root / "workspace.json"
    if not path.exists():
        return {"cwd": str(root / "workspace"), "observations": {}}
    with path.open("r", encoding="utf-8") as handle:
        payload = handle.read(1024 * 1024 + 1)
    if len(payload) > 1024 * 1024:
        raise ValueError("workspace state exceeds 1 MiB")
    value = json.loads(payload)
    if (not isinstance(value, dict) or not isinstance(value.get("cwd"), str)
            or not isinstance(value.get("observations"), dict)):
        raise ValueError("invalid workspace state")
    return value


def atomic(path, data, expected=None):
    """Replace bytes via same-directory rename; new files use exclusive hardlink.

    A fresh 0600 temporary never gains execute bits. Existing non-execute mode
    bits are retained on POSIX. Hash recheck catches changes before commit, but
    filesystem CAS cannot exclude writers outside this cooperating runtime.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=".workspace-", dir=str(path.parent))
    temp = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        if expected is not None:
            if not path.is_file() or sha256(path) != expected:
                raise ValueError("sha256_conflict: file changed before commit")
            os.chmod(temp, stat.S_IMODE(path.stat().st_mode) & ~0o111)
            os.replace(temp, path)
        else:
            # Unlike replace(), link() fails if another actor created the file.
            os.link(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def save_state(root, value):
    """Persist cwd and read proofs atomically without granting execute permission."""
    path = root / "workspace.json"
    payload = encoded(value).encode("utf-8")
    if len(payload) > 1024 * 1024:
        raise ValueError("workspace observation state is full")
    atomic(path, payload, sha256(path) if path.exists() else None)


def resolve(value, state):
    """Resolve against persistent VM cwd, allowing VM-account paths, not host paths."""
    if not value or "\x00" in value:
        raise ValueError("path must not be empty or contain NUL")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else Path(state["cwd"]) / path).resolve()


def regular(path):
    """Refuse devices/FIFOs before open so file tools cannot hang on special files."""
    if not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("path is not a regular file")
    return path


def proof(path, state, expected):
    """Require a previous content read AND a still-current SHA256 before overwrite."""
    current = sha256(regular(path))
    observed = state["observations"].get(str(path))
    if observed != current:
        raise ValueError("read_required: read the current file before modifying it")
    if expected is not None and expected != current:
        raise ValueError("sha256_conflict: expected_sha256 does not match")
    return current


def expected_hash(args, required=False):
    """Only accept canonical SHA256 proofs, never arbitrary truthy sentinels."""
    value = args.get("expected_sha256")
    if value is None and not required:
        return None
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError("expected_sha256 must be a lowercase SHA256 hex digest")
    return value


def read_page(path, args):
    """Read UTF-8 lines with resumable long-line columns and bounded text memory."""
    offset = integer(args, "offset", 1, 1, 2147483647)
    limit = integer(args, "limit", 120, 1, 1000)
    column = integer(args, "column", 0, 0, 2147483647)
    chunks, count, line, skipped = [], 0, 1, 0
    next_offset, next_column = None, 0
    # newline='' preserves the source's exact line endings for literal edits.
    with path.open("r", encoding="utf-8", newline="") as handle:
        while True:
            chunk = handle.readline(1024)
            if not chunk:
                break
            end = chunk.endswith("\n") or chunk.endswith("\r")
            if line < offset:
                if end:
                    line += 1
                continue
            if line == offset and skipped < column:
                take = min(column - skipped, len(chunk))
                skipped += take
                chunk = chunk[take:]
            if chunk:
                available = TEXT_LIMIT - count
                part = chunk[:available]
                # Astral Unicode and controls can expand twelvefold in JSON.
                # Fit the content by its encoded size, retaining a resume column.
                low, high = 0, len(part)
                prefix = "".join(chunks)
                while low < high:
                    middle = (low + high + 1) // 2
                    if len(encoded(prefix + part[:middle])) <= 5000:
                        low = middle
                    else:
                        high = middle - 1
                part = part[:low]
                chunks.append(part)
                count += len(part)
                if len(part) < len(chunk):
                    next_offset, next_column = line, skipped + len(part)
                    break
                skipped += len(chunk)
            if end:
                line += 1
                skipped = 0
                if line >= offset + limit:
                    if handle.read(1):
                        next_offset = line
                    break
            if count >= TEXT_LIMIT:
                if handle.read(1):
                    next_offset, next_column = line, skipped
                break
    return {"content": "".join(chunks), "offset": offset, "column": column,
            "next_offset": next_offset, "next_column": next_column,
            "truncated": next_offset is not None}


def file_descriptor(path):
    """Return a VM reference only; the caller decides whether/how to present it."""
    regular(path)
    return {"path": str(path), "name": path.name, "size": path.stat().st_size,
            "type": "file", "ref": str(path)}


def image_descriptor(path):
    """Validate both filename suffix and minimal image signature without libraries."""
    result = file_descriptor(path)
    with path.open("rb") as handle:
        header = handle.read(32)
    suffix = path.suffix.lower()
    mime = None
    if suffix == ".png" and len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n" and header[12:16] == b"IHDR":
        mime = "image/png"
    elif suffix in (".jpg", ".jpeg") and len(header) >= 4 and header[:3] == b"\xff\xd8\xff":
        mime = "image/jpeg"
    elif suffix == ".gif" and len(header) >= 10 and header[:6] in (b"GIF87a", b"GIF89a"):
        mime = "image/gif"
    elif suffix == ".webp" and len(header) >= 16 and header[:4] == b"RIFF" and header[8:12] == b"WEBP" and header[12:16] in (b"VP8 ", b"VP8L", b"VP8X"):
        mime = "image/webp"
    if mime is None:
        raise ValueError("not a supported image: suffix and header must agree")
    result.update(type="image", mime_type=mime)
    return result


def list_page(items, args, key):
    """Page sorted references without splitting entries or losing resume position."""
    offset = integer(args, "offset", 0, 0, 2147483647)
    limit = integer(args, "limit", 100, 1, 100)
    result = {"ok": True, key: [], "offset": offset, "next_offset": None, "truncated": False}
    for index, item in enumerate(items):
        if index < offset:
            continue
        # Bound complete JSON, not raw characters: controls/Unicode may expand.
        candidate = dict(result)
        candidate[key] = result[key] + [item]
        if len(result[key]) >= limit or len(encoded(candidate)) > OUTPUT_LIMIT - 150:
            result.update(next_offset=index, truncated=True)
            if not result[key]:
                raise ValueError("single result exceeds output budget; narrow the search")
            break
        result[key].append(item)
    return result


def files_under(path):
    """Walk hidden/ignored files, not symlink directories, in stable relative order."""
    if path.is_file():
        return [path]
    if not path.is_dir():
        raise FileNotFoundError(str(path))
    found = []
    for directory, directories, names in os.walk(path, followlinks=False):
        directories.sort()
        for name in names:
            candidate = Path(directory) / name
            if candidate.is_file():
                found.append(candidate)
    return sorted(found, key=lambda item: item.as_posix())


def glob_match(relative, pattern):
    """Use Python fnmatch globs; a slashless pattern matches basenames at any depth."""
    if "/" not in pattern:
        return fnmatch.fnmatchcase(Path(relative).name, pattern)
    # pathlib-style **/ also matches zero directory components.
    return (fnmatch.fnmatchcase(relative, pattern)
            or (pattern.startswith("**/") and glob_match(relative, pattern[3:])))


def grep_matches(path, args):
    """Yield Python re.search matches per UTF-8 line, skipping binary/non-UTF8 files.

    Pattern semantics are Python stdlib re, NOT ripgrep. Offset counts matching
    lines, include is a Python fnmatch glob, and large lines are previewed with
    text_truncated=True. Files over 8 MiB are explicitly reported as skipped.
    """
    expression = re.compile(text(args, "pattern"))
    include = text(args, "include", "*")
    skipped = []
    matches = []
    for candidate in files_under(path):
        relative = candidate.name if path.is_file() else candidate.relative_to(path).as_posix()
        if not glob_match(relative, include):
            continue
        if candidate.stat().st_size > 8 * 1024 * 1024:
            skipped.append({"path": str(candidate), "reason": "file_over_8_mib"})
            continue
        try:
            # Validate UTF-8 before emitting lines, avoiding half-accepted files.
            content = candidate.read_text(encoding="utf-8")
        except UnicodeError:
            skipped.append({"path": str(candidate), "reason": "non_utf8"})
            continue
        if "\x00" in content:
            skipped.append({"path": str(candidate), "reason": "binary"})
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if expression.search(line):
                # Preview by JSON size too: 1000 astral characters otherwise
                # exceed the whole transport cap after surrogate escaping.
                preview = line[:1000]
                while len(encoded(preview)) > 4000:
                    preview = preview[:len(preview) // 2]
                matches.append({"path": str(candidate), "line": number,
                                "text": preview, "text_truncated": len(line) > len(preview)})
    result = list_page(matches, args, "matches")
    # Skips must be explicit but cannot consume the transport budget indefinitely.
    result.update(skipped=skipped[:10], skipped_count=len(skipped),
                  regex_engine="python.re")
    while len(encoded(result)) > OUTPUT_LIMIT and result["matches"]:
        result["matches"].pop()
        result.update(next_offset=result["offset"] + len(result["matches"]), truncated=True)
    return result


def operate(operation, args, root):
    """Dispatch only the documented file capabilities under persistent VM cwd."""
    with locked(root):
        state = load_state(root)
        if operation == "workspace_cwd":
            value = args.get("path")
            if value is None:
                path = Path(state["cwd"])
                # Bootstrap only the designated default workspace, not any user path.
                if path == root / "workspace":
                    path.mkdir(parents=True, exist_ok=True)
            else:
                path = resolve(text(args, "path"), state)
            if not path.is_dir():
                raise ValueError("cwd must be an existing directory")
            state["cwd"] = str(path.resolve())
            save_state(root, state)
            return {"ok": True, "path": state["cwd"], "cwd": state["cwd"]}
        if operation == "workspace_present":
            paths = args.get("paths")
            if not isinstance(paths, list) or not 1 <= len(paths) <= 4:
                raise ValueError("paths must contain 1 to 4 file paths")
            descriptors = [file_descriptor(resolve(text({"path": value}, "path"), state)) for value in paths]
            return {"ok": True, "files": descriptors, "delivery": "references_only"}
        path = resolve(text(args, "path", "."), state)
        if operation == "workspace_read_image":
            return {"ok": True, "image": image_descriptor(path)}
        if operation == "workspace_read":
            regular(path)
            before = sha256(path)
            result = read_page(path, args)
            if sha256(path) != before:
                raise ValueError("file_changed_during_read: retry")
            result.update(ok=True, path=str(path), sha256=before)
            # Oversized references must not grant a proof for an undisplayed read.
            if len(encoded(result)) > OUTPUT_LIMIT:
                raise ValueError("result_too_large: path metadata exceeds output budget")
            state["observations"][str(path)] = before
            save_state(root, state)
            return result
        if operation in ("workspace_write", "workspace_edit"):
            expected = expected_hash(args, operation == "workspace_edit")
            current = proof(path, state, expected) if path.exists() else None
            if operation == "workspace_edit":
                if current is None:
                    raise FileNotFoundError(str(path))
                old, new = text(args, "old_string"), text(args, "new_string")
                if not old:
                    raise ValueError("old_string must not be empty")
                replace_all = args.get("replace_all", False)
                if type(replace_all) is not bool:
                    raise ValueError("replace_all must be a boolean")
                # Literal edits are bounded intentionally; reads still support huge files.
                if path.stat().st_size > 8 * 1024 * 1024:
                    raise ValueError("edit supports files up to 8 MiB")
                content = path.read_bytes().decode("utf-8")
                count = content.count(old)
                if count == 0 or (count != 1 and not replace_all):
                    raise ValueError("old_string must match exactly once unless replace_all=true")
                content = content.replace(old, new, -1 if replace_all else 1)
            else:
                content = text(args, "content")
                if current is None and expected is not None:
                    raise ValueError("sha256_conflict: expected existing file")
            data = content.encode("utf-8")
            atomic(path, data, current)
            digest = hashlib.sha256(data).hexdigest()
            # A write is not a content read; invalidate any previous read proof.
            state["observations"].pop(str(path), None)
            save_state(root, state)
            return {"ok": True, "path": str(path), "sha256": digest, "size": len(data)}
        if operation == "workspace_glob":
            pattern = text(args, "pattern")
            if not pattern or Path(pattern).is_absolute() or ".." in Path(pattern).parts:
                raise ValueError("pattern must be a nonempty relative glob without '..'")
            matches = []
            for candidate in files_under(path):
                relative = candidate.name if path.is_file() else candidate.relative_to(path).as_posix()
                if glob_match(relative, pattern):
                    matches.append(str(candidate))
            return list_page(matches, args, "paths")
        if operation == "workspace_grep":
            return grep_matches(path, args)
        raise ValueError("unknown workspace operation")
'''


def schema():
    """Return OpenAI-native function schemas; describe actual Python regex semantics."""
    path = {"type": "string", "description": "Path in the bot Linux VM; relative to persistent VM cwd."}
    digest = {"type": "string", "pattern": "^[0-9a-f]{64}$", "description": "SHA256 returned by workspace_read."}
    offset = {"type": "integer", "minimum": 0, "default": 0}
    page = {"type": "integer", "minimum": 1, "maximum": 100, "default": 100}
    definitions = [
        ("workspace_read", "Read UTF-8 VM text (default 120 lines). Returns whole-file SHA256 read proof. One-based offset; resume next_offset AND next_column for long lines. Complete output <=7000 chars.",
         {"path": path, "offset": {"type": "integer", "minimum": 1, "default": 1}, "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 120}, "column": {"type": "integer", "minimum": 0, "default": 0}}, ["path"]),
        ("workspace_write", "Atomically write UTF-8 in the VM; parent directory must exist. Existing file requires a prior current workspace_read; optional expected_sha256 adds explicit CAS. No execute bits. New files need no read.",
         {"path": path, "content": {"type": "string"}, "expected_sha256": digest}, ["path", "content"]),
        ("workspace_edit", "Atomic literal UTF-8 replacement, maximum 8 MiB. Requires current workspace_read and its expected_sha256; unique match unless replace_all. No execution.",
         {"path": path, "old_string": {"type": "string", "minLength": 1}, "new_string": {"type": "string"}, "replace_all": {"type": "boolean", "default": False}, "expected_sha256": digest}, ["path", "old_string", "new_string", "expected_sha256"]),
        ("workspace_glob", "Find files including hidden/ignored files, stable path sorting; max 100/page. Python fnmatch glob: slashless matches basename at any depth; **/ may match zero directories. No symlink-directory traversal. Resume next_offset.",
         {"pattern": {"type": "string"}, "path": path, "offset": offset, "limit": page}, ["pattern"]),
        ("workspace_grep", "Search UTF-8 files with Python stdlib re.search (NOT ripgrep syntax). include uses Python fnmatch glob; stable path/line ordering, offset counts matching lines, max 100/page. Binary/non-UTF8/>8MiB files explicitly skipped; long match lines previewed.",
         {"pattern": {"type": "string"}, "path": path, "include": {"type": "string"}, "offset": offset, "limit": page}, ["pattern"]),
        ("workspace_cwd", "Get/set persistent bot VM cwd in /root/higan/workspace.json. Setting requires existing directory. First get creates only the default /root/higan/workspace. Not host cwd or a sandbox.",
         {"path": path}, []),
        ("workspace_present", "Return 1-4 existing regular VM file descriptors for references only. Does NOT upload, send QQ messages, or access the host.",
         {"paths": {"type": "array", "items": path, "minItems": 1, "maxItems": 4}}, ["paths"]),
        ("workspace_read_image", "Validate PNG/JPEG/GIF/WebP suffix AND minimal header; return VM image reference. Caller must append the reference to live_refs to expose actual pixels. No automatic QQ send.",
         {"path": path}, ["path"]),
    ]
    return [{"type": "function", "function": {"name": name, "description": description,
             "parameters": {"type": "object", "properties": properties,
                            "required": required, "additionalProperties": False}}}
            for name, description, properties, required in definitions]


def build_script(operation, args, runtime_root=None):
    """Build an injection-safe stdlib VM script; do not execute locally or on import."""
    if operation not in _NAMES:
        raise ValueError("unknown workspace operation")
    if not isinstance(args, dict):
        raise ValueError("workspace arguments must be an object")
    definitions = {item["function"]["name"]: item["function"]["parameters"] for item in schema()}
    definition = definitions[operation]
    if any(key not in definition["properties"] for key in args):
        raise ValueError("unknown workspace argument")
    if any(key not in args for key in definition["required"]):
        raise ValueError("missing required workspace argument")
    payload = json.dumps({"operation": operation, "args": args,
                          "root": str(_ROOT if runtime_root is None else runtime_root)},
                         ensure_ascii=True, allow_nan=False).encode("utf-8")
    token = base64.b64encode(payload).decode("ascii")
    return (_RUNTIME + "\nimport base64\nREQUEST = json.loads(base64.b64decode(" + repr(token)
            + ").decode('utf-8'))\n"
            + "try:\n    emit(operate(REQUEST['operation'], REQUEST['args'], Path(REQUEST['root'])))\n"
            + "except Exception as error:\n    emit({'ok': False, 'error': type(error).__name__, 'message': str(error)[:500]})\n")


def _strict_object(pairs):
    """Reject ambiguous duplicate object keys in the executor's JSON response."""
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate VM JSON key")
        value[key] = item
    return value


def _reject_constant(value):
    """Python accepts NaN/Infinity by default, but they are not JSON values."""
    raise ValueError("invalid VM JSON constant: " + value)


async def dispatch(name, args, execute):
    """Await the existing VM adapter and parse exactly one complete JSON object.

    Errors are data, never silent host fallback. The caller owns live_refs and
    presentation; this layer never sends attachments or messages itself.
    """
    try:
        script = build_script(name, args)
        native = await execute(script)
        if not isinstance(native, dict) or native.get("ok") is not True:
            return {"ok": False, "error": "vm_exec_failed"}
        output = native.get("output")
        if not isinstance(output, str) or len(output) > 7001:
            return {"ok": False, "error": "invalid_vm_output"}
        result = json.loads(output, object_pairs_hook=_strict_object,
                            parse_constant=_reject_constant)
        if not isinstance(result, dict) or type(result.get("ok")) is not bool:
            return {"ok": False, "error": "invalid_vm_output"}
        return result
    except (ValueError, TypeError, UnicodeError):
        return {"ok": False, "error": "invalid_workspace_request_or_output"}
    except Exception:
        return {"ok": False, "error": "vm_exec_failed"}
