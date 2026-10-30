# Pure QQ native configuration resolver

As of 2026-10-29. Implementation: [native_metadata.js](../qq_transport/native_metadata.js).
Tests: [test_qq_native_metadata.py](../tests/test_qq_native_metadata.py).

## Scope and evidence boundary

This independently authored CommonJS module translates explicit package metadata and
one explicit version-table entry into desktop engine/login configuration. It has no
imports or dependency on NapCat, no filesystem, network, native loading, environment
reads, timers, package fetching, directory creation, or account selection. Requiring
it has no executable host-side effect beyond declaring/exporting functions.

This task **does not initialize the live engine or login service**, log in, obtain a
QR code, or certify that the settings are a minimum successful native bootstrap.
The separately reported control-host milestone (genuine wrapper loaded in Electron
40 / Node 24) is not promoted here into engine initialization or login success.
A successful pure resolver test establishes only configuration translation.

## API

```javascript
const {resolveNativeConfiguration} = require('./qq_transport/native_metadata.js');
const {engineConfig, loginConfig} = resolveNativeConfiguration({
  packageInfo,   // parsed actual, provenance-checked pristine QQ package metadata
  versionEntry,  // one explicit manifest record for exactly that version and arch
  platform,      // explicit 'linux'
  arch,          // explicit 'x64' or 'arm64'
  osRelease,     // explicit nonblank string
  hostname,      // explicit nonblank string, not an account identifier
  dataPath,      // explicit canonical absolute Linux non-root path
  // desktopSettings: optional complete presentation profile (below)
});
```

There are no `process.platform`, `process.arch`, `os.release()`, `os.hostname()`,
`process.cwd()`, home-directory, environment, manifest, version, appid, or qua defaults.
The host controller obtains and provenance-checks all input externally. Package shape
validation is **not** cryptographic proof of Tencent origin.

### Package and table contract

`packageInfo` must be a plain own-data record with:

- `name: 'QQ'` (foreign package names fail, even if other fields look correct).
- `version`: exact `major.minor.patch-build`, such as `3.2.34-53644`.
- `buildVersion`: positive safe integer or canonical digit string matching `build`.
- `platform: 'linux'` and `eleArch: 'x64' | 'arm64'`, agreeing with explicit inputs.

`versionEntry` must be a plain own-data record with:

- `version`: identical full package version including build.
- `arch`: identical explicit/package architecture.
- `appid`: positive safe integer **or** decimal digit string containing a nonzero
  digit. String spelling and type are preserved, including leading zeroes; large
  digit strings are not converted to potentially lossy JavaScript numbers.
- `qua`: explicit nonempty trimmed string with no ASCII control characters.

No entry is silently selected, synthesized, downloaded or guessed. Missing entries
and mismatched versions/architectures fail. There is deliberately no built-in version
allowlist: an otherwise unknown valid version is accepted **only** when its explicit
entry matches exactly. Unknown architectures/platforms always fail. A table stored in
a different layout must be translated by the controller into this single-entry shape;
the resolver does not execute or import that table's producer.

## Output mapping and presentation profile

Output is `{engineConfig, loginConfig}`, recursively frozen and detached from inputs.
Calling twice produces equivalent but distinct snapshots. Inputs remain editable.
Consumers that mutate native argument objects must clone their selected snapshot
before handing it to the native wrapper.

| Engine field | Source or deliberate schema profile |
| --- | --- |
| `base_path_prefix` | Empty prefix, not an implicit filesystem directory |
| `platform_type` | `5`, selected only for validated explicit Linux |
| `app_type` | `4`, the observed desktop-client app-type selector |
| `app_version` | Exact package/table version |
| `os_version` | Explicit `osRelease` |
| `use_xlog` | Observed presentation/logging default `false` |
| `qua` | Explicit matching `versionEntry.qua`, never an empty fallback |
| `global_path_config.desktopGlobalPath` | Exact explicit `dataPath` |
| `thumb_config` | Observed presentation defaults `{maxSide:324,minSide:48,longLimit:6,density:2}` |

Login fields: `machineId:''`, `appid:versionEntry.appid` (uncoerced),
`platVer:osRelease`, `commonPath:dataPath`, `clientVer:packageInfo.version`,
`hostName:hostname`, `externalVersion:false`. These are initialization metadata,
not a target QQ account, credential, or machine identity discovery implementation.

The constants are visible schema/profile choices, **not** hidden model behavior,
account guesses, arbitrary appid/qua defaults, or a Tencent compatibility guarantee.
`app_type:4` is documented here as the observed desktop-client selector; it is not
claimed to be an officially guaranteed public Tencent enum.

