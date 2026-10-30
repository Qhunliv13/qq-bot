"""First-install loopback port selection; never stop an existing service to free a port."""
from contextlib import contextmanager
import socket


@contextmanager
def first_install_ports(web_port, onebot_port, *, web_explicit=False, onebot_explicit=False):
    """Hold both local TCP bindings until private configuration has been committed.

    Defaults may choose an OS-allocated free port if busy. Explicit selections must
    fail, not silently change. These sockets do not listen, connect or send data.
    A later Docker start can still race another process; no availability guarantee
    or host firewall/service modification is implied.
    """
    held = []
    selected = {}
    choices = [("web_port", web_port, web_explicit), ("onebot_port", onebot_port, onebot_explicit)]
    # Bind explicit requests first so an ephemeral fallback cannot occupy them.
    choices.sort(key=lambda item: not item[2])
    try:
        for name, preferred, explicit in choices:
            handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            held.append(handle)
            if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
                handle.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            try:
                handle.bind(('127.0.0.1', preferred))
            except OSError as error:
                if explicit:
                    raise ValueError('An explicitly selected host port is unavailable; select a free port or release it yourself. No existing service was stopped.') from error
                handle.bind(('127.0.0.1', 0))
            selected[name] = handle.getsockname()[1]
        yield selected
    finally:
        for handle in held:
            handle.close()
