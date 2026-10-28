"""Transport-independent offline contract prototype; importing starts no runtime.

These exports do not extract the current execution engine or install adapters.
No QQ, framework, environment, network, filesystem, or subprocess initialization
is performed. See ``contracts`` for the explicit authentication trust boundary.
"""

from .contracts import Endpoint, EndpointKind, EventOrigin, InboundEvent, Principal

__all__ = ["Endpoint", "EndpointKind", "EventOrigin", "InboundEvent", "Principal"]