An optional `desktopSettings` allows a caller to explicitly select logging and
thumbnail presentation settings without modifying the resolver:

```javascript
const desktopSettings = {
  base_path_prefix: '',
  app_type: 4,
  use_xlog: false,
  thumb_config: {maxSide: 324, minSide: 48, longLimit: 6, density: 2}
};
```

Supplying it requires every listed field; unknown keys fail. Prefix and app type
remain the desktop profile (`''` and `4`). `use_xlog` must be Boolean; thumbnail
sides/long limit must be positive safe integers, density positive finite number,
and `minSide <= maxSide`. Invalid settings never trigger silent defaulting. The
profile is reconstructed and frozen in output, not aliased or frozen in input.

## Filesystem and safety responsibility

The Linux path validator rejects relative/home/environment spellings, Windows paths,
root `/`, empty/traversal/dot components, repeated/trailing separators, backslashes,
NUL/control characters and outer whitespace. Ordinary internal spaces and Unicode
are allowed. It never normalizes one spelling into another or checks/creates a file.
`desktopGlobalPath` and `commonPath` always receive the identical supplied string.

**Pure lexical validation cannot detect symlinks, ownership, mount boundaries,
existing files, permissions, disk capacity, or races.** The isolated host controller
must verify/create an approved writable directory, enforce no symlink traversal as
needed, and preserve that guarantee through native initialization. No such proof is
claimed by this API. Caller objects must be JSON-style data records, not executable
Proxies; own accessors and inherited required fields are rejected. Errors are
`TypeError` with `code: 'ERR_QQ_NATIVE_METADATA'` and field-only messages that do not
echo private values.

## Pinned primary consumer observations

Observed from NapNeko/NapCatQQ commit
`ba0a53dd372dbc4c4bfc83cf7132997459605210` (reviewed 2026-10-29):

- [Desktop engine and login consumer, base.ts lines 84–128](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-shell/base.ts#L84-L128):
  engine/profile field names and defaults; matching engine/login path; login metadata.
- [Desktop schema and platform enum, wrapper.ts lines 356–398](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/wrapper.ts#L356-L398):
  desktop `app_type:4`, thumbnail schema and zero-based `KLINUX:5` selector.
- [Metadata consumer, qq-basic-info.ts lines 71–95](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/helper/qq-basic-info.ts#L71-L95):
  version/architecture lookup and fallback boundary. Its line-4 import identifies the table below.
- [Explicit appid/qua table](https://github.com/NapNeko/NapCatQQ/blob/ba0a53dd372dbc4c4bfc83cf7132997459605210/packages/napcat-core/external/appid.json):
  externally maintained version metadata, not embedded runtime code in this module.

These are primary observations of an independent consumer, **not** Tencent's public
ABI specification. No source implementation is copied or imported. In particular,
this resolver does not reproduce that consumer's fallback behavior or host discovery.
Production must supply its own explicitly reviewed exact manifest entry; test appids
and qua strings are intentionally synthetic and never production defaults.

### Actual source divergence for the measured 53644 artifact

The pristine Tencent `resources/app/package.json` (SHA-256
`e7d8f160704a8704a4f531ef97602ea6fea8338f738f74ee9b10155cb9fc0a85`)
contains `appid.linux` as the **string** `537391511` and no `qua` field. The pinned
independent consumer's exact-version table instead supplies the **number**
`537391664` with `V1_LNX_NQ_3.2.34_53644_GW_B` for x64. These are different
sources, not interchangeable aliases. The initialize/connect controls explicitly
used the latter observed consumer entry, and that choice is recorded in their
fixed sources; native initialization returning does **not** prove server-side
acceptance or compatibility of those values. Do not silently change/coerce either
value, construct a missing QUA or claim the Tencent manifest authenticates the
consumer table. The pure resolver intentionally requires its owner to select and
review the supplied version entry. See `evidence/qq-pristine-package-evidence.json` and
`evidence/qq-native-init-evidence.json` for artifact and measured-control provenance.

## Verification

```text
node --check qq_transport/native_metadata.js
python -m pytest tests/test_qq_native_metadata.py -q
python -m pyflakes tests/test_qq_native_metadata.py
```

Tests cover exact output, both appid representations, malformed/missing/foreign
metadata, exact version and architecture matching, future exact-entry versions,
Linux arm64, unsafe paths, explicit profile validation, getter/inheritance rejection,
no host defaults, frozen detached snapshots, and loading/running in a VM with no
`require`, `process`, filesystem, native functions, timers or network globals.
They invoke Node only against synthetic metadata; no actual native init is performed.
