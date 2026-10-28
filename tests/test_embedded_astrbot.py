"""Pinned source and licensing evidence must survive packaging unchanged."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_embedded_source_notices_and_pin_are_intact():
    """Check original source counts and all original notices, without executing upstream."""
    manifest = json.loads((ROOT / 'vendor/ASTRBOT_UPSTREAM.json').read_text(encoding='utf-8'))
    source = ROOT / manifest['source_directory']
    files = [path for path in source.rglob('*') if path.is_file() and '__pycache__' not in path.parts]
    assert len(files) == manifest['source_file_count']
    assert sum(path.stat().st_size for path in files) == manifest['source_bytes']
    assert manifest['upstream_modifications'] == []
    for notice in manifest['license_files']:
        assert hashlib.sha256((source / notice['path']).read_bytes()).hexdigest() == notice['sha256']
    assert not (source / '.git').exists()
    assert not any(path.suffix.lower() in {'.deb', '.exe', '.appimage'} for path in files)


def test_distribution_does_not_bundle_napcat_runtime():
    """Only our AstrBot image is part of deployment; QQ connection is external."""
    docker = (ROOT / 'deploy/Dockerfile').read_text(encoding='utf-8')
    compose = (ROOT / 'deploy/compose.yaml').read_text(encoding='utf-8')
    assert 'COPY vendor/astrbot/' in docker
    assert 'napcat' not in docker.lower()
    assert 'napcat' not in compose.lower()
    assert '127.0.0.1:' in compose
