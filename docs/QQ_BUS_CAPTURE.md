# Private bus admission capture control

This fixed `bus_admission_capture_offline` control reuses the sole existing jail,
AUTH probe and dbus-send method implementation. It never launches QQ or stages an
entry overlay. No production network, host identity files, account state or
security relaxation is permitted. Only private disposable runtime services run.

## Frozen design before actual observation

- `method_probe(method, detailed=False)` and `sample_bus(detailed=False)` preserve
  old defaults. Old method stdout and stderr remain DEVNULL, with unchanged argv.
- New fixed entry binds `detailed=True` through the shared observer. No CLI flag,
  caller command, address, arbitrary sampler or global capture switch is added.
  The existing sampler binding is temporary in private PID1, restored in finally;
  only its single bounded fork uses the detailed partial.
- Each method has a one-second timeout, overall sampler four seconds, outer 45.
  UID/GID 65534, no-new-privileges and cap-zero remain independently checked.
- The detailed sampler narrows inherited RLIMIT_FSIZE to at most 16384 bytes;
  clients inherit it. Stderr goes to a private TemporaryFile, read at most 4097
  bytes, classifying only the first 4096. Stdout always goes to DEVNULL.
- Only fixed boolean categories and presence/truncation survive. No raw stderr,
  stdout, daemon GUID, message body, username or library path is serialized.
  Quoted excerpts are removed before diagnostic phrase matching. Unknown failures
  retain an explicit boolean rather than inventing a cause.
- AUTH, explicit Hello (`--peer`) and GetId (`--bus`, automatic Hello) are three
  independent connections, not same-connection progression. Installed public
  dbus-send help supports these transport options; exit 1 alone is not usage proof.
- `status=observed` means observation completed, NOT method success or bus/QQ
  readiness. Method success requires its own returned_zero boolean. All login,
  backend, native and production readiness claims remain false.

## One authorized actual observation

After focused regression tests and parent source review, execute exactly once:

```powershell
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_bus_capture_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

The new exclusive artifact is [capture evidence](qq-bus-capture-evidence.json).
The [prior admission evidence](qq-bus-admission-evidence.json) is never overwritten.
The capture artifact records measured results. Runtime source and the design above
were frozen before the actual run. No second execution is authorized. No commit
is made. The measured addendum and regression test below were added afterwards;
these do not change the runtime experiment.

## Measured result (one execution)

The observation completed (`status=observed`), with AUTH `auth_ok`. Both Hello
and GetId returned exit 1. Both private stderr projections have
`no_reply_timeout=true` and `message_generic_error=true`; all other category
booleans are false, including usage, authentication, disconnection and UID lookup.
Both prefixes were present and not truncated. The complete sample took 0.010 s:
this is **no-reply diagnostic wording, not an observed one-second timeout**.
The underlying cause remains unproven; three independent connections cannot prove
same-connection AUTH-to-Hello progression.

The detailed sampler tightened the baseline inherited file-size bound from 1 MiB
to 16 KiB. Thus this is not literally an experiment with all resource limits
unchanged; the stricter output bound accompanies capture, without relaxing any
security restriction. Each binary still inherits its sampler's limit unchanged.

UID/GID 65534, zero effective capabilities and no-new-privileges were verified.
QQ was not launched; all offline checks passed. All five host software hashes
matched before/after. Login, backend and production readiness remain false. The
prior evidence was untouched. Focused mock regressions: 46 passed before the run;
syntax and pyflakes passed. A post-run regression pins these artifact facts without
executing another bus client.
