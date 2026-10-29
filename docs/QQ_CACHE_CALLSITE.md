# Static `write cache` callsite and local error return

Observed 2026-10-30 (UTC+8). Repository baseline: `dc328f1`. This extends the earlier [constant inventory](QQ_CACHE_STATIC_ATTRIBUTION.md); it does not reproduce the reported stderr or SIGTRAP.

## Scope and identity

Only the pristine software ELF at `/tmp/qq-native-lab-20261027-pristine/opt/QQ/qq` in WSL `Ubuntu-TSC` was opened read-only. Its SHA-256 was recalculated as:

```text
ae67432744180adc2b6567aa61b9575b14b28745f57b377a2853a1b744299ec2
```

No QQ/native addon execution, process memory, ptrace, core, sysctl, patching, filesystem-condition experiment, account data, network research, deployment or commit was involved. Addresses below are public ELF **link-time VMAs**, not observed runtime addresses. No upstream source was used; stripped/nearest-symbol labels are not treated as function identities.

## Exact cross-reference, not literal proximity

ELF64 little-endian headers were decoded with Python standard-library `struct`. The executable `PT_LOAD` has file offset `0x264f000`, VMA `0x2650000`, and file size `155998112` bytes. A 1 MiB-block scan (instruction overlap retained) searched REX `48`/`4c` RIP-relative LEA encodings with `(modrm & 0xc7) == 5`, evaluating signed `disp32 + instruction_end` against the exact target; common immediate-MOV patterns were also checked. Output was capped at sixteen candidates.

The target `0x11277a0` maps to file offset **17987488** and was checked against the exact twelve bytes `write cache\0`. **One candidate was found and independently confirmed by bounded GNU objdump decoding:**

```text
07d47da0  48 8d 3d f9 f9 3d f9  lea -0x6c20607(%rip), %rdi
07d47da7  e8 54 8d dc 03        call 0bb10b00 <perror@plt>
07d47dac  44 89 ff              mov %r15d, %edi
07d47daf  e8 4c d7 13 ff        call 06e85500 <close@@Base>
07d47db4  e9 f8 fc ff ff        jmp 07d47ab1
```

Arithmetic: `0x7d47da0 + 7 - 0x6c20607 = 0x11277a0`. Under the x86-64 SysV ABI, RDI is the first argument, establishing **`perror("write cache")`** directly. This is much stronger evidence than a nearby string. It still does not prove which historical process invocation emitted either reported line.

## Trigger and local control flow

The containing routine begins at `0x7d47980`; its relevant control flow was inspected within a 1472-byte window, not a whole-module disassembly.

| VMA | Static evidence |
|---|---|
| `0x7d47bc1` | Calls the open wrapper `0x7d4a2f0`; flags supplied are `0x42`, mode `0x1b6` (0666). The wrapper adds `0x80000` and calls `open64@plt`. |
| `0x7d47bc6–0x7d47bc9` | Compares open result with `-1`; on equality jumps to `0x7d47ab1` cleanup **without** the `write cache` perror. |
| `0x7d47bef` / `0x7d47ce8` | Two alternative calls to `write@plt`, using the saved fd in R15D, data in R13, and requested size from `[R13+8]`. The second branch temporarily changes a header word and restores it. |
| `0x7d47bf4–0x7d47bf9` | Sign-extends EAX, compares the result with requested size, and branches on **not equal** to `0x7d47da0`. This includes write errors and short writes, not just a negative return. |
| `0x7d47da0–0x7d47db4` | Calls `perror`, closes the fd, jumps to shared cleanup. |
| `0x7d47ab1–0x7d47ac9` | Three cleanup calls, then unconditional jump to `0x7d479f9`. |
| `0x7d479f9` | `xor %eax,%eax`: local failure return value is **0**. |
| `0x7d479fb–0x7d47a1f` | Stack-canary check, register/stack restoration, `ret`. |
| `0x7d47d96` | Separate successful completion loads return value **1** and jumps to the same checked epilogue. |

