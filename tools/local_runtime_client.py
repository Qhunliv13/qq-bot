"""Root-local JSONL client for the existing bot instance; no QQ login or model config."""
import argparse
import json
from pathlib import Path
import socket
import sys


def request(path, payload, *, timeout=None):
    """Send one authorized local request once; never replay ambiguous execution."""
    encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode('utf-8') + b'\n'
    if len(encoded) > 16 * 1024:
        raise ValueError('request_too_large')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(timeout)
        connection.connect(str(path))
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


def main(argv=None):
    """Use the deployed same-instance endpoint without opening a public listener."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', type=Path, default=Path('/root/higan/run/local.sock'))
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
    except (OSError, ValueError):
        print('local bot request failed; do not replay an unknown run automatically', file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get('ok') is True else 1


if __name__ == '__main__':
    raise SystemExit(main())
