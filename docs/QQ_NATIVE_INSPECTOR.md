# Fixed private QQ native inspector experiment

## Scope and safety

This independently authored client is an **opt-in lab experiment**, not a QQ transport,
login implementation or production readiness check. It does not import NapCat, copy a
third-party loader, load QQ main, invoke an exported native method, read an account home,
perform login or connect to a public network. The caller is responsible for pristine
Tencent software provenance. No production action is authorized by this document.

The trusted supervisor may copy `tools/qq_private_inspector.js` read-only into the fresh
private root as `/tmp/qq-private-inspector.js`. The sole command is:

```text
/usr/bin/node /tmp/qq-private-inspector.js
```

There are **no CLI target, expression or script options**; any extra argument is refused.
The executable must actually report Linux, Node 18 and `process.execPath=/usr/bin/node`.
The client loads only the WSL-verified system `require('undici').WebSocket`, requiring
`require.resolve('undici')` to equal `/usr/share/nodejs/undici/index.js`. No package install
or downloaded WebSocket library is needed. A different resolution fails closed instead
of searching for a substitute. `http`, `fs` and `url` are Node built-ins.

## Required external isolation

The existing trusted Python supervisor—not this client—must establish and verify the
full boundary before launching either QQ or this child:

- Fresh private network, mount, PID and IPC namespaces, compared with the outer parent.
- Detached private filesystem root, read-only pristine software and system libraries,
  fresh bounded temporary filesystems/proc, and no account homes, production mounts,
  inherited runtime sockets or host display/DBus sockets.
- Only private authenticated lab display/services, if required by the pristine QQ build.
- UID/GID 65534, dropped supplementary groups and capabilities, no-new-privileges,
  disabled core dumps, resource/output limits and an outer wall-clock watchdog that
  terminates the whole namespace, including QQ and services.
- A newly constructed environment, with no inherited tokens, proxies, Node hooks,
  loader options or credentials. The inspected QQ process must have the same boundary.
- Bind the QQ inspector **only** to `127.0.0.1:9229` inside the private network namespace.
  The parent must decide whether the real QQ build supports that entry; no successful
  inspector availability is assumed or manufactured by this client.

Set exactly these inspector-related environment values in that clean child environment:

| Name | Meaning |
| --- | --- |
| `QQ_ISOLATED_NATIVE_INSPECTOR=1` | Explicit supervisor opt-in; not isolation proof. |
| `QQ_INSPECTOR_PARENT_NETNS=net:[digits]` | Outer parent network namespace readlink, obtained **before** entering the private namespace. Never returned publicly. |
| `NODE_NO_WARNINGS=1` | Suppress Node18's experimental-WebSocket warning, not failure evidence. |

Before networking, the client reads `/proc/self/status` and checks all four UID and GID
fields are 65534, `NoNewPrivs=1`, and `CapEff=0`. It reads `/proc/self/ns/net`, requires a
well-formed namespace identifier different from the supplied outer identifier, and
refuses nonempty `NODE_OPTIONS`, `NODE_PATH`, `LD_PRELOAD` or `LD_LIBRARY_PATH`.
These local observations cannot prove mount/PID/IPC isolation, provenance, QQ privileges,
absence of host resources, or the truth of a caller-supplied namespace identifier.
**Do not turn a child environment gate into an affirmative full-isolation claim.**
Boundary verification belongs in the outer supervisor's independently validated report.
The child needs up to 5 seconds discovery plus 5 seconds WebSocket time; allow a small
shutdown overhead in the outer deadline without removing the finite watchdog.

## Fixed discovery and protocol

The only HTTP request is built-in `http` GET `127.0.0.1:9229/json/list`, with no proxy,
agent reuse, redirect following or user-supplied path. Discovery has a shared 5-second
budget, at most 25 attempts with bounded retry delays, 8-KiB HTTP headers and **64 KiB
aggregate body bytes across all attempts**. Non-200 responses/network failures may
retry; malformed or ambiguous successful JSON fails closed.

A successful discovery must contain exactly one target with a matching `id` and
`webSocketDebuggerUrl`. The URL must literally match:

```text
ws://127.0.0.1:9229/[A-Za-z0-9-]{1,128}
```

Only a single allowlisted identifier segment is accepted, including real inspector UUIDs.
No userinfo, query, empty query, hash, empty hash, escaping, normalization shortcut,
localhost/DNS hostname, alternate IP spelling, second path segment, IPv6 or alternate
port is accepted. The raw endpoint, target metadata and HTTP body are never printed.

WebSocket work is limited to **5 seconds**, **64 messages**, **64 KiB per message** and
**128 KiB aggregate message text**. Binary messages, malformed responses and unexpected
response IDs fail closed; unsolicited protocol events are ignored but consume all
budgets. `undici` assembles a message before delivering it, so JavaScript-level bounds
are not a pre-allocation memory firewall. External memory limits remain mandatory.

The only command is `Runtime.evaluate`, at most twice, with constant closed strict
expressions, `returnByValue=true`, `includeCommandLineAPI=false`, no user gesture,
no awaited promise, silent evaluation and a 1000-ms evaluation timeout:

