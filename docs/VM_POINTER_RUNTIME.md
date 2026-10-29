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

An opt-in command adapter and deadline implementation are now supplied below.
GUI capability separation, service deployment and real-desktop acceptance remain
unimplemented/unverified. Raw execution/browser evaluation remains outside the complete
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
second path execution implementation was introduced; the opt-in transport below
now provides a host callback without installing it.

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
structured receipt. An adapter may explicitly implement `execute_authorized(action,
*, lease)`; the native loop passes that trusted method through the gate's optional
`authorized_path_callable` argument. No capability is resolved from model arguments
or URLs, and no adapter is installed by default. Missing capability passes
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

Controlled deployment still requires an actual VM baseline, host installation of
the trusted transport and one exclusive GUI owner across **all** input channels. The gate lock is turn-local, not a process-wide desktop lock. TTL is
checked at execution admission, not continuously during the up-to-10-second path.
Physical screen changes after the final capture and OS scheduling remain races;
this boundary does not claim physical atomicity, user consent, or complete
mediation of raw execution/browser evaluation.

## Worker-queue deadline handoff

The authorized callback receives a frozen `ExecutionLease` containing the original
preview expiry and the issuing gate's monotonic clock callable. The lease is not an
action-schema field, cannot be supplied by the model, and never renews TTL after
model observation, confirmation capture, or thread submission. `VmPointerAdapter`
passes its check through the runtime's optional trusted `before_start` hook, which
runs **inside the worker immediately before the first mousemove**, after executor
queue delay and runtime validation. An expired lease produces no input, including
no mouseup when no press was attempted; gate errors remain sanitized and tickets
remain consumed. Once input starts, lease expiry does not suppress safety release.
Cancellation still drains the worker while holding the adapter and gate locks.

Legacy action-only callbacks and standalone adapter `__call__` retain their APIs;
they do not gain worker-queue freshness automatically. There is no TypeError-based
signature fallback. The installed adapter's explicit authorized method is selected
by the native loop; the default executor remains `None`.

This bounds admission age, **not screen ownership**: there is no new worker-side
screenshot or atomic screenshot/input transaction. Screen changes, other turns,
raw execution, other adapters, and scheduling/filesystem/process-launch delays
after the check remain outside this turn-local lock. An exclusive global GUI owner
is still required; neither TTL nor matching captured pixels proves live identity.
[Queue tests](../tests/test_vm_execution_lease.py) saturate a real one-worker
executor with an event barrier and use fake commands only, covering gate-to-adapter
expiry at the exact boundary, fresh success, and release despite later expiry.

## Opt-in POSIX command adapter (not installed)

[VmPointerAdapter](../plugin/vm_pointer_adapter.py) takes explicit trusted-host
`executable_path=Path(...)`, `env={...}` and `cwd=Path(...)` constructor arguments.
A reviewed host can assign that instance to `_vm_pointer_executor`; this change
adds no assignment in startup, environment switch, HTTP endpoint, or model field.
Construction/import does not execute anything. Exact canonical runtime records
are detached and validated by the existing single schema before execution.

The executable must be an absolute, canonical, regular nonsymlink executable
file; cwd must be an absolute canonical directory. The host must keep these
paths and their ancestors administrator-owned and stable (the checks do not
solve malicious filesystem replacement races). Environment is an explicit
cloned trusted dictionary, never merged with ambient environment. It can contain
trusted display/auth configuration; never copy model-provided environment keys.
The public `runner` is a trusted runtime capability, not a model authorization API.
Only exact tuple logical `xdotool mousemove X Y`, `mousedown 1`, and `mouseup 1`
commands are accepted; the executable is replaced with the configured absolute
path, without PATH lookup. No shell or PIPE is used; all three streams go to
DEVNULL and no raw command output is returned. Non-POSIX launch is refused before
Popen, since Windows direct-child kill would not meet group teardown semantics.

Each command starts a new session. A deadline or cancellation kills only its
managed PID's process group, then waits to reap the direct child before returning.
No command/action retries occur. Timed blocking waits check cancellation at most
every 50 ms while running; the scheduled path sleeper wakes on the stop event.
An exact safety `mouseup 1` remains permitted after cancellation. The runtime
attempts it once if press was attempted, and sends no later path input. Release
failure still means uncertainty, not guaranteed physical recovery. This is not
a sandbox for arbitrary daemonizing executables; the configured binary is trusted.
Uninterruptible kernel states can delay reap indefinitely: cleanup retains the
lock rather than silently admitting another action with an unretired process.

An instance's async callable owns one asyncio lock on its owning event loop.
Concurrent calls fail immediately with `pointer_runtime_busy` instead of queuing
an action whose screenshot has already been captured. Cancellation signals the
worker, then shields and drains it even under repeated task cancellation before
unlocking and propagating CancelledError. This does **not** serialize other
instances, processes, click channels or raw input. Screenshot validation occurs
before adapter admission; this lock does not make validation/input atomic. The
frozen action carries no ticket deadline: the authorized method instead receives
the separate immutable lease and checks its original TTL after thread-pool queueing.
Legacy standalone calls have no lease. Neither path recaptures pixels inside the
worker; a future host must provide external exclusive GUI ownership across all
channels before claiming safe production activation.

[Mock adapter tests](../tests/test_vm_pointer_adapter.py) exercise transport
contracts, invalid argv, Windows refusal, kill/reap failures and cancellation with
held-lock retirement; they launch no process. The explicitly invoked
[POSIX fixture checks](../tests/pointer_adapter_posix_check.py) use only a temporary
fixed harmless Python executable, never real xdotool, desktop or production access.

Local verification (2026-10-30): focused mock/runtime tests **66 passed in 0.36s**;
pyflakes and compileall passed. With explicit parent approval, the POSIX fixture
ran once under WSL `Ubuntu-TSC` as root: **2 tests passed in 0.435s**, using only
its temporary fixed executable and empty environment. It verified actual zero
and nonzero exits, deadline-triggered group kill, and direct-child reap via
`waitpid` raising `ChildProcessError`. The descendant check accepts absent or
zombie state (not running); it does **not** claim the adapter reaps grandchildren
or proves zero zombies. No desktop, QQ, or production operation was performed.

Queue-lease verification (2026-10-30): 105 focused tests passed, followed by the
full suite with 4334 passed, 40 skipped and 287 subtests passed. The existing
harmless POSIX fixture also passed both tests in 0.427s. One initial fresh-queue
positive case returned failure without retaining its error receipt/command list;
it passed in isolation, two subsequent focused runs and the full suite. Its cause
remains unknown; these reruns do not establish a diagnosis or real-time reliability.
The separate fake-clock regression in a release test was corrected in the test,
not by relaxing runtime timing checks. No real desktop acceptance was performed.
