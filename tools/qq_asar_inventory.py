"""Inventory ASAR header metadata only; never extract, import, or execute entries.

Format reference: https://github.com/electron/asar/blob/main/README.md
Only the size Pickle and JSON header are read; no archive entry contents are read.
This utility does not recover original QQ main, prove a bootstrap ABI, or run QQ.
"""
import argparse
import json
import re
import struct
from pathlib import Path

# Bound memory independently of the archive's untrusted length claims.
MAX_HEADER_BYTES = 8 * 1024 * 1024
MAX_ENTRIES = 100000
MAX_DEPTH = 100


def _integer(value, label, allow_string=False):
    """Accept nonnegative integers, with ASAR's ASCII decimal offsets only."""
    if allow_string and isinstance(value, str) and re.fullmatch(r'[0-9]+', value):
        value = int(value)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError('Invalid ' + label)
    return value


def inspect_archive(path):
    """Read a bounded Chromium Pickle header and validate metadata without reading payloads."""
    archive = Path(path)
    with archive.open('rb') as stream:
        length = archive.stat().st_size
        prefix = stream.read(16)
        if len(prefix) != 16:
            raise ValueError('Truncated ASAR prefix')
        first, header_size, pickle_size, json_size = struct.unpack('<4I', prefix)
        # A size pickle has one uint32; the following pickle has a padded JSON string.
        padding = (4 - json_size % 4) % 4
        if (first != 4 or json_size == 0 or json_size > MAX_HEADER_BYTES
                or pickle_size != 4 + json_size + padding
                or header_size != 4 + pickle_size or 8 + header_size > length):
            raise ValueError('Invalid or oversized ASAR header')
        try:
            tree = json.loads(stream.read(json_size).decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
            raise ValueError('Invalid ASAR JSON header') from error
    if not isinstance(tree, dict) or not isinstance(tree.get('files'), dict):
        raise ValueError('ASAR root files must be an object')
    payload_size = length - (8 + header_size)
    output = []

    def walk(files, prefix='', depth=0):
        """Validate components, cap traversal, and record links without following them."""
        if depth > MAX_DEPTH:
            raise ValueError('ASAR nesting limit exceeded')
        for component, entry in files.items():
            if (not isinstance(component, str) or not component or component in {'.', '..'}
                    or any(char in component for char in ('/', '\\', '\x00', ':'))
                    or not isinstance(entry, dict)):
                raise ValueError('Invalid ASAR entry name or object')
            name = prefix + component
            if 'files' in entry:
                if not isinstance(entry['files'], dict):
                    raise ValueError('ASAR directory files must be an object')
                walk(entry['files'], name + '/', depth + 1)
                continue
            if len(output) >= MAX_ENTRIES:
                raise ValueError('ASAR entry limit exceeded')
            # Links are metadata only; deliberately do not resolve their destinations.
            if 'link' in entry:
                if not isinstance(entry['link'], str):
                    raise ValueError('Invalid ASAR link')
                output.append({'name': name, 'kind': 'link', 'link': entry['link']})
                continue
            size = _integer(entry.get('size'), 'entry size')
            unpacked = entry.get('unpacked', False)
            if not isinstance(unpacked, bool):
                raise ValueError('Invalid unpacked flag')
            record = {'name': name, 'kind': 'file', 'size': size, 'unpacked': unpacked}
            if not unpacked:
                offset = _integer(entry.get('offset'), 'packed offset', allow_string=True)
                if offset > payload_size or size > payload_size - offset:
                    raise ValueError('Packed entry exceeds archive bounds')
                record['offset'] = offset
            output.append(record)
    walk(tree['files'])
    return {'archive_basename': archive.name, 'archive_bytes': length,
            'header_json_bytes': json_size, 'payload_offset': 8 + header_size,
            'entry_count': len(output), 'entries': output}


def main():
    """Require an explicit archive path and return metadata or a path-redacted error."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', required=True)
    args = parser.parse_args()
    try:
        result = inspect_archive(args.archive)
    except (OSError, ValueError) as error:
        # Never print OS exception text: it can include private full filesystem paths.
        print(json.dumps({'ok': False, 'error_type': type(error).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
