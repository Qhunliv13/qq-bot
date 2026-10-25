"""Apply an explicitly requested group mode through the shared store, with a full backup."""
import argparse
import importlib.util
import json
from pathlib import Path
import shutil


def main():
    """Require exact source/config/group/mode and a new backup; never send chat or restart services."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--backup", type=Path, required=True)
    parser.add_argument("--group", required=True)
    parser.add_argument("--mode", required=True)
    args = parser.parse_args()
    spec = importlib.util.spec_from_file_location("release_group_policy", args.plugin / "group_policy.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    store = module.GroupPolicyStore(args.config)
    before = store.read()
    if args.backup.exists():
        raise RuntimeError("backup_already_exists")
    args.backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(args.config, args.backup)
    args.backup.chmod(0o600)
    result = store.update(args.group, args.mode)
    after = store.read()
    if args.mode == "mention_only" and args.group not in after["mention_only"]:
        raise RuntimeError("policy_readback_failed")
    print(json.dumps({"ok": result["ok"], "group": args.group, "mode": args.mode,
                      "readback_verified": True, "previous_mention_only": args.group in before["mention_only"]}))


if __name__ == "__main__":
    main()
