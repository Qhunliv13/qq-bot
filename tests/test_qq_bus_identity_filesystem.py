"""Stdlib Linux-root filesystem checks, runnable without installing pytest."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import qq_isolated_runtime_probe as runtime  # noqa: E402


@unittest.skipUnless(sys.platform == "linux", "Linux ownership contract")
class IdentityFilesystemTests(unittest.TestCase):
    """Exercise real exclusive creation and path containment without launching services."""

    def test_fixed_private_records(self):
        """Reject overwrite, symlinks and nonprivate staging; never read host records."""
        if runtime.os.geteuid() != 0:
            self.skipTest("root-only setup")
        with tempfile.TemporaryDirectory(prefix="qq-identity-test-") as temporary:
            stage = Path(temporary)
            jail = stage / "root"
            jail.mkdir()
            original = Path.open
            def exclusive(path, mode="r", *args, **kwargs):
                """Forbid all reads and all writes outside the two fixed jail paths."""
                self.assertEqual(mode, "xb")
                self.assertIn(path, (jail / "etc/passwd", jail / "etc/group"))
                return original(path, mode, *args, **kwargs)
            with patch.object(Path, "open", exclusive):
                report = runtime.create_synthetic_identity(jail)
            self.assertEqual((jail / "etc/passwd").read_bytes(),
                             b"qqprobe:x:65534:65534:isolated runtime:/tmp/home:/usr/sbin/nologin\n")
            self.assertEqual((jail / "etc/group").read_bytes(), b"qqprobe:x:65534:\n")
            self.assertEqual({item.name for item in (jail / "etc").iterdir()}, {"passwd", "group"})
            self.assertTrue(all(record["root_owned"] and record["mode_0444"]
                                for record in report["records"].values()))
            with self.assertRaises(runtime.ProbeError):
                runtime.create_synthetic_identity(jail)
            alias = stage / "alias"
            alias.symlink_to(jail, target_is_directory=True)
            with self.assertRaises(runtime.ProbeError):
                runtime.create_synthetic_identity(alias)
            empty = stage / "empty"
            empty.mkdir()
            (empty / "etc").symlink_to(stage, target_is_directory=True)
            with self.assertRaises(runtime.ProbeError):
                runtime.create_synthetic_identity(empty)
            fresh = stage / "fresh"
            fresh.mkdir()
            stage.chmod(0o755)
            with self.assertRaises(runtime.ProbeError):
                runtime.create_synthetic_identity(fresh)
            self.assertFalse(any(fresh.iterdir()))
            stage.chmod(0o700)


if __name__ == "__main__":
    unittest.main()