1. Return only allowlisted `process.versions` descriptors (`node`, `electron`, `modules`,
   `napi`, `chrome`, `v8`, `uv`) and whether `process.execPath === '/opt/QQ/qq'`.
   Node, Electron, module ABI and N-API metadata must be present and well formed before
   any load is attempted. Strings are bounded to 64 characters; ABI/N-API are numeric.
   The actual executable path, environment, arguments and process identifiers are not
   returned.
2. Recheck the exact QQ executable path, then execute **only** this load operation:

   ```javascript
   const native={exports:{}};
   process.dlopen(native,'/opt/QQ/resources/app/wrapper.node');
   ```

   Enumerate own-property names/descriptors of `native.exports`; do not evaluate a
   getter, instantiate a class, call an exported method, create/configure/connect a
   session, or invoke login. No global `require`, `process.mainModule`, application
   entry or NapCat dependency is used. The load itself can run native initialization;
   it is **not** side-effect-free and must only run inside the externally verified lab.

There is only **one** `dlopen` attempt. The application may have already loaded the same
module, so this is potentially an additional load/initialization; this client cannot
prove otherwise. It does not probe the module cache, retry a failed load, call cleanup,
or infer that repeated loading is safe. A process crash/timeout remains a failed gate,
not permission to weaken isolation or switch to a guessed loader.

At most 64 export descriptors are returned. Names must match
`[A-Za-z_$][A-Za-z0-9_$]{0,95}`; disallowed/excess names set `truncated=true` rather than
being leaked. `type` is JavaScript `typeof` for a data descriptor, or `accessor` for an
accessor descriptor. No values, symbols, descriptor getter/setter functions or native
exception text are returned. Candidate booleans mean an **own data descriptor whose
value has type function**, without invoking it:

- `NodeIQQNTWrapperEngine`
- `NodeIKernelLoginService`
- `NodeIKernelWrapperSession`
- `NodeIQQNTWrapperSession`
- `NodeIQQNTStartupSessionWrapper`

Candidate spelling is not a claimed API contract. Absence, a different type, or an
unknown export does not authorize guessing a replacement method or session lifecycle.

## Public marker schema

Stdout is exactly one UTF-8 line: `QQ_NATIVE_INSPECTOR:` immediately followed by JSON and
one newline. JSON is capped at **16 KiB**; an oversized report becomes a minimal fixed
`output_limit` report. CLI exit status is 0 only when both observations succeed, otherwise
1. No stack, raw error message, endpoint, payload, account identifier or private text is
printed. Under the trusted clean launch, warnings are suppressed before lazy undici load.
The parent must additionally bound/redact **QQ's own** stdout/stderr independently.

The following illustrative schema is not actual runtime evidence:

```json
{
  "schema_version": 1,
  "ok": false,
  "error": "environment_refused",
  "runtime": {"versions": {}, "exec_path_matches": false},
  "binding": {
    "load_attempted": false,
    "loaded": false,
    "exports": [],
    "truncated": false,
    "candidates": {
      "NodeIQQNTWrapperEngine": false,
      "NodeIKernelLoginService": false,
      "NodeIKernelWrapperSession": false,
      "NodeIQQNTWrapperSession": false,
      "NodeIQQNTStartupSessionWrapper": false
    }
  },
  "readiness": {
    "runtime_host_verified": false,
    "native_binding_verified": false,
    "session_initialized": false,
    "safe_to_login": false,
    "independent_backend_ready": false,
    "production_cutover_ready": false
  }
}
```

A successful `exports` entry has exactly `{ "name": "ExportName", "type": "function",
"kind": "data" }`; accessor entries use `type=accessor, kind=accessor`. Successful
`error` is null. A verified host can remain true when the later native load fails.
`native_binding_verified` means only the fixed QQ-host `dlopen` returned and bounded
metadata passed validation. It does not prove usable methods, native compatibility in
a different host, native construction, initialization, a session, network/login access,
180 actions/events, independent-backend readiness or safe cutover. **Every readiness
field except the two directly observed host/load flags is permanently false.**

Error values are fixed categories only: `environment_refused`, `discovery_timeout`,
`discovery_limit`, `discovery_invalid`, `socket_unavailable`, `socket_timeout`,
`socket_closed`, `socket_error`, `message_limit`, `protocol_invalid`,
`evaluation_failed`, `runtime_mismatch`, `binding_load_failed`, `binding_invalid`,
`internal_error`, `output_limit`. Load errors never include a raw message or symbol.
The parent must validate the marker, types, bounds, fields and readiness independently;
raw inspector responses are not an acceptable public evidence substitute.

## Tests and evidence limits

Run `python -m pytest tests/test_qq_private_inspector.py -q` and
`node --check tools/qq_private_inspector.js`. The Python tests save temporary JS harnesses
and run Node with fake HTTP/WebSocket transports and VM-only fake `process.dlopen`.
Requiring the module exposes test helpers but does not run its CLI, open sockets, start
QQ or load a native library. The only CLI exercise deliberately fails the environment
gate. Tests cover fixed URL validation, environment prerequisites, discovery/transport
budgets, constant evaluations, output schema/redaction, malformed metadata, descriptor
handling and unchanged session/login/backend/cutover flags.

Passing mocks is not real runtime evidence. Real execution and its externally verified
boundary are handled by the parent harness, not performed by these tests. No production
readiness, isolation certification or successful native load is claimed in this document.
