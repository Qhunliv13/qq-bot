# Private X window-role observation

## Scope and unchanged boundary

This diagnostic selects the fixed `vendor_window_roles` mode in the existing
shared ephemeral host entry. It uses exactly the existing vendor-bootstrap JS,
pristine-root five-software-hash gate, private-X authentication/environment,
pivot-root/network/IPC/PID isolation, UID/GID65534/NNP/cap-zero hook and vendor-stall
30-second waiter. No new jail, QQ flags, native calls, loader or resource override
was introduced. Child limits remain CPU 30 seconds, FSIZE 16384 bytes, NOFILE 128.
The original wrapper, bootstrap and count-only stall defaults are unchanged.

The fixed role reader subclasses `application.PrivateXIdentity`, reuses its
snapshot traversal and keeps window IDs internal. It visits at most 64 windows,
reads at most 64 root child IDs and 32 IDs per other child, and queries role
properties only for windows whose `_NET_WM_PID` resolves to the actual launched
main QQ PID and identical start ticks/same-UID safety identity. Identity is
rechecked before and after role queries. Client-supplied PID metadata is bounded
correlation, **not authenticated X-client ownership**.

Only map state, `_NET_WM_WINDOW_TYPE` atom whitelist booleans (DIALOG, NORMAL,
UTILITY, unknown), `WM_TRANSIENT_FOR` existence/main-window correlation, and three
fixed case-insensitive `WM_CLASS` predicates leave the reader. Class bytes are
limited to 128 and immediately reduced to `class_contains_error`,
`class_contains_dialog`, `class_contains_qq`; raw class content is not reported.
Atom-list reads request at most 128 items; class reads request 32 four-byte units;
types, formats and counts are checked, buffers always freed and `bytes_after`
truncation is reported without fetching the remainder. No name/title property,
text, image, input, dismissal, process memory, host display or account data is read.
CLI accepts only software root and explicit execution consent; there are no
arbitrary PID/display/property options. The fixed callback is copied and imported
before pivot, runs in the existing unprivileged sampler, and the callback hooks
are restored after the same waiter ends.

## One actual execution — 2026-10-29 (UTC+08:00)

Executed once through WSL Ubuntu-TSC as the outer root supervisor against
`/tmp/qq-native-lab-20261027-pristine/opt/QQ`:

```text
python3 tools/qq_private_window_roles.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

Recorded evidence: `evidence/qq-private-window-roles-evidence.json`.

- One 30.03-second experiment; 30 identity-verified monotonic samples, no retries.
- First sample at 0.172 seconds had no windows. Samples at 1.011 through 29.011
  seconds (29 samples) each had four root children/four visited windows and two
  actual-main-PID/start-tick-correlated windows.
- Both correlated windows remained **unmapped**. One had NORMAL type, the other
  had unknown/absent type. Neither had DIALOG/UTILITY, nor a transient parent.
- Both bounded classes matched only the QQ predicate; error/dialog predicates
  were false. Properties were not truncated. No class bytes or window IDs appear
  in published role records.
- `main_owned_dialog_observed=false` throughout. This does **not** prove that a
  modal state or other unattributed window cannot exist; it means no main-PID
  correlated DIALOG/transient role was observed within this bounded snapshot.
- Main process state was S at samples 1–5 seconds, then I at 6–29 seconds. Linux
  I is the sampled idle-state code, not evidence of deadlock. CPU increased by
  353 ticks at 100 Hz over the sampled launch/observation interval; descriptive
  summary is `predominantly_waiting_observed`, not proven cause.
- Exact vendor bootstrap returned; Electron ready callback and timer0 fired;
  timer5s/timer8s did not. Final host record remained absent; watchdog retired the
  process (`exit_code=-9`, report `status=failed`, `custom_main_no_valid_record`).
  CLI exit 1 reflects that incomplete application milestone, not observer failure.
- All five pristine software SHA256 hashes match before/after. Private offline
  isolation gates remain true; CPU/FSIZE/NOFILE were sampled unchanged.
- Bounded stderr categories include DBus-unavailable; no EMFILE/file-size-limit,
  fontconfig, sandbox or shared-memory category. Category absence is not proof
  that a resource cannot be involved.

**Root-cause classification:** the proposed main-owned Qt/error-dialog explanation
is not supported by this actual role metadata. The continuing post-ready idle/
waiting interval and missing later timers remain unexplained. Unknown metadata
is not a title-based inference. Modal state, backend readiness, authentication,
native login and full send/receive remain unproven; no feature completion claim.

## Validation

`python -m pytest tests/test_qq_private_window_roles.py tests/test_qq_vendor_stall_observation.py tests/test_qq_vendor_bootstrap_control.py -q`
reported **34 passed**. Pyflakes was clean for all three modified tools and the new
test module. Tests use mocked Xlib buffers and process identities, cover format/
count/error/buffer-freeing bounds, 128-byte class prefixes/truncation, PID-reuse
refusal, other-PID exclusion, fixed mode/source equality, and forbidden privacy
operations. No tests launch QQ. Parent owns final repository-wide validation and
commit. No production deployment or service mutation was performed.
