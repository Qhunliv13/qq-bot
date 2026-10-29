# Fixed host logging-enabled contrast

**Enabling logging may alter timing, output pressure and even the observed failure. This is not an identical-environment baseline.** No root cause may be inferred merely from a changed signal or additional diagnostics. The unchanged 16KiB file-size limit can itself affect logging-heavy execution (including SIGXFSZ); truncation is evidence, not permission to enlarge limits or retry.

## Fixed design

The [logging wrapper](../tools/qq_host_logging_control.py) selects only `host_dispatch_logging_offline`. Its sole semantic contrast against [fatal capture](QQ_HOST_FATAL.md) is setting `ELECTRON_ENABLE_LOGGING='1'` in the QQ process child environment. The runtime constructs a fresh allowlisted environment, so ambient logging is absent for all other modes; their existing kwargs remain unchanged.

[Official Electron environment documentation](https://www.electronjs.org/docs/latest/api/environment-variables#electron_enable_logging), verified by primary HTTP fetch, says this variable prints Chromium internal logging to the console. The vendor executable may handle or ignore it differently; setting the documented variable does not prove which logger ran. No command-line flags are added, no GPU/sandbox changes occur, and no native hooks are removed.

The authored entry SHA256 remains `4ab8582d780a2cbf4a1fdddc90990dc0c74d594edc8f177dfe219b2b61993a94`. Synthetic identity, four copied helpers, detailed resource/wait/exit sampler, private services, all five pristine software files, isolation, executable arguments, resource limits and 30s/45s watchdogs are unchanged.

## Private boundary and one-run gate

Explicit `--execute --private-output` consent is mandatory. The destination must be outside the public repository and satisfy the existing exclusive Linux owner/mode0600 validator; Windows/DrvFS is not a primary destination. The reviewed external wrapper reserves one public artifact and one private Windows export exclusively, uses a Linux-local `/tmp` primary, verifies owner/mode, and exports at most300KiB. It does not invoke the CLI and therefore does not reserve public evidence twice.

The shared parent strips `_private_fatal_capture` before public assignment, validates the exact schema, and preserves at most16KiB from each stream privately. Public output contains only fixed categories, lengths, hashes and sampled metadata. The outer helper JSON cap stays128KiB. Private raw bytes/base64/text must never be copied into public evidence, documentation, chat or Git. Only redacted signal/fatal-clue interpretation is reportable.

No native execution occurs until focused tests pass, sources freeze, and parent source-review GO is received. Exactly one run is authorized; no automatic retries, cap widening, software changes or production configuration changes are permitted. This delegated implementation performs no commit; the parent owns any reviewed publication.

## Frozen sources and verification

Before execution,96 focused logging/fatal/identity/staging tests passed; Python compilation, pyflakes and whitespace checks passed. After execution,110 expanded focused tests passed, including the closed public artifact; all four frozen source hashes remained identical and pyflakes/whitespace checks passed. Parent source review explicitly authorized exactly one execution after these checks. No runtime source changes are permitted after execution.

Pre-execution SHA256:

- [Fixed wrapper](../tools/qq_host_logging_control.py): `897c6bd46976970e1f58060aa173397afaf54f5a2676520e4f5b9ac47caec738`.
- [Shared control](../tools/qq_ephemeral_host_entry.py): `70540d91489dba84bbc56dc6749616aca1616e609b6ae3086157f1fceb2dcbea`.
- [Shared runtime](../tools/qq_isolated_runtime_probe.py): `354da3476ffd2b22d340a0479c1da53a32cdf68131ec1db927d61affbec32d0c`.
- Reviewed external wrapper: `5dd67cf1be78d881fd3a146794d5ecff7a34cf21739a896d40c24a777aef453c`.

## Observation

Exactly one approved native run completed. [Public evidence](qq-host-logging-evidence.json) records `host_dispatch_incomplete`,30.05s before watchdog retirement and supervisor return-9. All30 process-identity samples passed. The first sample has core-dumping/raw exit status0/0; the remaining29 have1/5, again consistent with SIGTRAP rather than an observed file-size-limit signal. Authored source, synthetic identity, invocation and all five pristine software hashes match the fatal baseline. This is agreement in these observations, not an identical-environment claim.

The private Linux-primary capture and bounded external export succeeded, with Linux owner/mode0600 verified. Direct private read-tool inspection found stdout156bytes of startup/preload notices and stderr746bytes, compared with555bytes in the fatal baseline. The added diagnostic is a shared-memory temporary-space warning: available16MiB is below its64MiB warning threshold. Prior missing-system-bus diagnostics and unresolved cache-write warnings remain; the cache diagnostic does not independently prove that a cache path was missing. **No initiating assertion/fatal explanation was found. The warning is an additional clue, not a proven cause of SIGTRAP.** Vendor preload notices are not verified native APIs or a working session.

Both output-cap flags are false; stderr prefix truncation is false; file-size-limit classification is false. No cap was widened and no retry occurred. Public evidence exposes no private text/base64. Login, binding, session initialization and send/receive remain unverified; `safe_to_login=false`. The wrapper's exit1 reflects the recorded incomplete host observation, not a second execution or capture failure.
