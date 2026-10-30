# Native-free host dispatch: external window roles

## Scope and invariant

The fixed `host_dispatch_roles_offline` mode reuses the exact
[dispatch entry](<../tools/qq_host_dispatch_entry.js>) and
[dispatch parser](<../tools/qq_host_dispatch_control.py>). The only observation
change from `host_dispatch_offline` is the existing
[`sample_roles` callback](<../tools/qq_private_window_roles.py>) passed to the shared
bounded waiter. Its existing identity whitelist is unchanged. The shared runner
preimports and copies the original four helpers plus that reader before pivot.
No native entry, native modules, or old-mode behavior was changed.

The [new fixed CLI](<../tools/qq_host_dispatch_roles_control.py>) reserves
[separate evidence](<evidence/qq-host-dispatch-roles-evidence.json>) with exclusive creation
before execution. It cannot overwrite [prior evidence](<evidence/qq-host-dispatch-evidence.json>).
There are no configurable source, mode, PID, display or executable switches.

The sampler remains UID65534 on authenticated private X, with actual main
PID/start-tick correlation. It reads bounded window type, transient relationship,
map state and existing class predicates only. No titles, screenshots, images,
input events, raw class strings, account state or production data are requested.
Network isolation, host executable argv, private services, 30s/45s bounds and
CPU/file/open-file limits are unchanged. Output categories remain `{}`.

## One authorized observation

Focused fake-only tests passed before parent GO. Exactly one actual invocation
was made under WSL Ubuntu-TSC root; QQ and the sampler drop privileges in the
existing runner:

```text
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_host_dispatch_roles_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

The diagnostic returned exit 1 because dispatch remained incomplete, not because
metadata capture failed: watchdog 30s, QQ exit -9, process retired, elapsed 30.05s.
Thirty identity-verified samples were captured. The first (0.172s) had zero root
children and zero main-correlated windows. All remaining 29 (1.011–29.011s) had
three root children and one main-PID-correlated window, consistent with the prior
count-only observation. Its role values were identical in those 29 samples:

| Metadata | Observed |
|---|---|
| Map state | `unmapped` |
| Dialog / normal / utility type | all `false` |
| Unknown type | `true` |
| Transient property / main-window relationship | both `false` |
| Class valid / contains QQ | both `true` |
| Class contains error / dialog | both `false` |
| Property truncation | all `false` |

No dialog-indicating metadata was observed in the sampled main-correlated window.
**This does not prove that an error dialog is absent, that a modal state cannot
exist, or that the stall's root cause has been identified.** Unknown type is not
normal type; an unmapped window is not a visible dialog. The two other root
children were not main-PID-correlated role subjects, and sampling is bounded.
All `modal_state_proven` and `root_cause_proven` values remain false.

Dispatch matched the prior event prefix: `bootstrapEntered`, `synchronousEnd`,
`nextTick`, `promiseThen`, `queueMicrotask`, `setImmediate`, `setTimeout0`.
The last two were observed at 287ms. Registered `setTimeout1000`, `whenReady` and
`onReady` were not observed before retirement. This is neither QQ/native/login
readiness nor evidence of which subsystem blocked. The shared summary classified
samples as predominantly waiting (373 CPU ticks at 100 ticks/s); coarse wait
categories do not identify a native/kernel wait or root cause.

## Integrity and validation

Authored JS SHA256, identical to prior evidence and current source:

```text
4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94
```

Prior evidence remains byte-for-byte preserved (SHA256):

```text
828b84a651fc0099a98dbce400232fa5f954abac0c57b77d3fede2a1b484571e
```

All five host software SHA256 values are identical before/after this run and to
the prior observation. Exact values are retained in the new evidence. All four
offline-network checks passed. No host software, account, production, network
policy, resource limit or Electron flag was changed.

[Focused tests](<../tests/test_qq_host_dispatch_roles_control.py>) reuse existing
fixtures and check fixed source identity, old-mode compatibility, exact callback
identity, shared parser, copied/preimported dependencies, failed-sample unknown
semantics, exclusive artifact creation and frozen evidence/hash comparison.
The focused set with old dispatch and shared integration passed 66 tests before
the actual run; a frozen-evidence test was then added. Runtime source was not
changed after the observation. Syntax compilation and pyflakes checks passed.
