"""Real local TCP bindings prove first-install defaults avoid existing listeners safely."""
import socket

import pytest

from tools.setup_ports import first_install_ports


def occupied_socket():
    """Reserve an OS-selected loopback port without connecting to any external host."""
    handle = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if hasattr(socket, 'SO_EXCLUSIVEADDRUSE'):
        handle.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    handle.bind(('127.0.0.1', 0))
    handle.listen(1)
    return handle


def test_busy_defaults_select_two_distinct_free_ports_without_stopping_listener():
    """A clean installation may pick another port instead of requiring manual edits."""
    with occupied_socket() as first, occupied_socket() as second:
        occupied = [first.getsockname()[1], second.getsockname()[1]]
        with first_install_ports(*occupied) as chosen:
            assert len(set(chosen.values())) == 2
            assert not set(occupied) & set(chosen.values())
            assert first.getsockname()[1] == occupied[0]
            for port in chosen.values():
                with socket.socket() as probe:
                    with pytest.raises(OSError):
                        probe.bind(('127.0.0.1', port))
        assert first.fileno() >= 0 and second.fileno() >= 0


def test_explicit_occupied_port_is_not_silently_changed():
    """Explicit configuration is a contract, not permission to change another service."""
    with occupied_socket() as first, occupied_socket() as second:
        with pytest.raises(ValueError, match='explicitly selected host port'):
            with first_install_ports(first.getsockname()[1], second.getsockname()[1], web_explicit=True):
                pytest.fail('occupied explicit port must fail')
        assert first.fileno() >= 0


def test_partial_reservation_closes_on_explicit_failure():
    """No socket reservation survives a failed initialization attempt."""
    with occupied_socket() as busy:
        with socket.socket() as temporary:
            temporary.bind(('127.0.0.1', 0))
            free = temporary.getsockname()[1]
        with pytest.raises(ValueError):
            with first_install_ports(free, busy.getsockname()[1], onebot_explicit=True):
                pytest.fail('must not yield')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', free))


def test_free_explicit_ports_preserved_and_released():
    """Chosen ports are held only for initialization and remain exactly as requested."""
    with socket.socket() as first, socket.socket() as second:
        first.bind(('127.0.0.1', 0))
        second.bind(('127.0.0.1', 0))
        ports = first.getsockname()[1], second.getsockname()[1]
    with first_install_ports(*ports, web_explicit=True, onebot_explicit=True) as chosen:
        assert chosen == {'web_port': ports[0], 'onebot_port': ports[1]}
    for port in ports:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
