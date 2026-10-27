"""Offline transport building blocks, not an implemented or verified QQ backend.

No backend, login, connection, or handlers are installed on package import.
Callers must explicitly inject native handlers and a durable operation journal.
"""

from .actions import ActionDispatcher, Handler

__all__ = ["ActionDispatcher", "Handler"]
