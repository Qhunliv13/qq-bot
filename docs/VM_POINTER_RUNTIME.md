# Deterministic pointer execution primitive — not deployed

## Why the legacy drag route remains blocked

A retained private VM source snapshot from 2026-10-28 was inspected read-only.
Its hold-drag route adds random coordinate offsets and sleeps and does not check
each command's exit status. This is historical source evidence, not a current
production hash verification. No private source was copied into this repository.
A preview of an exact path cannot truthfully authorize that implementation as
execution of the same path. Existing direct drag denials remain unchanged.

## Implemented primitive

[vm_pointer_runtime.py](../plugin/vm_pointer_runtime.py) supplies one pure,
injected-capability implementation; it registers no HTTP route, model tool or
startup hook and has no default process runner.

The exact version-1 action fields are `version`, `button`, `width`, `height`, and
`points`. Only the left button is supported. Each point has exact integer `x`,
`y`, and `t_ms` fields, copied into immutable records before any side effect.
Unknown fields, booleans as numbers, invalid dimensions, off-screen points,
non-increasing times and excessive duration/point count are rejected.

Bounds: 2–128 points, first time zero, final time at most 10 seconds, dimensions
at most 16384 each and total area at most 16 Mi pixels. Equal positions at
successive times represent a stationary hold without random jitter.

The trusted runtime injects `runner(argv, timeout_s=...)`, `sleep` and a monotonic
`clock`. The runner must execute without a shell, synchronously wait and enforce
the timeout. Only an exact integer zero is accepted as command acknowledgment.
Every movement uses explicit coordinates; scheduling starts after the press
command completes. Non-finite/reversed clocks, premature sleepers and movements
more than 250 ms late abort rather than rapidly replaying overdue points.
Movement timeouts are bounded by the remaining timing allowance, with a maximum
of one second per command. The 250 ms allowance is explicit version-1 runtime
policy, not a guarantee of real-time OS scheduling.

Any attempted button-down, including an exception or uncertain receipt, causes
one button-up attempt in `finally`. There are no action retries. Failed movement,
press or release returns a fixed uncertain outcome without raw exception data.
A failed release can still leave a physical button down: this implementation
reports uncertainty, not a guaranteed recovery. Plain `mousemove` deliberately
avoids `--sync`, which may wait indefinitely when a stationary point does not
move the pointer. Process success does not prove physical cursor position.

## Required integration before use

This component **does not perform visual authorization**. A trusted exclusive GUI
owner must bind the complete immutable action to the marked preview, verify that
the model received the image in an earlier request, consume an exact single-use
ticket, check freshness and screenshot pixels, then hold its GUI lock through
execution. The existing [click gate](../plugin/vm_click_gate.py) was not weakened
or replaced. The shared path-preview/ticket boundary is now implemented as described
below; production adapter and deployment integration remain pending.

The actual VM adapter, command timeout implementation, GUI capability separation,
service deployment and real-desktop acceptance are not implemented or verified
by this module. Raw execution/browser evaluation remains outside the complete
mediation boundary described in [the checkpoint document](VM_CLICK_CHECKPOINT_20261028.md).
No production route was enabled and no real pointer commands were executed.

## Verification scope

[Tests](../tests/test_vm_pointer_runtime.py) inject command, sleep and clock
capabilities. They check immutable copying, exact argv, strict rejection before
side effects, timeout propagation, scheduling, release on failed press/movement,
uncertain release, no retry and private-error redaction. These are offline
component tests, not end-to-end desktop or permission-isolation acceptance.

## Shared gate boundary (native model tools, backend unavailable by default)

`ClickGate.preview_path(action, label, screenshot_callable)` and
`confirm_path(ticket, screenshot_callable, path_callable, *, action,
screen_sha256, viewed)` use the same preview/render/store and confirmation,
consumption, image validation, observation flag, TTL and lock as the existing
click API. There is exactly one pending slot: every new preview, including an
invalid one, revokes any earlier click or path ticket. Wrong-kind confirmation
also consumes the ticket. Existing click arguments, receipts and errors remain
compatible.

