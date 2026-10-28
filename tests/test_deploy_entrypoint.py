"""Offline tests for fresh-state initialization and the single-service image."""

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("deploy_entrypoint", ROOT / "deploy" / "entrypoint.py")
ENTRYPOINT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ENTRYPOINT)


class EntrypointTests(unittest.TestCase):
    """Exercise state safety without Docker, upstream imports, QQ or a model."""

    def setUp(self):
        """Create a private fake plugin and explicit bootstrap settings."""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.plugin = self.root / "image-plugin"
        self.plugin.mkdir()
        (self.plugin / "main.py").write_text("# immutable plugin\n", encoding="utf-8")
        self.settings = {
            "schema_version": 1,
            "model": {"base_url": "https://model.example.invalid/v1", "model": "chosen-model", "api_key": "test-private-key"},
            "owner_qqs": [], "persona": "A private persona",
            "dashboard_password": "test-private-dashboard-password",
        }
        self.settings_path = self.root / "settings.json"
        self.settings_path.write_text(json.dumps(self.settings), encoding="utf-8")
        self.data = self.root / "astrbot" / "data"
        self.higan = self.root / "higan"
        self.defaults = {"dashboard": {}, "provider_settings": {}, "unrelated": {"preserved": True}}

    def prepare(self):
        """Use the real initializer against temporary paths only."""
        try:
            return ENTRYPOINT.prepare(self.settings_path, self.data, self.higan, self.plugin, self.defaults)
        except OSError as exc:
            # Production is Linux; unprivileged Windows cannot create symlinks.
            if getattr(exc, "winerror", None) == 1314:
                self.skipTest("Windows symlink privilege unavailable; run these integration tests on Linux")
            raise

    def test_fresh_configuration_uses_explicit_settings(self):
        """Seed the official provider relationship and never enable QQ implicitly."""
        original = copy.deepcopy(self.defaults)
        environment = self.prepare()
        config = json.loads((self.data / "cmd_config.json").read_text(encoding="utf-8"))
        self.assertEqual(config["provider_sources"][0]["api_base"], self.settings["model"]["base_url"])
        self.assertEqual(config["provider_sources"][0]["key"], ["test-private-key"])
        self.assertEqual(config["provider"][0]["provider_source_id"], "qhunliv-provider")
        self.assertEqual(config["provider"][0]["model"], "chosen-model")
        self.assertEqual(config["agent_runner"]["config"]["model"]["provider_id"], "qhunliv-model")
        self.assertEqual(config["platform"], [])
        self.assertEqual(config["admins_id"], [])
        self.assertEqual(environment["HIGAN_OWNER_QQS"], "[]")
        self.assertEqual(environment["HIGAN_SETTINGS_FILE"], str(self.settings_path))
        expected_hash = hashlib.md5(self.settings["dashboard_password"].encode()).hexdigest()
        self.assertEqual(config["dashboard"]["password"], expected_hash)
        self.assertEqual(self.defaults, original)
        self.assertTrue((self.data / "plugins" / "astrbot_plugin_higan").is_symlink())

    def test_repeated_start_preserves_operator_changes(self):
        """Do not overwrite live dashboard model edits or other plugin folders."""
        self.prepare()
        config = self.data / "cmd_config.json"
        config.write_text('{"operator_change": true}', encoding="utf-8")
        other = self.data / "plugins" / "user_plugin"
        other.mkdir()
        (other / "user.txt").write_text("retain", encoding="utf-8")
        self.prepare()
        self.assertEqual(config.read_text(encoding="utf-8"), '{"operator_change": true}')
        self.assertEqual((other / "user.txt").read_text(encoding="utf-8"), "retain")

    def test_image_plugin_change_is_rejected(self):
        """Never silently update plugin code bound to a persisted installation."""
        self.prepare()
        (self.plugin / "main.py").write_text("# changed image\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "controlled update"):
            self.prepare()

    def test_foreign_data_is_not_adopted(self):
        """Refuse existing state before writing configuration or plugin links."""
        self.data.mkdir(parents=True)
        sentinel = self.data / "existing.db"
        sentinel.write_bytes(b"do-not-touch")
        with self.assertRaisesRegex(ValueError, "not fresh"):
            self.prepare()
        self.assertEqual(sentinel.read_bytes(), b"do-not-touch")
        self.assertFalse((self.data / "cmd_config.json").exists())

    def test_replaced_plugin_is_not_overwritten(self):
        """Reject an operator-replaced plugin and preserve its contents."""
        self.prepare()
        link = self.data / "plugins" / "astrbot_plugin_higan"
        link.unlink()
        link.mkdir()
        (link / "main.py").write_text("# operator plugin", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "installation changed"):
            self.prepare()
        self.assertEqual((link / "main.py").read_text(encoding="utf-8"), "# operator plugin")

    def test_invalid_settings_write_nothing(self):
        """Reject invalid bootstrap inputs before creating persisted directories."""
        for field, value in (("owner_qqs", [123]), ("persona", None), ("dashboard_password", "short")):
            with self.subTest(field=field):
                invalid = {**self.settings, field: value}
                self.settings_path.write_text(json.dumps(invalid), encoding="utf-8")
                with self.assertRaises(ValueError):
                    self.prepare()
                self.assertFalse(self.data.exists())

    def test_missing_initialized_config_is_rejected(self):
        """A marker must not mask a missing required runtime configuration."""
        self.prepare()
        (self.data / "cmd_config.json").unlink()
        with self.assertRaisesRegex(ValueError, "configuration is missing"):
            self.prepare()

    def test_actual_upstream_defaults_have_no_import_side_effects(self):
        """Read actual frozen defaults without importing AstrBot core or its DB."""
        defaults, version = ENTRYPOINT.load_upstream_defaults(ROOT / "vendor" / "astrbot")
        self.assertEqual(version, "4.28.1")
        self.assertEqual(defaults["config_version"], 3)
        self.assertEqual(defaults["agent_runner"]["runner_type"], "local")
        self.assertFalse(self.data.exists())

    def test_compose_has_only_bot_and_loopback_ports(self):
        """Prevent accidental bundling of a QQ client or public dashboard port."""
        compose = (ROOT / "deploy" / "compose.yaml").read_text(encoding="utf-8")
        self.assertEqual([line for line in compose.splitlines() if line.startswith("  ") and not line.startswith("   ")], ["  bot:"])
        self.assertIn("io.qhunliv.bootstrap.data-dir", compose)
        self.assertIn("127.0.0.1:${BOT_WEB_PORT:-6185}:6185", compose)
        self.assertIn("/root/higan", compose)
        self.assertNotIn("napcat", compose.lower())
        dockerfile = (ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("FROM node:20", dockerfile)
        self.assertIn("COPY vendor/astrbot/ /AstrBot/", dockerfile)
        self.assertIn("--frozen-lockfile", dockerfile)
        self.assertIn("/AstrBot/astrbot/dashboard/dist/", dockerfile)
        self.assertNotIn("napcat", dockerfile.lower())


if __name__ == "__main__":
    unittest.main()
