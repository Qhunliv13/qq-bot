"""Offline workspace contracts using generated scripts and temporary state only.

No VM, network, QQ send, plugin initialization or production absolute writes.
"""

import asyncio
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

_MODULE = Path(__file__).resolve().parents[1] / "plugin" / "workspace_tools.py"
_SPEC = importlib.util.spec_from_file_location("workspace_tools_test_subject", _MODULE)
workspace = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(workspace)


class WorkspaceTests(unittest.TestCase):
    """Run precisely the generated VM scripts as isolated local Python processes."""

    def setUp(self):
        """Give each case private state and an explicit temporary workspace."""
        self.temporary = tempfile.TemporaryDirectory(prefix="workspace-tools-")
        self.base = Path(self.temporary.name)
        self.root = self.base / "runtime"
        self.cwd = self.base / "files"
        self.cwd.mkdir()
        self.calls = 0
        self.invoke("workspace_cwd", {"path": str(self.cwd)})

    def tearDown(self):
        """Release temporary files without touching shared bot or production state."""
        self.temporary.cleanup()

    def invoke(self, operation, args):
        """Execute an on-disk script and assert transport-safe single-object stdout."""
        self.calls += 1
        script = self.base / ("request-" + str(self.calls) + ".py")
        script.write_text(workspace.build_script(operation, args, runtime_root=self.root), encoding="utf-8")
        completed = subprocess.run([sys.executable, str(script)], capture_output=True,
                                   text=True, timeout=10, check=True, encoding="utf-8")
        self.assertEqual(completed.stderr, "")
        self.assertEqual(len(completed.stdout.splitlines()), 1)
        self.assertLessEqual(len(completed.stdout.strip()), 7000)
        return json.loads(completed.stdout)

    def put(self, name, content):
        """Prepare a fixture; writes here are local test setup, never remote calls."""
        path = self.cwd / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="")
        return path

    def test_schema_native_names_and_required_cas(self):
        """Expose only the requested eight native function tools and literal-edit CAS."""
        definitions = {entry["function"]["name"]: entry["function"] for entry in workspace.schema()}
        self.assertEqual(set(definitions), set(workspace._NAMES))
        self.assertIn("expected_sha256", definitions["workspace_edit"]["parameters"]["required"])
        self.assertIn("Python", definitions["workspace_grep"]["description"])
        self.assertNotIn("workspace_delete", definitions)
        for entry in workspace.schema():
            self.assertEqual(entry["type"], "function")
            self.assertFalse(entry["function"]["parameters"]["additionalProperties"])

    def test_read_lines_hash_and_default(self):
        """Default pages contain 120 lines while SHA256 covers the entire file."""
        content = "".join("line " + str(index) + "\n" for index in range(150))
        self.put("data.txt", content)
        page = self.invoke("workspace_read", {"path": "data.txt"})
        self.assertTrue(page["ok"], page)
        self.assertEqual(page["content"], "".join(content.splitlines(True)[:120]))
        self.assertEqual(page["next_offset"], 121)
        self.assertEqual(page["sha256"], hashlib.sha256(content.encode()).hexdigest())
        end = self.invoke("workspace_read", {"path": "data.txt", "offset": 121})
        self.assertEqual(end["content"], "".join(content.splitlines(True)[120:]))
        self.assertIsNone(end["next_offset"])

    def test_long_unicode_line_resumes_without_loss(self):
        """JSON expansion never corrupts transport or discards a long-line suffix."""
        content = "😀\t中文" * 1700 + "\nend\n"
        self.put("unicode.txt", content)
        args = {"path": "unicode.txt"}
        pages = []
        for _ in range(50):
            result = self.invoke("workspace_read", args)
            self.assertTrue(result["ok"], result)
            pages.append(result["content"])
            if not result["truncated"]:
                break
            args.update(offset=result["next_offset"], column=result["next_column"])
        else:
            self.fail("read pagination did not finish")
        self.assertEqual("".join(pages), content)

    def test_invalid_offsets_and_types(self):
        """Reject zero/negative line offsets, booleans, and unbounded limits."""
        self.put("a", "hello")
        for update in ({"offset": 0}, {"offset": -1}, {"limit": 0},
                       {"limit": 1001}, {"limit": True}, {"column": -1}):
            result = self.invoke("workspace_read", dict(path="a", **update))
            self.assertFalse(result["ok"], result)

    def test_new_write_atomic_and_not_executable(self):
        """New UTF-8 files require no read, cannot gain executable mode bits."""
        result = self.invoke("workspace_write", {"path": "new.txt", "content": "你好\n"})
        self.assertTrue(result["ok"], result)
        path = self.cwd / "new.txt"
        self.assertEqual(path.read_bytes(), "你好\n".encode())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o111, 0)
        self.assertEqual(list(self.cwd.glob(".workspace-*")), [])

    def test_existing_write_requires_observation(self):
        """A guessed current hash alone cannot bypass the read-before-write rule."""
        path = self.put("a", "before")
        digest = hashlib.sha256(b"before").hexdigest()
        denied = self.invoke("workspace_write", {"path": "a", "content": "after", "expected_sha256": digest})
        self.assertFalse(denied["ok"])
        self.assertIn("read_required", denied["message"])
        self.assertEqual(path.read_text(), "before")
        read = self.invoke("workspace_read", {"path": "a"})
        accepted = self.invoke("workspace_write", {"path": "a", "content": "after", "expected_sha256": read["sha256"]})
        self.assertTrue(accepted["ok"], accepted)
        self.assertEqual(path.read_text(), "after")

    def test_implicit_read_proof_and_stale_hash(self):
        """Optional explicit hash never permits a stale observed file overwrite."""
        path = self.put("a", "one")
        self.invoke("workspace_read", {"path": "a"})
        result = self.invoke("workspace_write", {"path": "a", "content": "two"})
        self.assertTrue(result["ok"], result)
        self.invoke("workspace_read", {"path": "a"})
        path.write_text("external", encoding="utf-8")
        result = self.invoke("workspace_write", {"path": "a", "content": "three"})
        self.assertFalse(result["ok"])
        self.assertEqual(path.read_text(), "external")

    def test_edit_literal_unique_and_replace_all(self):
        """Regex punctuation is literal and ambiguous edits require replace_all."""
        path = self.put("a", "a.* a.*\r\n")
        read = self.invoke("workspace_read", {"path": "a"})
        args = {"path": "a", "old_string": "a.*", "new_string": "new", "expected_sha256": read["sha256"]}
        self.assertFalse(self.invoke("workspace_edit", args)["ok"])
        args["replace_all"] = True
        result = self.invoke("workspace_edit", args)
        self.assertTrue(result["ok"], result)
        self.assertEqual(path.read_bytes(), b"new new\r\n")

    def test_edit_missing_hash_and_mismatch(self):
        """Builders reject missing edit hashes and runtime rejects false CAS."""
        self.put("a", "hello")
        with self.assertRaises(ValueError):
            workspace.build_script("workspace_edit", {"path": "a", "old_string": "h", "new_string": "H"})
        self.invoke("workspace_read", {"path": "a"})
        result = self.invoke("workspace_edit", {"path": "a", "old_string": "h", "new_string": "H", "expected_sha256": "0" * 64})
        self.assertFalse(result["ok"])
        self.assertIn("sha256_conflict", result["message"])

    def test_glob_hidden_ignored_stable_pages(self):
        """All files count including dot/ignored paths, with max-100 resumable pages."""
        for name in ("z.txt", "a.txt", ".hidden.txt", "ignored/deep.txt"):
            self.put(name, "x")
        self.put(".gitignore", "ignored/\n")
        first = self.invoke("workspace_glob", {"pattern": "*.txt", "limit": 2})
        second = self.invoke("workspace_glob", {"pattern": "*.txt", "limit": 2, "offset": first["next_offset"]})
        paths = first["paths"] + second["paths"]
        self.assertEqual(paths, sorted(paths))
        self.assertEqual(len(paths), 4)
        self.assertTrue(any(".hidden" in path for path in paths))
        self.assertTrue(any("deep" in path for path in paths))
        self.assertIsNone(second["next_offset"])

    def test_glob_100_and_recursive_root_match(self):
        """Recursive globs include root files and cannot exceed a hundred entries."""
        for number in range(105):
            self.put(str(number).zfill(3) + ".py", "pass")
        result = self.invoke("workspace_glob", {"pattern": "**/*.py"})
        self.assertGreater(len(result["paths"]), 0)
        self.assertLessEqual(len(result["paths"]), 100)
        self.assertEqual(result["next_offset"], len(result["paths"]))
        self.assertTrue(result["truncated"])
        remaining = self.invoke("workspace_glob", {"pattern": "**/*.py", "offset": result["next_offset"]})
        self.assertEqual(len(result["paths"]) + len(remaining["paths"]), 105)

    def test_grep_python_regex_include_and_pagination(self):
        """Search Python lookbehinds with explicit basename glob filtering."""
        self.put("a.py", "id=11\nid=22\nno match\n")
        self.put("nested/b.py", "id=33\n")
        self.put("ignored.txt", "id=44\n")
        first = self.invoke("workspace_grep", {"pattern": "(?<=id=)\\d+", "include": "*.py", "limit": 1})
        self.assertEqual(first["regex_engine"], "python.re")
        self.assertEqual(first["matches"][0]["line"], 1)
        second = self.invoke("workspace_grep", {"pattern": "(?<=id=)\\d+", "include": "*.py", "offset": first["next_offset"]})
        self.assertEqual([match["text"] for match in second["matches"]], ["id=22", "id=33"])
        self.assertIsNone(second["next_offset"])

    def test_grep_invalid_and_binary_explicit_skip(self):
        """Malformed regex is a structured error; binary exclusions stay visible."""
        (self.cwd / "binary").write_bytes(b"\x00foo")
        self.put("valid", "foo\n")
        invalid = self.invoke("workspace_grep", {"pattern": "["})
        self.assertFalse(invalid["ok"])
        result = self.invoke("workspace_grep", {"pattern": "foo"})
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["skipped_count"], 1)

    def test_grep_unicode_preview_stays_transport_safe(self):
        """A single Unicode-heavy matching line is previewed, never broken JSON."""
        self.put("unicode.txt", "😀" * 1800 + "\n")
        result = self.invoke("workspace_grep", {"pattern": "😀"})
        self.assertTrue(result["ok"], result)
        self.assertEqual(len(result["matches"]), 1)
        self.assertTrue(result["matches"][0]["text_truncated"])

    def test_write_invalidates_prior_observation(self):
        """Read proofs bind one version and cannot authorize blind later writes."""
        self.put("a", "first")
        self.invoke("workspace_read", {"path": "a"})
        self.assertTrue(self.invoke("workspace_write", {"path": "a", "content": "second"})["ok"])
        denied = self.invoke("workspace_write", {"path": "a", "content": "third"})
        self.assertFalse(denied["ok"])
        self.assertIn("read_required", denied["message"])

    def test_persistent_cwd_and_missing_directory(self):
        """Separate requests share VM settings but cannot chdir to nonexistent paths."""
        self.assertEqual(self.invoke("workspace_cwd", {})["cwd"], str(self.cwd.resolve()))
        self.assertFalse(self.invoke("workspace_cwd", {"path": "missing"})["ok"])
        state = json.loads((self.root / "workspace.json").read_text())
        self.assertEqual(state["cwd"], str(self.cwd.resolve()))
        self.assertFalse((self.root / "workspace").exists())

    def test_default_cwd_created_only_on_cwd_request(self):
        """Bootstrapping uses the overridden test root, never a production directory."""
        fresh = self.base / "fresh-runtime"
        script = workspace.build_script("workspace_cwd", {}, runtime_root=fresh)
        path = self.base / "fresh.py"
        path.write_text(script, encoding="utf-8")
        completed = subprocess.run([sys.executable, str(path)], capture_output=True, text=True, check=True, timeout=10)
        result = json.loads(completed.stdout)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["cwd"], str((fresh / "workspace").resolve()))

    def test_present_references_only_existing_regular_files(self):
        """Presentation yields VM descriptors without uploads or execution."""
        path = self.put("a.txt", "hello")
        result = self.invoke("workspace_present", {"paths": ["a.txt"]})
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["delivery"], "references_only")
        self.assertEqual(result["files"][0]["path"], str(path.resolve()))
        for paths in ([], ["a.txt"] * 5, ["missing"], ["."]):
            self.assertFalse(self.invoke("workspace_present", {"paths": paths})["ok"])

    def test_images_validate_header_and_suffix(self):
        """Supported signatures return references; suffix-only impostors are rejected."""
        cases = {"a.png": b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + b"\x00" * 16,
                 "a.jpg": b"\xff\xd8\xff\xe0", "a.gif": b"GIF89a\x01\x00\x01\x00",
                 "a.webp": b"RIFF\x20\x00\x00\x00WEBPVP8 "}
        for name, header in cases.items():
            (self.cwd / name).write_bytes(header)
            result = self.invoke("workspace_read_image", {"path": name})
            self.assertTrue(result["ok"], result)
            self.assertEqual(result["image"]["type"], "image")
        self.put("fake.png", "not an image")
        self.assertFalse(self.invoke("workspace_read_image", {"path": "fake.png"})["ok"])
        (self.cwd / "wrong.txt").write_bytes(cases["a.png"])
        self.assertFalse(self.invoke("workspace_read_image", {"path": "wrong.txt"})["ok"])

    def test_arguments_cannot_inject_helper(self):
        """Quotes/newlines in paths and contents stay data after base64 JSON encoding."""
        content = "'); import os\nraise RuntimeError('injected')\n😀"
        result = self.invoke("workspace_write", {"path": "quoted.txt", "content": content})
        self.assertTrue(result["ok"], result)
        self.assertEqual((self.cwd / "quoted.txt").read_text(encoding="utf-8"), content)
        with self.assertRaises(ValueError):
            workspace.build_script("workspace_delete", {"path": "quoted.txt"})
        with self.assertRaises(ValueError):
            workspace.build_script("workspace_read", {"path": "a", "unexpected": True})

    def test_dispatch_strict_json_and_adapter_contract(self):
        """Only complete native vm_exec JSON objects are accepted; no host fallback."""
        outputs = [({"ok": True, "output": '{"ok":true,"cwd":"/test"}'}, True),
                   ({"ok": False, "error": "no vm"}, False),
                   ({"ok": True, "output": '{"ok":true}\nnoise'}, False),
                   ({"ok": True, "output": '{"ok":true'}, False),
                   ({"ok": True, "output": '[]'}, False),
                   ({"ok": True, "output": '{"ok":1}'}, False),
                   ({"ok": True, "output": '{"ok":true,"x":NaN}'}, False),
                   ({"ok": True, "output": '{"ok":true,"ok":false}'}, False)]
        for response, expected in outputs:
            async def execute(code):
                """Emulate the caller-owned async vm_exec adapter without remote access."""
                compile(code, "<workspace-script>", "exec")
                return response
            result = asyncio.run(workspace.dispatch("workspace_cwd", {}, execute))
            self.assertEqual(result["ok"], expected, result)

    def test_root_override_at_call_time(self):
        """Monkeypatching the module root never leaves a captured production default."""
        original = workspace._ROOT
        try:
            workspace._ROOT = str(self.root)
            self.assertEqual(workspace.build_script("workspace_cwd", {}),
                             workspace.build_script("workspace_cwd", {}, runtime_root=self.root))
        finally:
            workspace._ROOT = original


if __name__ == "__main__":
    unittest.main()
