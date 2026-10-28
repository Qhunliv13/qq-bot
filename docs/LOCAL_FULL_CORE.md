# Local entry for the existing full bot

## What this release changes

`plugin/local_runtime.py` exposes a private Unix-domain socket on the existing live
HiganPlugin instance. Local calls use `_local_handle` → `_decide_tools_first` and the
same persona, configured model, 106 registered tools, history Recorder, facts/memory,
task store, observations and execution lock. It does not build a second bot, copy a
state database, replace QQ login or create another model/tool pipeline.

This releases the requirement that a foreground request arrive as a QQ message. QQ
remains an optional capability: QQ-dependent tools still require its native transport;
local reasoning, existing memory and non-QQ tools do not require a live QQ event.
The process remains hosted by the existing AstrBot lifecycle. This is not a claim
that AstrBot itself has been removed or every autonomous QQ activity became local.

## Access

The listener is `/root/higan/run/local.sock` under the existing configured HIGAN_ROOT.
The runtime root must be root-owned and not writable by others. Its `run` directory
is 0700 and the socket is 0600. Linux SO_PEERCRED requires actual UID 0 before request
bytes are read; request JSON cannot supply an owner ID, authorization flag, scope or
QQ account. No public TCP port, shared bearer token or new login is introduced.

The committed operator client can run from the release staging directory:

```bash
/root/astrbot-venv/bin/python /root/qqbot-releases/RELEASE/local_runtime_client.py --health
/root/astrbot-venv/bin/python /root/qqbot-releases/RELEASE/local_runtime_client.py --prompt 'Your request'
/root/astrbot-venv/bin/python /root/qqbot-releases/RELEASE/local_runtime_client.py --history --limit 20
```

A run is executed once. After a client disconnection, accepted work continues, and
its local output remains in the existing history store. Do not automatically replay
a run whose outcome is unknown; inspect local history first. Health/history require
no model call. The CLI does not have to configure another provider or persona.

## State and delivery boundaries

Local requests have their own fixed non-QQ scope, `local:operator`, inside the existing
Recorder. Local context can see the existing shared public-group timeline and its own
prior local turns. Existing private conversation history is not added to that pool.
No old scope, memory file, record ID, task or origin is renamed or migrated.

Local replies/media descriptors and local task results are persisted once through the
same Recorder. They are `local_recorded`, never native QQ receipts or proof that a
client read them. Current authenticated local operators can use existing privileged
tools; persisted local task origins do not impersonate a human owner. Native QQ
receipt and unknown-send behavior remain unchanged.

The foreground local path, scheduled tasks and QQ turns share the original whole-turn
FIFO. Hot reload closes old local intake and waits for accepted work; it does not
cancel a model/tool call. Neither AstrBot nor NapCat is restarted by this release.

## Verification

Before release: full Windows regression, syntax/static checks and actual Linux-root
SO_PEERCRED/socket integration. The Linux tests use a synthetic model-free host; full
core tests reuse the actual production methods with synthetic provider responses.
Production verification separately checks exact committed plugin hashes, unchanged
process identities/configuration and login, local health, and one local real model
turn. A healthy socket alone is not proof of complete model/tool behavior.
