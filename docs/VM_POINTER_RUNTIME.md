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
or replaced. Generalized path-preview/ticket integration is still pending.

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