The runtime's `canonical_pointer_action` is the only path schema validator. Its
immutable action and point tuples are copied before screenshot awaits. Dimensions
must exactly equal decoded screenshot dimensions. Confirmation must canonicalize
to the complete stored record, with exact integers (not booleans or floats), the
same ticket and pixel digest, and `viewed=True`; the existing `mark_observed(path)`
must already have recorded model image delivery. Neither label text nor merely
returning an image path constitutes observation or authorization.

The preview preserves screenshot dimensions and paints the complete polyline,
numbered point timestamps, green start and blue end markers. Numbered time order
indicates direction. A bounded, complete ordered legend and detached structured
action accompany the PNG, since point text can overlap or clip on small images
or dense paths. Mutating that response cannot change the stored action. The
original screenshot is never painted. Timing denotes scheduled offsets after
button-down, not measured physical motion.

After consuming and validating, the gate captures fresh pixels and rechecks TTL
**after** capture, then passes only the stored immutable record to the trusted
path callback while retaining its lock. Callback exceptions/failed receipts
return `path_outcome_uncertain`, without raw failure details or replay permission.
A test-only trusted callback converts the record to a wire dict and calls the
actual `execute_pointer_path`, which revalidates through the same schema. No
second execution implementation or production adapter was introduced.

[Shared gate tests](../tests/test_vm_pointer_gate.py) exercise the full gate to
actual primitive with a fake command runner: positive exact argv, immutable
binding, mutations during capture, altered path/times/button/types, dimension
mismatch, missing observation, expiry during capture, changed pixels, replay,
concurrency, mixed kinds and uncertain callbacks. These tests launch no commands
and confer no production capability.

The model catalog now exposes `vm_path_preview(action, label)` and
`vm_path_confirm(ticket, action, screen_sha256, viewed)` through the native loop.
Both reuse that turn's existing `ClickGate`, so click/path previews automatically
revoke each other. Marked PNGs join the same visual references as click previews;
observation is recorded only after a successful model request actually contains
the marker's `image_url`. Same-request preview/confirm batches cannot claim vision.
Direct dispatcher and VM-tool calls to either path name return the exact
`preview_and_confirm_required` denial, even with the click-only verified flag.
No HTTP route, environment endpoint or automatic runtime adapter was added.

The plugin initializes its explicit private trusted-host capability
`_vm_pointer_executor` to `None`. A reviewed host adapter may supply an async
callable taking only the gate's stored immutable canonical action and returning a
structured receipt. It is never resolved from model arguments, URLs or inferred
attributes; no such adapter is installed by default. Missing capability passes
`None` into the existing gate: a correct, observed confirmation consumes its ticket
and returns `backend_unavailable` **before** fresh screenshot or pointer execution.
This known pre-execution condition is not mislabeled `path_outcome_uncertain`;
replaying the consumed ticket returns `no_pending_preview`. Catalog/tool discovery
descriptions explicitly disclose this default, not a claim of live path support.

[Native-loop tests](../tests/test_vm_path_integration.py) transport real marked PNG
bytes through provider-message fixtures, then gate confirmation into the actual
pointer primitive using a trusted fake command adapter. They cover exact argv,
immutable callback input, missing vision, altered action, changed screen, same
request batches, replay, unconfigured backend, both cross-kind revocation orders,
and direct bypass denial. These tests do not execute real pointer commands.

Controlled deployment still requires an actual VM baseline, trusted command
timeout enforcement and one exclusive GUI owner across **all** input channels. The gate lock is turn-local, not a process-wide desktop lock. TTL is
checked at execution admission, not continuously during the up-to-10-second path.
Physical screen changes after the final capture and OS scheduling remain races;
this boundary does not claim physical atomicity, user consent, or complete
mediation of raw execution/browser evaluation.
