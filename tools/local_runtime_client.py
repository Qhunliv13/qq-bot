"""Authenticated local JSONL client; no QQ login or model configuration."""
import argparse
import json
import os
from pathlib import Path
import socket
import sys

# Direct script execution must locate the one shared transport/security implementation.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugin.local_runtime import MAX_REQUEST_BYTES, read_windows_endpoint, windows_instance_hash


def _authenticated_request(payload, token):
    """Keep the native authentication envelope identical for validation and exchange."""
    return {'token': token, 'request': payload}


def encode_request(payload):
    """Encode the actual JSONL wire bytes and enforce the shared server request bound."""
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
    if len(encoded) > MAX_REQUEST_BYTES:
        raise ValueError('request_too_large')
    return encoded


def validate_request_size(payload, *, windows=False):
    """Check exact wire size offline, including native credentials and JSON escaping.

    Endpoint validation requires a 64-character hexadecimal token, so an equally
    sized ASCII placeholder has identical JSON/UTF-8 cost without reading secrets.
    The caller chooses the target transport, not the operating system of its UI.
    """
    document = _authenticated_request(payload, '0' * 64) if windows else payload
    encode_request(document)


def _exchange(address, payload, *, timeout, windows=False):
    """Send exactly once, applying the same wire bounds to both local transports."""
    encoded = encode_request(payload)
    family = socket.AF_INET if windows else socket.AF_UNIX
    with socket.socket(family, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(address if windows else str(address))
        connection.sendall(encoded)
        # Bound a single response, while allowing an actual model turn to finish.
        with connection.makefile('rb') as reader:
            response = reader.readline(8 * 1024 * 1024 + 1)
    if not response.endswith(b'\n') or len(response) > 8 * 1024 * 1024:
        raise ValueError('invalid_local_response')
    value = json.loads(response)
    if not isinstance(value, dict):
        raise ValueError('invalid_local_response')
    return value


def request(path, payload, *, timeout=None):
    """Discover Windows credentials privately, prove identity, then execute at most once.

    Keep the Unix request(path, payload) API. On Windows path accepts the instance
    root, its run/local.sock alias, or the explicit run/local-endpoint.json locator.
    """
    if sys.platform != 'win32' and os.name != 'nt':
        return _exchange(path, payload, timeout=timeout)
    endpoint = read_windows_endpoint(path)
    address = ('127.0.0.1', endpoint['port'])
    health = _exchange(address, _authenticated_request(
        {'op': 'health', 'nonce': endpoint['nonce']}, endpoint['token']),
        timeout=2.0, windows=True)
    if (health.get('ok') is not True or health.get('transport') != 'local'
            or health.get('pid') != endpoint['pid'] or health.get('nonce') != endpoint['nonce']
            or health.get('instance_hash') != windows_instance_hash(endpoint['instance'])):
        raise ValueError('local_instance_mismatch')
    if payload == {'op': 'health'}:
        return health
    return _exchange(address, _authenticated_request(payload, endpoint['token']),
                     timeout=timeout, windows=True)


def main(argv=None):
    """Use the deployed same-instance endpoint without opening a public listener."""
    parser = argparse.ArgumentParser(description=__doc__)
    default = (Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / 'QhunlivBot' / 'Full' / 'higan'
               if sys.platform == 'win32' or os.name == 'nt' else Path('/root/higan/run/local.sock'))
    parser.add_argument('--socket', '--endpoint', type=Path, default=default)
    command = parser.add_mutually_exclusive_group(required=True)
    command.add_argument('--health', action='store_true')
    command.add_argument('--history', action='store_true')
    command.add_argument('--prompt')
    parser.add_argument('--limit', type=int, default=50)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        configure = getattr(stream, 'reconfigure', None)
        if callable(configure):
            configure(encoding='utf-8')
    payload = ({'op': 'health'} if args.health else
               {'op': 'history', 'limit': args.limit} if args.history else
               {'op': 'run', 'prompt': args.prompt})
    try:
        result = request(args.socket, payload, timeout=10 if args.health or args.history else None)
    except (OSError, ValueError, RuntimeError):
        print('local bot request failed; do not replay an unknown run automatically', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
