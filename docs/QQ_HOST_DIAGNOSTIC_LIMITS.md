# Native-free host diagnostic limits and next discriminating observation

The frozen [dispatch](qq-host-dispatch-evidence.json) and
[window-role](qq-host-dispatch-roles-evidence.json) observations do not establish a
root cause, successful application readiness, or authentication. The next work
must discriminate an actual hypothesis rather than repeat the same observation.

## Proven diagnostic gaps

- [Shared host observer](../tools/qq_ephemeral_host_entry.py) deliberately excludes
  host-dispatch modes from `capture_categories`, and returns an empty
  `private_output_categories`. This is **missing evidence**, not evidence of no
  error. The application observer returns before the generic runtime timeout
  diagnostics, so those do not fill the gap.
- [Runtime isolation](../tools/qq_isolated_runtime_probe.py) and host child limits
  impose `RLIMIT_FSIZE=16384` on every regular-file write, including descendant
  profile/cache files, not only captured logs. Standard output/error are regular
  files, not undrained pipes. A surviving main process does not rule out failed
  descendants or handled `EFBIG`. `NOFILE=128` is a limit, not measured occupancy.
- [Existing external sampler](../tools/qq_vendor_stall_observation.py) folds empty,
  zero and unrecognized wait-channel values into `other`. Failed task reads are
  skipped. Identity verification is not proof of wait-channel visibility; `other`
  cannot identify a lock, a system call or a kernel wait.
- The private filesystem deliberately exposes only software and `/etc/fonts`,
  not host account identity, machine ID or system D-Bus. HOME/config/cache are
  fresh writable UID65534 directories; claiming there is no writable home is
  wrong. The private session bus has no service activation; its current readiness
  gate verifies a live daemon and socket owner/type, not client Hello/GetId or
  availability of desktop services. These are possible prerequisites, not proven
  explanations of this stall.

## Smallest next step

Reuse `capture_categories` for one fixed host observation, preserving the exact
native-free entry bytes, privileges, namespaces, resource bounds and watchdog.
Report only existing fixed error categories and actual output sizes. Explicitly
check equality with the 16384-byte file cap: `prefix_truncated=false` does not rule
out saturation when the hard limit prevents a larger file. Never publish raw
stderr, account paths, titles or arbitrary environment values.

If that remains inconclusive, separately review a bounded fd-occupancy / masked
wait-channel projection or a private-bus protocol check. Do not silently add
services, bind host identity files, open network routes, remove sandbox limits or
copy account sessions. No such follow-up was executed in the window-role round.
