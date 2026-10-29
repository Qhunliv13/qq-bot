"""Trusted execution-start deadline, separate from every model-facing action schema."""

from dataclasses import dataclass
import math
from typing import Callable


@dataclass(frozen=True)
class ExecutionLease:
    """Carry the original deadline and its clock together; never renew on handoff."""

    deadline: float
    clock: Callable[[], float]

    def __post_init__(self):
        """Reject malformed trusted configuration rather than permit unbounded input."""
        if (type(self.deadline) not in (int, float) or not math.isfinite(self.deadline)
                or not callable(self.clock)):
            raise ValueError("invalid_execution_lease")

    def check(self):
        """Authorize start only, using the issuing gate's monotonic clock origin."""
        now = self.clock()
        if (type(now) not in (int, float) or not math.isfinite(now)
                or now >= self.deadline):
            raise RuntimeError("execution_lease_expired")
