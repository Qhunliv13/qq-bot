"""A minimal source ZIP must import and run with only Python's standard library."""
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

import pytest

from tools import build_lite


def test_package_allowlist_has_no_vendor_or_private_state_and_never_overwrites(tmp_path):
    """Include only deliberate source inputs and preserve any existing destination bytes."""
    output = tmp_path / 'native-lite.zip'
    build_lite.build(output)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
    assert names == list(build_lite.FILES)
    assert not any('vendor/' in name or 'settings.json' in name or '__pycache__' in name for name in names)
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        build_lite.build(output)
    assert output.read_bytes() == before
    assert output.stat().st_size < 150_000


def test_extracted_small_package_needs_no_site_packages_or_parent_checkout(tmp_path):
    """Run a real isolated-interpreter configure/info from a Chinese/spaced extracted ZIP."""
    output = tmp_path / 'small.zip'
    build_lite.build(output)
    extracted = tmp_path / '原生 轻量'
    with zipfile.ZipFile(output) as archive:
        archive.extractall(extracted)
    data = tmp_path / 'private'
    command = [sys.executable, '-S', '-B', str(extracted / 'tools/lite_native.py')]
    env = {name: value for name, value in os.environ.items() if name not in ('PYTHONPATH', 'PYTHONHOME')}
    initialize = subprocess.run(command + ['configure', '--non-interactive', '--data-dir', str(data),
                                           '--model-base', 'http://127.0.0.1:18080/v1', '--model', 'synthetic-only'],
                                cwd=tmp_path, env=env, capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert initialize.returncode == 0, initialize.stderr
    info = subprocess.run(command + ['info', '--data-dir', str(data)], cwd=tmp_path, env=env,
                          capture_output=True, text=True, encoding='utf-8', timeout=10)
    assert info.returncode == 0, info.stderr
    assert 'No WSL, Docker, pip' in info.stdout
    config = json.loads((data / 'settings.json').read_text(encoding='utf-8'))
    assert config['application'] == 'qhunliv-native-lite'
    # A real source-only browser import must not require any vendored AstrBot.
    check = tmp_path / 'check_native_web_import.py'
    check.write_text('import sys\nsys.path.insert(0, ' + repr(str(extracted)) + ')\nfrom tools.lite_web import create_server\n', encoding='utf-8')
    imported = subprocess.run([sys.executable, '-S', '-B', str(check)], cwd=tmp_path, env=env, capture_output=True, timeout=10)
    assert imported.returncode == 0, imported.stderr


def test_builder_refuses_paths_in_repository():
    """Packaging cannot create generated ZIP artifacts or private data in source control."""
    with pytest.raises(ValueError):
        build_lite.build(Path(build_lite.ROOT) / 'lite.zip')
