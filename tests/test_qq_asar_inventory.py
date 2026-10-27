"""Exercise static header inspection with synthetic archives only, no Tencent code."""
import importlib.util
import json
import struct
from pathlib import Path

import pytest

MODULE_PATH = Path(__file__).resolve().parents[1] / 'tools' / 'qq_asar_inventory.py'
SPEC = importlib.util.spec_from_file_location('qq_asar_inventory', MODULE_PATH)
INVENTORY = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(INVENTORY)


def archive_bytes(files, payload=b'abc'):
    """Build the same bounded size/header pickle structure using independent fixture data."""
    body = json.dumps({'files': files}, separators=(',', ':')).encode('utf-8')
    padding = b'\0' * ((4 - len(body) % 4) % 4)
    pickle_size = 4 + len(body) + len(padding)
    return struct.pack('<4I', 4, 4 + pickle_size, pickle_size, len(body)) + body + padding + payload


def inspect_bytes(tmp_path, data):
    """Write a local disposable synthetic archive and inspect header metadata only."""
    path = tmp_path / 'synthetic.asar'
    path.write_bytes(data)
    return INVENTORY.inspect_archive(path)


def test_packed_file(tmp_path):
    """Validate relative payload offsets and avoid exposing a full local path."""
    result = inspect_bytes(tmp_path, archive_bytes({'main.js': {'size': 3, 'offset': '0'}}))
    assert result['entry_count'] == 1
    assert result['archive_basename'] == 'synthetic.asar'
    assert result['entries'][0] == {'name': 'main.js', 'kind': 'file', 'size': 3, 'unpacked': False, 'offset': 0}


def test_unpacked_and_link(tmp_path):
    """Unpacked files need no packed offset; link targets remain unfollowed metadata."""
    result = inspect_bytes(tmp_path, archive_bytes({'native.node': {'size': 999, 'unpacked': True}, 'alias': {'link': '../external'}, 'nested': {'files': {'.entry': {'size': 0, 'offset': 0}}}}))
    assert result['entries'][0]['unpacked'] is True
    assert 'offset' not in result['entries'][0]
    assert result['entries'][1]['kind'] == 'link'
    assert result['entries'][2]['name'] == 'nested/.entry'


@pytest.mark.parametrize('name', ['..', '.', 'a/b', 'a\\b', '/abs', 'C:drive', 'nul\x00'])
def test_invalid_names(tmp_path, name):
    """Reject names that could imply traversal or platform-dependent absolute paths."""
    with pytest.raises(ValueError):
        inspect_bytes(tmp_path, archive_bytes({name: {'size': 0, 'offset': '0'}}))


@pytest.mark.parametrize('entry', [{'size': True, 'offset': 0}, {'size': -1, 'offset': 0}, {'size': 1, 'offset': True}, {'size': 1, 'offset': '-1'}, {'size': 1, 'offset': '٠'}, {'size': 4, 'offset': 0}, {'size': 0, 'offset': 4}, {'size': 0, 'offset': 0, 'unpacked': 1}])
def test_invalid_file_metadata(tmp_path, entry):
    """Reject boolean integers, invalid decimal offsets, flags, and payload overflow."""
    with pytest.raises(ValueError):
        inspect_bytes(tmp_path, archive_bytes({'x': entry}))


@pytest.mark.parametrize('data', [b'', b'123', struct.pack('<4I', 4, 12, 8, INVENTORY.MAX_HEADER_BYTES + 1), struct.pack('<4I', 5, 8, 4, 1), struct.pack('<4I', 4, 8, 4, 1)])
def test_invalid_prefix(tmp_path, data):
    """Fail before trusting inconsistent or overlarge Pickle length fields."""
    with pytest.raises(ValueError):
        inspect_bytes(tmp_path, data)


def test_header_only_reads(tmp_path, monkeypatch):
    """Prove inspection requests bytes only through JSON, never archive entry payloads."""
    path = tmp_path / 'bounded.asar'
    path.write_bytes(archive_bytes({'x': {'size': 3, 'offset': 0}}, b'xyz'))
    real_open = Path.open
    reads = []

    class Guard:
        """Record reads while preserving the file context manager interface."""
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            """Provide the read guard to the inspector."""
            return self

        def __exit__(self, *args):
            """Close the file regardless of inspection success."""
            self.stream.close()

        def read(self, size):
            """Record bounded read requests; no seek/extraction interface is provided."""
            reads.append(size)
            return self.stream.read(size)

    def guarded_open(target, *args, **kwargs):
        """Wrap the archive stream without changing metadata or bytes."""
        return Guard(real_open(target, *args, **kwargs))

    monkeypatch.setattr(Path, 'open', guarded_open)
    result = INVENTORY.inspect_archive(path)
    assert reads == [16, result['header_json_bytes']]


def test_nesting_limit(tmp_path, monkeypatch):
    """Reject an archive directory depth over the explicitly bounded traversal limit."""
    monkeypatch.setattr(INVENTORY, 'MAX_DEPTH', 1)
    with pytest.raises(ValueError, match='nesting'):
        inspect_bytes(tmp_path, archive_bytes({'a': {'files': {'b': {'files': {}}}}}))


def test_entry_limit(tmp_path, monkeypatch):
    """Reject excess leaf entries rather than returning a misleading partial inventory."""
    monkeypatch.setattr(INVENTORY, 'MAX_ENTRIES', 1)
    with pytest.raises(ValueError, match='entry limit'):
        inspect_bytes(tmp_path, archive_bytes({'a': {'size': 0, 'offset': 0}, 'b': {'size': 0, 'offset': 0}}))


def test_invalid_json(tmp_path):
    """Invalid UTF-8 in a correctly sized header produces a controlled error."""
    with pytest.raises(ValueError, match='JSON'):
        inspect_bytes(tmp_path, struct.pack('<4I', 4, 12, 8, 1) + b'\xff\0\0\0')
