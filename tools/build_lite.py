"""Build an allowlisted source-only native Windows lite ZIP, without downloads or secrets."""
import argparse
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
# Shared imports are included, not copied or reimplemented. Never recurse into data.
FILES = (
    'lite.cmd', 'lite.ps1', 'tools/windows-lite.ps1', 'tools/lite_native.py', 'tools/build_lite.py',
    'tools/lite_credentials.py', 'tools/lite_web.py', 'tools/lite.py', 'tools/setup_inputs.py',
    'bot_core/__init__.py', 'bot_core/contracts.py', 'bot_core/harness.py',
    'bot_core/http_host.py', 'plugin/agent_loop.py', 'plugin/tool_context.py',
    'docs/WINDOWS_LITE.md', 'LICENSE',
)


def build(output):
    """Write one new archive outside the checkout from a fixed source-file allowlist."""
    target = Path(output).expanduser().resolve()
    if target == ROOT or ROOT in target.parents or target.suffix.lower() != '.zip':
        raise ValueError('Choose a new .zip path outside this repository')
    for name in FILES:
        source = ROOT / name
        if source.is_symlink() or not source.is_file():
            raise ValueError('Required lightweight source is missing or not a regular file')
    # Exclusive creation: existing artifacts/private files can never be overwritten.
    with zipfile.ZipFile(target, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name in FILES:
            archive.write(ROOT / name, name)
    return target


def main(argv=None):
    """Build a source-only package or emit a fixed error without private path diagnostics."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True, help='new ZIP path outside the repository')
    args = parser.parse_args(argv)
    try:
        output = build(args.output)
    except Exception:
        print('Lite package not created. Check the source checkout and choose a new external .zip path.')
        return 2
    print('Source-only Windows lite ZIP created; no interpreter, dependency downloads or credentials included.')
    print('Size: ' + str(output.stat().st_size) + ' bytes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
