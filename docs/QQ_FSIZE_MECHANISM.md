# Child-only file-size limit mechanism check

## Measured result

On 2026-10-30, one explicit invocation in Ubuntu-TSC ran four disposable Python
children, not QQ or Electron. Each resized one anonymous file to 32768 bytes:

| Backing | Child soft/hard RLIMIT_FSIZE | Result | Final size |
|---|---:|---|---:|
| Temporary anonymous file in `/dev/shm` | 16384 bytes | EFBIG | 0 |
| Temporary anonymous file in `/dev/shm` | 65536 bytes | resized | 32768 |
| memfd | 16384 bytes | EFBIG | 0 |
| memfd | 65536 bytes | resized | 32768 |

[Evidence](qq-fsize-mechanism-evidence.json) is exclusively created and never
replaced. The [fixed control](../tools/qq_fsize_mechanism.py) uses fixed children,
five-second process timeouts, empty stdin, discarded stderr and small fixed JSON
receipts. Children close their anonymous files; no named cache or shared-memory
file is retained. The parent's file-size limits remained unchanged.

SIGXFSZ is ignored **only in these disposable Python children**, so the syscall's
errno can be recorded instead of terminating the experiment. This deliberate
choice means their signal handling is not equivalent to QQ. No QQ process,
account, display, network, memory inspection or core-dump access was involved.
The QQ jail, tmpfs sizes, signal handling and resource limits were not changed.

## What this establishes

The existing QQ experiment's 16384-byte RLIMIT_FSIZE is not merely an output-log
cap: a same-size limit can deny enlargement of both a tmpfs-backed file and a
memfd on the measured kernel. Enlarging a tmpfs mount alone would not remove this
independent per-file constraint. The separate 65536-byte children are a mechanism
control, not an authorization or change to the QQ sandbox.

This strengthens a resource-compatibility hypothesis but **does not prove the
SIGTRAP cause**. This experiment does not observe QQ's actual allocation sizes,
backing file descriptors, failed syscall, signal disposition or reaction to
allocation failure. The earlier low-space warning is not proof of exhaustion;
memfd and `/dev/shm` behavior must not be conflated with exact vendor code paths.
No claim is made that a 32 KiB request occurred in QQ.

The current native acceptance gate must therefore continue to report unverified
login/readiness. Before any revised QQ resource profile is run, its changed
bounds, reason and comparison must be explicitly reviewed and recorded; existing
evidence cannot be relabeled as passing or overwritten. A future discriminating
observation must connect a vendor failure path to the constraint rather than
merely assume that removing a restriction fixes the cause.

## Version-qualified upstream corroboration

Exact Chromium **144.0.7559.60** source could not be fetched from the attempted
primary endpoints. The following source was actually read from Debian's hosted
Chromium **150.0.7871.100-1~deb12u1** tree. It is a different version, not evidence
that the QQ vendor build reaches these exact sites:

- [Discardable shared-memory manager](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/components/discardable_memory/service/discardable_shared_memory_manager.cc)
  logs the less-than-64-MiB warning, then caps its policy by half the available
  space. The warning itself is not an allocation or fatal check. This version's
  allocation service returns an invalid region on creation failure; an in-process
  caller can terminate because of out-of-memory after failure to map it.
- [POSIX shared-memory regions](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/memory/platform_shared_memory_region_posix.cc)
  use a temporary backing file and `AllocateFileRegion`, returning an invalid
  region if allocation fails. This inspected default path uses a temporary file,
  not memfd. [POSIX file utilities](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/files/file_util_posix.cc)
  first extend its length; failure there returns false before the fallocate/write
  branches. Our memfd measurement establishes a separate mechanism, not the
  backing type used by that source path or QQ.
- [Field-trial allocation](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/metrics/field_trial.cc)
  defines `kFieldTrialAllocationSize = 256 << 10` (256 KiB). Its allocator creates
  a read-only shared-memory region and calls `OnOutOfMemory` when the region is
  invalid; that helper calls `TerminateBecauseOutOfMemory`. Child launch option
  population invokes allocator initialization. [Read-only shared-memory creation](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/memory/read_only_shared_memory_region.cc)
  propagates an invalid underlying writable region unless a creation hook
  overrides the default implementation.

This identifies a concrete upstream allocation larger than 16 KiB and an
allocation-failure-to-fatal path, but **does not identify QQ's initiating signal
site, prove the first allocation size, or verify the vendor's Chromium 144 code**.
A SIGTRAP and ongoing core dump alone cannot distinguish this mechanism from
other fatal checks. No claim is made that the 64-MiB warning threshold is a
required tmpfs minimum or that increasing mount capacity resolves the per-file
limit. The measured matrix and version-qualified source are complementary,
not a completed root-cause or remediation test.

The same version provides a conditional route from allocation failure to SIGTRAP:
[base/process/memory.h](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/process/memory.h)
uses PartitionAlloc's termination function when `USE_PARTITION_ALLOC` is enabled;
[oom.cc](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/allocator/partition_allocator/src/partition_alloc/oom.cc)
calls `PA_IMMEDIATE_CRASH` on non-Windows platforms. The GCC/Clang x86 sequence in
[PartitionAlloc's crash header](https://sources.debian.org/data/main/c/chromium/150.0.7871.100-1~deb12u1/base/allocator/partition_allocator/src/partition_alloc/partition_alloc_base/immediate_crash.h)
starts with `int3`, then `ud2`; its comment discusses SIGTRAP. This is consistent
with an intentional trap following allocation failure, **not identification of
QQ's actual instruction or build configuration**. Do not substitute the different
build guards from the separate ordinary `base::ImmediateCrash` implementation.

## Verification

[Offline tests](../tests/test_qq_fsize_mechanism.py) cover fixed error categories,
explicit execution consent, exclusive evidence and the measured matrix. They do
not alter the test runner's resource limits. The actual command was:

```text
wsl -d Ubuntu-TSC -u root -- python3 /mnt/c/qhunliv13_web/_public_qqbot/tools/qq_fsize_mechanism.py --execute
```

The control returned exit code zero with all four observations; the artifact
explicitly records `qq_executed=false`, `qq_limits_changed=false` and
`qq_root_cause_proven=false`.