**Conclusion:** the ordinary error path following this diagnostic is a recoverable **local return of 0**, assuming cleanup returns normally and the stack canary is intact. It does **not directly branch to an `int3`, abort, or exit**. The nearby `__stack_chk_fail@plt` call at `0x7d47db9` is reached on a separate canary-mismatch branch; `int3` bytes at `0x7d47dbe–0x7d47dbf` follow that non-returning guard call, not the normal cache-error jump. The function-local evidence does not establish what its caller does with failure or prove that a later SIGTRAP is independent.

`perror` reports the then-current errno. A successful but short `write` need not set errno, so this mismatch branch can print a **stale errno from an earlier operation**. In particular, a reported `write cache: No such file or directory` does **not by itself prove that this write failed because a cache directory/path was missing**. The label alone must not be promoted into proof of a particular write error, a failed open, absent directory, full filesystem, or exact pathname.

## Path construction established by instructions

The writer does not load `/code_cache` at the diagnostic site. Its base/path selection is data-dependent:

1. At `0x7d479b1`, helper `0x7d447c0` obtains a pointer from offset `0x88` of a configuration-like object (using a default/global object when its argument is null). No literal base directory is established by this load.
2. The writer iterates candidate values and calls `0x7d5cf50` with that prefix and a candidate when the prefix exists, otherwise duplicating the candidate. It probes writability/existence with `access` and has chmod/directory-preparation branches. Their presence does not say which branch a historical run took.
3. At `0x7d47b3a` a separate helper generates a filename-like component into a local buffer. At `0x7d47b49`, `0x7d5cf50` joins the selected directory and that component. Its inspected implementation measures components with `strlen`, inserts byte **`0x2f` (`/`) at `0x7d5d144`**, copies components with `memcpy`, and NUL-terminates the result. This is actual path-composition evidence, not adjacency inference. The component's exact naming algorithm was not traced.
4. At `0x7d47b82`, helper `0x7d48250` receives that joined path. The helper copies it into an object and constructs **`.NEW`** and **`.LCK`** variants with immediate machine-code bytes: `0x57454e2e` at `0x7d482da` and `0x4b434c2e` at `0x7d4830a`. Its associated preparation helper also constructs `.TMP-XXXXXX`.
5. At `0x7d47bad`, a tiny accessor at `0x2a85810` returns `[RDI+8]`; the object constructor stores the **`.NEW` path** in that field at `0x7d482ba`. That returned pointer becomes the first argument to the open wrapper at `0x7d47bc1`. Thus this write targets the temporary **joined-cache-path + `.NEW`** file, not a statically proven `/code_cache` pathname.

The accessor is printed by objdump with a V8 `CodeEvent::GetCodeStartAddress` symbol, but the actual instructions are only a load from offset 8 and return. Such an alias/nearest symbol is **not** evidence that this cache is V8-owned. Likewise the huge `ares_array_len+...` labels on stripped routines are not meaningful attribution.

### Separate `/code_cache` reference

A second exact-target scan for the previously identified `/code_cache` literal, file offset **17986528** / VMA **`0x11273e0`**, found one LEA at **`0x9cc3d06`**. Its bounded window loads that literal into a structure, records length 11, and calls another helper. It is not the error routine described above. No data-flow or call-graph connection between these two sites was established. Therefore the 960-byte rodata proximity must **not** be used to identify the failing cache directory.

## Reproduction and evidence limits

External tool: `C:\qhunliv13_web\_inbox\qq_static_callsites_20261030.py`. It accepts a software-file path and diagnostic target, uses binary read-only opens, a 1 GiB input ceiling, at most 256 ELF program headers, 1 MiB reads, and at most sixteen recorded candidates. The exact standalone diagnostic bytes are checked for the nominated target. The executable scan is a candidate-pattern search, **not an exhaustive decoder**: other instruction forms, indirect references, relocations or compiler transformations can evade it. Confirmed candidates above were decoded rather than assumed to be instruction boundaries.

