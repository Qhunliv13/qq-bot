"""Optional per-turn schema exposure; never a dispatcher or permission boundary.

The caller retains the full implementation registry and supplies actual discovery
results after dispatch. This module neither executes tools nor interprets intent.
"""

from copy import deepcopy
from typing import Any


# Existing protocol helpers, not inferred capabilities or alternative schemas.
_DISCOVERY = frozenset({"tool_search", "tool_describe"})
_INITIAL = _DISCOVERY | {"reply_now"}


class ToolProfile:
    """Own a detached catalog snapshot and monotonically expose exact schemas.

    Create one instance per turn. Full is the default and preserves catalog order
    and content. Light requires discovery and only changes model-facing exposure.
    """

    def __init__(self, schemas: list[dict[str, Any]], mode: str = "full",
                 initial_tools: list[str] | None = None):
        # Reject configuration errors before any caller can make a model request.
        if not isinstance(mode, str) or mode not in ("full", "light"):
            raise ValueError("mode must be 'full' or 'light'")
        if not isinstance(schemas, list):
            raise ValueError("schemas must be a list of named function schemas")
        self._catalog = deepcopy(schemas)
        self._by_name = {}
        for schema in self._catalog:
            function = schema.get("function") if isinstance(schema, dict) else None
            name = function.get("name") if isinstance(function, dict) else None
            if (not isinstance(name, str) or not name
                    or schema.get("type") != "function"):
                raise ValueError("each schema must be a named function schema")
            # Never silently choose one of two competing authoritative definitions.
            if name in self._by_name:
                raise ValueError("duplicate tool name: " + name)
            self._by_name[name] = schema
        if initial_tools is None:
            initial_tools = []
        if (not isinstance(initial_tools, list)
                or any(not isinstance(name, str) or name not in self._by_name
                       for name in initial_tools)):
            raise ValueError("initial_tools must list actual catalog names")
        if mode == "light" and not _DISCOVERY.issubset(self._by_name):
            raise ValueError("light mode requires tool_search and tool_describe")
        self._mode = mode
        selected = set(self._by_name) if mode == "full" else _INITIAL | set(initial_tools)
        # Catalog iteration, not set iteration, establishes deterministic ordering.
        self._exposed = [name for name in self._by_name if name in selected]
        self._seen = set(self._exposed)

    def schemas(self) -> list[dict[str, Any]]:
        """Return detached exposed schemas; callers cannot mutate future requests."""
        return deepcopy([self._by_name[name] for name in self._exposed])

    def full_catalog(self) -> list[dict[str, Any]]:
        """Return the complete detached catalog for discovery, never an allowlist."""
        return deepcopy(self._catalog)

    def observe(self, name: str, args: Any, result: Any) -> bool:
        """Promote requested native schemas after an actual successful description.

        The caller must pass the actual decoded dispatch result, not model text.
        Only its literal ``ok=True`` is a success signal. Result-provided names
        and schemas are never selection inputs. Invalid observations are no-ops;
        the return value reports whether the exposed list grew, not permission.
        """
        if (self._mode != "light" or name != "tool_describe"
                or not isinstance(result, dict) or result.get("ok") is not True
                or result.get("error")
                or not isinstance(args, dict) or set(args) != {"names"}):
            return False
        requested = args["names"]
        # Match the existing descriptor's argument shape and reject the entire
        # observation if even one name is invalid; no partial promotion on failure.
        if (not isinstance(requested, list) or not 1 <= len(requested) <= 100
                or any(not isinstance(item, str) or item not in self._by_name
                       for item in requested)):
            return False
        selected = set(requested)
        added = [item for item in self._by_name
                 if item in selected and item not in self._seen]
        # Append only new entries: earlier schemas stay a stable prefix forever.
        self._exposed.extend(added)
        self._seen.update(added)
        return bool(added)
