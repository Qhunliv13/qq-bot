# Optional tool-schema exposure profiles

## Scope and defaults

[`ToolProfile`](../plugin/tool_profile.py) changes **only the schemas sent to the
model**. The default is `full`, including in production: every native schema is
returned in the same order and with identical content, as a detached deep copy.
No tool implementation, authorization, dispatch path, history, tool result, or
user data is removed or rewritten. This module does not read settings or change
production configuration; callers explicitly select the mode per instance.

`light` is opt-in lazy schema exposure, **not** text-only mode. It starts with the
existing `tool_search`, `tool_describe`, and (when registered) `reply_now`, plus
operator-provided `initial_tools`. These are generic protocol helper names, not
business-specific selection rules. It never fabricates missing capabilities.
Both discovery helpers must exist in the supplied catalog or construction fails.

All implementations and the **complete catalog** must remain available to the
existing dispatcher/discovery. Hidden schema does not mean disabled tool, and an
exposed schema never grants permission. Existing authorization remains mandatory.

## Enable for a separate installation

The QQ plugin reads this optional field from the existing external file selected
by `HIGAN_SETTINGS_FILE`, once per turn:

```json
"tool_profile": {"mode": "light", "initial_tools": []}
```

Omit the field, or set `"mode": "full"`, to retain the complete tool catalog.
The current production deployment is intentionally kept on **full**; no live
settings were modified. This option does not disable task scheduling, memory,
images or tool execution. It reduces initial schema payload only, not the full
runtime's installed dependencies or memory footprint. For standalone text-only
use see [Lite](LITE.md); that separate client is not a full-feature substitute.

The real QQ loop now passes exposed schemas only in light mode, observes actual
`tool_describe` results, and leaves complete discovery and execution unchanged.
The [integration tests](../tests/test_tool_profile_integration.py) verify a real
HTTP-boundary tool-describe → tool execution sequence and default full equality.

## API and caller integration contract

```python
profile = ToolProfile(full_schemas, mode="full", initial_tools=None)
# Explicit alternative: mode="light", initial_tools=["an_actual_catalog_name"]

# Each model request in this turn:
tools = profile.schemas()

# Existing dispatch must still use the FULL registry/catalog and existing auth:
result = await existing_dispatch(name, arguments)
# Observe only the actual decoded result, after execution, not model-authored text:
changed = profile.observe(name, arguments, result)
# Next request obtains profile.schemas() again to include promoted native schemas.
```

The snippet is an integration contract, not another dispatcher implementation.
The host owns main-loop wiring; merely importing this module enables nothing.
Create a fresh profile for each turn and use that same instance for its successive
model requests. Keep the complete catalog in the parent or obtain a detached copy
with `profile.full_catalog()`; never replace discovery's catalog or the execution
registry with `profile.schemas()`.

- `ToolProfile(schemas, mode='full', initial_tools=None)` snapshots a list of named
  OpenAI function schemas. Invalid modes, malformed catalog entries, duplicate
  catalog names, and unknown/non-list initial selections raise `ValueError`
  immediately, before model calls. Repeated initial names are deduplicated.
  Explicit initial selections are validated even in full mode.
- `schemas()` returns a detached deep copy of the currently exposed schemas.
- `full_catalog()` returns a detached deep copy of the complete snapshot.
- `observe(name, args, result) -> bool` reports whether exposure grew. Only
  `tool_describe`, a decoded dictionary with literal `ok is True` and no nonempty
  `error`, and valid `args` containing only `names` can promote. Names must be a
  list of 1–100 strings, all in the actual snapshot. Invalid observations are
  no-ops, including mixed known/unknown names; no partial promotion occurs.
  Full mode is always a no-op.

The caller is responsible for supplying the actual dispatcher result: this module
cannot attest the provenance of an arbitrary Python dictionary. It treats only
`ok`/`error` as outcome signals and **never** selects names or copies schemas from
result payloads. Requested catalog names are the sole promotion source. Thus
malicious descriptor metadata cannot inject or replace a schema.

Initial selection follows original catalog order. Each successful description
appends only previously unexposed requested schemas, in original catalog order
**within that new batch**. Existing entries remain an unchanged prefix; there is
no global re-sort, eviction, or shortening during the turn. Repeated descriptions
are idempotent. Names with identical parameter schemas remain distinct, with
unaltered names, descriptions, parameters, and metadata.

## Honest tradeoffs

Light reduces the number of schema descriptors in early requests (the isolated
106-entry fixture begins with 3), not the available functionality or stored data.
The model may need additional search/describe rounds before invoking a native
tool. Discovery adds tokens and round trips, can affect model selection behavior,
and can increase total latency. There is **no latency guarantee**, no claim of
identical model decisions, and no guaranteed end-to-end token saving. A turn that
uses many tools can eventually expose the full catalog.

No history shortening, cache summary, semantic tool router, implementation
copying, automatic permission grants, or production/native changes are part of
this feature. Existing text-only launch modes are not functional substitutes.

## Verification

Run `python -m pytest tests/test_tool_profile.py tests/test_tool_discovery.py -q`.
The [profile tests](../tests/test_tool_profile.py) cover byte-equivalent full-mode
JSON, a 3-of-106 initial exposure fixture, actual discovery outcomes, invalid and
failed observations, immutable copies, unchanged same-parameter native schemas,
monotonic prefix ordering, duplicate validation, unknown names, and untrusted
result schema injection. Tests are offline and do not import the bot entry point.