GNU objdump output was redirected directly to private external evidence files. Each invocation had explicit start/stop addresses, and the script enforces a 4096-byte maximum interval; the largest actual interval was 1472 bytes. No whole-module instruction output, strings inventory, arbitrary virtual-memory reads, or raw private-data output was produced. Window-edge partial instructions and nearest-symbol headings are not used as evidence.

External evidence basenames under `C:\qhunliv13_web\_inbox\`:

- `qq_static_callsites_20261030.json`: matching module hash, load segment and exact diagnostic xref.
- `qq_cache_callsite_window_20261030.txt`: initial 384-byte callsite window.
- `qq_cache_branch_window_20261030.txt`: 1472-byte containing-routine evidence.
- `qq_cache_path_helper_20261030.txt`, `qq_cache_join_helper_20261030.txt`: base-pointer and slash-join instructions (512/768 bytes).
- `qq_cache_file_helpers_20261030.txt`, `qq_cache_path_accessor_20261030.txt`: temporary-path construction and field accessor (768/32 bytes).
- `qq_cache_open_helper_20261030.txt`, `qq_cache_open_tail_20261030.txt`: bounded open wrapper (192/32 bytes).
- `qq_code_cache_xref_20261030.json`, `qq_code_cache_window_20261030.txt`: separate exact literal xref and 384-byte decode.

The original software file was not modified. No runtime filesystem state, exact expanded directory, caller reaction, upstream function name, or SIGTRAP cause is claimed. These remain outside this static result.

## Subsequent upstream-source corroboration

A read-only comparison against Fontconfig tag **2.14.2** found a strong multi-part
source fingerprint. The tag is a comparison reference, **not a verified version
of the library embedded in QQ**:

- [fccache.c](https://gitlab.freedesktop.org/fontconfig/fontconfig/-/raw/2.14.2/src/fccache.c),
  `FcDirCacheWrite`, calls `write(fd, cache, cache->size)`, checks returned length
  against `cache->size`, prints `perror("write cache")` on mismatch, then closes,
  unlocks, destroys the atomic-file object, frees the path and returns `FcFalse`.
- [fcint.h](https://gitlab.freedesktop.org/fontconfig/fontconfig/-/raw/2.14.2/src/fcint.h)
  defines `FC_CACHE_MAGIC_MMAP` as `0xFC02FC04`. Its cache header starts with
  `unsigned int magic`, `int version`, `intptr_t size`, matching the observed
  size-at-offset-eight layout under the x86-64 ABI. Its atomic object puts the
  `new` pointer at offset eight, matching the inspected accessor.
- [fcatomic.c](https://gitlab.freedesktop.org/fontconfig/fontconfig/-/raw/2.14.2/src/fcatomic.c)
  constructs the exact `.NEW`, `.LCK` and `.TMP-XXXXXX` variants and returns
  `atomic->new` from `FcAtomicNewFile`.

Together these support Fontconfig-cache attribution far more strongly than
string adjacency or nearest-symbol labels. They do not establish the vendor's
source revision, actual runtime pathname, file contents, failed write length or
SIGTRAP trigger. Upstream [fcdir.c](https://gitlab.freedesktop.org/fontconfig/fontconfig/-/raw/2.14.2/src/fcdir.c)
explicitly ignores cache-write trouble in `FcDirCacheScan`/`FcDirCacheRescan` and
retains the constructed cache. That is upstream caller behavior; the vendor's
caller has not been traced.

[write(2)](https://man7.org/linux/man-pages/man2/write.2.html) lists a file-size
resource limit among possible short-write causes. This makes the experimental
16 KiB regular-file cap relevant to a hypothesis, not proof of the actual failure.
No universal cache-size minimum was established and no resource limit was changed.
[Fontconfig's documentation](https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html)
describes `FONTCONFIG_USE_MMAP` as controlling mmap access, not disabling writes;
it must not be proposed as a demonstrated cache-write fix.
