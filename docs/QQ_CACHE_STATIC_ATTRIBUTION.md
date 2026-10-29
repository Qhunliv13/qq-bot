# Static attribution of the `write cache` constant

Observed 2026-10-30 (UTC+8), WSL `Ubuntu-TSC`, against the existing pristine software root `/tmp/qq-native-lab-20261027-pristine/opt/QQ`. Repository baseline supplied by the parent: `e47f099`.

## Scope and safety

Only four fixed, nominated software files were opened read-only. No QQ, Node, Electron or native addon was executed or loaded; no account files, process memory, core dumps, tracing, sysctl changes, installation or filesystem-condition experiments were involved. The temporary general inventory script is external to the repository, at `C:\qhunliv13_web\_inbox\qq_static_fixed_inventory_20261030.py`. No raw binary/string context was printed or retained.

## Exact substring inventory

All offsets below are decimal **file byte offsets**, not callsites or runtime addresses. The needle is exactly the eleven ASCII bytes `write cache`, case-sensitive.

| Fixed alias | Relative path below software root | Count | Offsets |
|---|---|---:|---|
| qq | `qq` | 2 | 17987488, 20074908 |
| major.node | `resources/app/major.node` | 1 | 110122639 |
| wrapper.node | `resources/app/wrapper.node` | 2 | 10452551, 12134174 |
| app_launcher | `resources/app/app_launcher/index.js` | 0 | none |

The launcher above is the original **outer** launcher; it is not the packed ASAR entry. No additional ASAR payload was scanned.

## Standalone-message refinement

Requiring both a preceding and following NUL (`\0write cache\0`) narrows the result:

| Alias | Count | Offset |
|---|---:|---|
| qq | 1 | 17987488 |
| major.node | 0 | none |
| wrapper.node | 0 | none |
| app_launcher | 0 | none |

Thus, among the four nominated files, **qq owns the only observed standalone NUL-delimited `write cache` constant**. The initial substring matches in the other components do not establish that they contain that standalone message. This is constant ownership, not proof of which runtime call emitted either reported stderr line.

A bounded ASCII-path filter found the generic NUL-delimited literal `/code_cache` in qq at file offset **17986528**, 960 bytes before the standalone message. It is in the same 4096-byte inspection window. This is **proximity only**: it does not prove a shared function, a concatenation base, an absolute runtime path, `/tmp` use, V8 ownership, or a connection to the SIGTRAP. No matching allowlisted path was emitted from the other three files; that is not evidence that they have no paths.

GNU objdump section headers place the qq message in `.rodata` (section file offset and VMA both `0x00e8e800`, size `0x017a1f5f`), so this particular message's link-time virtual address equals its file offset. No instruction disassembly or cross-reference analysis was performed. No exact failing cache pathname or emitting function has been established.

## Reproducible method and output limits

The external script uses a fixed four-path map, binary read-only opens, a 1 GiB per-file size ceiling, 1 MiB streaming reads, and a ten-byte overlap between blocks. It records exact byte matches, with a 4096-match ceiling. A separate mode checks the NUL delimiters at the recorded offsets. The nearby mode reads at most 4096 bytes per match, beginning up to 2048 bytes before it, and accepts only NUL-bounded printable ASCII literals of at most 128 bytes. Output is restricted to exact preapproved generic literals or slash-separated paths whose **every component** belongs to a fixed public vocabulary. Unknown strings, suffixes, binary bytes and arbitrary context are never emitted. Hashing also streams in 1 MiB blocks. All modes operate on the same fixed files; no recursive binary discovery or shell `strings` dump is used.

Tool versions: Python **3.12.3**; GNU objdump and GNU readelf **2.42** (GNU Binutils for Ubuntu). Versions were obtained from the static tools themselves, not from native QQ execution.

| Alias | SHA-256 of inspected bytes |
|---|---|
| qq | `ae67432744180adc2b6567aa61b9575b14b28745f57b377a2853a1b744299ec2` |
| major.node | `95c4694572bb3b9fe62ff40a25168fb10b5c6a815fdd260e581ae3d6d92e85f9` |
| wrapper.node | `236ff3ebf29310c75a41394d80db4587411d43d66208c2a7d58b160d7b7b213b` |
| app_launcher | `1c3f92e9da772b2578803f0e2847d3667363165b7f8a77e4869c060ce21596ab` |

These hashes identify the inspected files; they do not independently attest package provenance. The reported prior stderr and SIGTRAP were supplied context, not reproduced here. Existing XDG/cache/home directories must not be assumed absent from these static observations. Any next single-variable native condition remains a separate parent decision and is not authorized or validated by this result.
