"""Validate only the release payload, without reading private runtime configuration."""
from pathlib import Path
import ast
import json
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    """Fail on embedded credential patterns and syntax errors; never print matching secret values."""
    failures = []
    scanned = []
    patterns = [
        re.compile(r"sk-[A-Za-z0-9_-]{16,}"),
        re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        re.compile(r"(?im)^\s*(?:[A-Z_]*(?:PASSWORD|API_KEY|ACCESS_TOKEN|AUTH_TOKEN|SECRET_KEY))\s*=\s*['\"][^'\"\n]{8,}['\"]"),
    ]
    # Tests intentionally contain fake credential fixtures, not deployable configuration.
    for directory in (ROOT / "plugin", ROOT / "tools"):
        for path in directory.rglob("*"):
            if path.suffix not in {".py", ".json", ".yaml", ".md", ".html", ".ps1", ".sh", ".cmd"}:
                continue
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".py":
                ast.parse(text)
            for pattern in patterns:
                if pattern.search(text):
                    failures.append({"file": str(path.relative_to(ROOT)), "reason": "credential_pattern"})
                    break
            scanned.append(str(path.relative_to(ROOT)))
    print(json.dumps({"scanned_files": len(scanned), "failures": failures}, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
