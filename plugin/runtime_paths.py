"""Resolve durable bot paths once without embedding a developer's machine layout."""

import os
from pathlib import Path


def data_root():
    """Use explicit deployment state, otherwise the running account's home directory.

    Root-owned legacy installs retain /root/higan via Path.home(), while ordinary
    users are never redirected into another account's private directory. Resolution
    does not create, migrate, chmod or otherwise adopt any existing data.
    """
    raw = os.environ.get('HIGAN_ROOT')
    if raw is not None:
        if not raw.strip() or any(ord(char) < 32 for char in raw):
            raise ValueError('HIGAN_ROOT must be a nonempty absolute data path')
        path = Path(raw).expanduser()
        if not path.is_absolute():
            raise ValueError('HIGAN_ROOT must be an absolute data path')
        return path
    return Path.home() / 'higan'
