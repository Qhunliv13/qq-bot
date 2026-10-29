# Identity-only private bus admission contrast

## Result (2026-10-30, one approved actual run)

The fixed synthetic UID/GID identity records were sufficient to change this isolated
private bus's admission outcome with the exact existing detailed client unchanged.
This is bus causality evidence, **not QQ, native initialization, login, send/receive,
or backend readiness proof**. AUTH, Hello and GetId are three separate connections;
no same-connection progression is claimed.

| Observation | Existing capture baseline | Identity-only contrast |
|---|---|---|
| AUTH | auth_ok | auth_ok |
| explicit Hello | exit 1, no_reply_timeout + message_generic_error | exit 0, stderr absent |
| GetId | exit 1, no_reply_timeout + message_generic_error | exit 0, stderr absent |
| total client elapsed | 0.010 seconds | 0.010 seconds |
| UID/GID | all four slots 65534 | all four slots 65534 |
| capabilities / NNP | zero / true | zero / true |
| QQ invoked / runtime executed | false / false | false / false |

Evidence: [baseline capture](<qq-bus-capture-evidence.json>) and
[identity-only artifact](<qq-bus-identity-evidence.json>). Both retain unchanged
before/after hashes for the pristine software and all offline network gates.
The previous `no_reply_timeout` was a diagnostic category, not a measured one-second
watchdog expiration. The 10ms observations remain consistent with fast rejection
before identity records and successful admission after them. This combined-record
contrast does not establish which individual record or NSS lookup was necessary.

## Fixed intervention and safety

[The new control](<../tools/qq_bus_identity_control.py>) delegates to the unchanged
[capture wrapper](<../tools/qq_bus_capture_control.py>) and
[shared detailed bus client](<../tools/qq_bus_admission_control.py>), changing only
its mode label after observation. Client argv, separate connection semantics,
1-second stages, 4-second sampler, 45-second outer bound and 16KiB capture limit
are unchanged. No native call or QQ child is launched.

[The shared entry](<../tools/qq_ephemeral_host_entry.py>) alone selects the fixed
`bus_identity_offline` mode. [The shared jail](<../tools/qq_isolated_runtime_probe.py>)
defaults `synthetic_identity=False`; its strict boolean/fixed-mode/full-private-runtime
callback gate runs before filesystem modifications. There is no arbitrary identity
CLI, payload, pathname, environment variable or alternate identity input.

Before the private daemon starts, an empty canonical root-owned jail beneath a
root-owned 0700 staging directory receives two exclusively created root-owned 0444
files with precisely these bytes (escaped LF shown):

```text
/etc/passwd: qqprobe:x:65534:65534:isolated runtime:/tmp/home:/usr/sbin/nologin\n
/etc/group: qqprobe:x:65534:\n
```

No root identity is added. No host identity file is read or bound. No nsswitch,
machine-id, services, shadow or password file is introduced; `x` is not a usable
password, shadow is absent, and the shell is nologin. Existing software-only fonts
remain the only pre-existing /etc bind. Records stay inside the disposable jail;
no application data directory outside the jail is written. Existing UID/capability,
network, mount, PID, IPC and detached pivot-root boundaries remain unchanged.

The evidence destination is fixed and opened with `x` before execution, rejecting
existing files and symlinks rather than overwriting or repeating the experiment.

## Integrity

SHA-256 of exact working-tree files at the approved run:

| File | SHA-256 |
|---|---|
| [Baseline artifact](<qq-bus-capture-evidence.json>) | `0c7b8a23b3a7009553fdf14bab4fa5916a94b230022c28a5fd106e16bd855368` |
| [Identity artifact](<qq-bus-identity-evidence.json>) | `46f4921ad413655f80e062a3a9630bc18a2b4d93aa0c8e31ccd9006f718bbdda` |
| [Detailed bus client](<../tools/qq_bus_admission_control.py>) | `2703501c713bcba9836bb0d074083ad4fda6557fbfd57350ceab66bf44d75f17` |
| [Capture wrapper](<../tools/qq_bus_capture_control.py>) | `c4dbd6a9aedc5544f0d975c8d7696801ecf390907242d56bc55be4a56442620b` |

`git diff` of both original client files against HEAD `4b1de8c` is empty. Shared
runtime/entry changes add only the identity gate, setup and fixed dispatch; old modes
retain false defaults. No commit, production/configuration changes or deployment.

## Validation and actual command

- Initial pre-execution Windows suite: 63 passed, 1 Linux-root-specific skip.
- Final focused Windows suite with frozen artifact/client-hash comparison: 64 passed,
  2 Linux-root-specific skips (the actual Linux filesystem test passed separately).
- Real Linux-root [stdlib filesystem test](<../tests/test_qq_bus_identity_filesystem.py>):
  1 passed, including exact bytes, root ownership/0444, no host reads, no overwrite,
  symlink rejection and nonprivate staging refusal. No package installation needed;
  the attempted Linux pytest invocation reported pytest unavailable.
- [Mode/observer/default tests](<../tests/test_qq_bus_identity_control.py>) validate
  strict fixed-mode gates before side effects, no QQ branch, and unchanged defaults.
- Modified Python syntax compilation, pyflakes and `git diff --check`: passed.

Only after focused tests and explicit parent GO, executed exactly once:

```powershell
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_bus_identity_control.py --qq-root /tmp/qq-native-lab-20261027-pristine/opt/QQ --execute
```

Exit code 0; observed successful bus methods. This command is recorded for audit,
not an instruction to repeat it: the exclusive artifact intentionally blocks reruns.
No follow-up QQ/native contrast is authorized by these results.
