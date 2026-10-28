"""Thin standalone harness and routing contracts; importing starts no service.

The harness reuses the existing injected tool loop. No QQ login, framework,
configuration read, network request or background task starts on import.
Identity/authorization and tool availability remain the explicit host's responsibility.
"""

from .contracts import Endpoint, EndpointKind, EventOrigin, InboundEvent, Principal
from .harness import Harness, RunResult

__all__ = ["Endpoint", "EndpointKind", "EventOrigin", "InboundEvent", "Principal", "Harness", "RunResult"]
