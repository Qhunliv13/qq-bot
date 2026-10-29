'use strict';

/**
 * Pure QQ desktop metadata translation. No native loading, I/O, host discovery,
 * account selection, package download, or version-table fallback belongs here.
 * Observed schema/profile constants are explained in docs/QQ_NATIVE_METADATA.md.
 */

/** Report a field, never echo caller-controlled metadata or private paths. */
function invalid(field) {
  const error = new TypeError(`Invalid QQ native configuration: ${field}`);
  error.code = 'ERR_QQ_NATIVE_METADATA';
  throw error;
}

/** Accept JSON-style data only; accessors must not execute during resolution. */
function record(value, field) {
  if (value === null || typeof value !== 'object' || Array.isArray(value) ||
      ![Object.prototype, null].includes(Object.getPrototypeOf(value))) {
    invalid(field);
  }
  // Reject getters/setters rather than evaluating caller code as configuration.
  for (const descriptor of Object.values(Object.getOwnPropertyDescriptors(value))) {
    if (!Object.hasOwn(descriptor, 'value')) invalid(field);
  }
  return value;
}

/** Require an own data field so prototype state cannot supply missing metadata. */
function own(value, key, field) {
  if (!Object.hasOwn(value, key)) invalid(field);
  return value[key];
}

/** Require explicit nonblank strings without invisible control characters. */
function text(value, field) {
  if (typeof value !== 'string' || value.trim() !== value || !value ||
      /[\u0000-\u001f\u007f]/u.test(value)) invalid(field);
  return value;
}

/** Parse the QQ release/build spelling, without maintaining a baked-in allowlist. */
function parseVersion(value, field) {
  text(value, field);
  // Exact matching to a supplied table entry is the authority, not a known build.
  const match = /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)-([1-9]\d*)$/u.exec(value);
  if (!match) invalid(field);
  return {version: value, build: match[4]};
}

/** Preserve the manifest's appid type and digit spelling; never coerce it. */
function appId(value) {
  if (typeof value === 'number') {
    if (!Number.isSafeInteger(value) || value <= 0) invalid('versionEntry.appid');
  } else if (typeof value !== 'string' || !value || /[^0-9]/u.test(value) || !/[1-9]/u.test(value)) {
    invalid('versionEntry.appid');
  }
  return value;
}

/** Validate a canonical absolute Linux path lexically, without claiming realpath proof. */
function linuxDataPath(value) {
  text(value, 'dataPath');
  // No root, traversal, Windows spelling, duplicate separators or implicit expansion.
  // Unicode and ordinary spaces inside an explicit component remain legal on Linux.
  if (!value.startsWith('/') || value === '/' || value.includes('\\') ||
      value.split('/').slice(1).some(part => !part || part === '.' || part === '..')) {
    invalid('dataPath');
  }
  return value;
}

/** Freeze only newly constructed output, never a caller-owned configuration object. */
function deepFreeze(value) {
  for (const child of Object.values(value)) {
    if (child !== null && typeof child === 'object') deepFreeze(child);
  }
  return Object.freeze(value);
}

/** Validate an explicit complete presentation profile, or use the documented profile. */
function desktopProfile(value) {
  // These are consumer-observed desktop schema settings, not version/appid guesses.
  const defaults = {base_path_prefix: '', app_type: 4, use_xlog: false,
    thumb_config: {maxSide: 324, minSide: 48, longLimit: 6, density: 2}};
  if (value === undefined) return defaults;
  record(value, 'desktopSettings');
  const keys = ['base_path_prefix', 'app_type', 'use_xlog', 'thumb_config'];
  if (Object.keys(value).some(key => !keys.includes(key))) invalid('desktopSettings');
  for (const key of keys) own(value, key, `desktopSettings.${key}`);
  // This resolver is intentionally the desktop-client profile, not another app enum.
  if (value.base_path_prefix !== '' || value.app_type !== 4 ||
      typeof value.use_xlog !== 'boolean') invalid('desktopSettings');
  const thumb = record(value.thumb_config, 'desktopSettings.thumb_config');
  const thumbKeys = ['maxSide', 'minSide', 'longLimit', 'density'];
  if (Object.keys(thumb).some(key => !thumbKeys.includes(key))) invalid('desktopSettings.thumb_config');
  for (const key of thumbKeys) {
    const number = own(thumb, key, `desktopSettings.thumb_config.${key}`);
    // Pixel/ratio limits are integers; density is a positive finite scale factor.
    if (typeof number !== 'number' || !Number.isFinite(number) || number <= 0 ||
        (key !== 'density' && !Number.isSafeInteger(number))) {
      invalid(`desktopSettings.thumb_config.${key}`);
    }
  }
  if (thumb.minSide > thumb.maxSide) invalid('desktopSettings.thumb_config');
  return {base_path_prefix: value.base_path_prefix, app_type: value.app_type,
    use_xlog: value.use_xlog, thumb_config: {maxSide: thumb.maxSide,
      minSide: thumb.minSide, longLimit: thumb.longLimit, density: thumb.density}};
}

/**
 * Resolve {engineConfig, loginConfig} exclusively from explicit pristine package
 * metadata, one exact version/architecture manifest entry and explicit host facts.
 * Only Linux x64/arm64 are supported. This does not authenticate the package source
 * or establish filesystem/symlink safety; those are controller responsibilities.
 */
function resolveNativeConfiguration(options) {
  record(options, 'options');
  const packageInfo = record(own(options, 'packageInfo', 'packageInfo'), 'packageInfo');
  const versionEntry = record(own(options, 'versionEntry', 'versionEntry'), 'versionEntry');
  const platform = own(options, 'platform', 'platform');
  const arch = own(options, 'arch', 'arch');
  // Explicit platform selects the observed Linux platform enum; never process.platform.
  if (platform !== 'linux') invalid('platform');
  if (arch !== 'x64' && arch !== 'arm64') invalid('arch');
  if (own(packageInfo, 'name', 'packageInfo.name') !== 'QQ') invalid('packageInfo.name');
  if (own(packageInfo, 'platform', 'packageInfo.platform') !== platform) invalid('packageInfo.platform');
  if (own(packageInfo, 'eleArch', 'packageInfo.eleArch') !== arch) invalid('packageInfo.eleArch');
  const parsed = parseVersion(own(packageInfo, 'version', 'packageInfo.version'), 'packageInfo.version');
  const build = own(packageInfo, 'buildVersion', 'packageInfo.buildVersion');
  // buildVersion may be JSON numeric or textual, but must identify exactly this build.
  if (!((typeof build === 'string' && build === parsed.build) ||
        (Number.isSafeInteger(build) && build > 0 && String(build) === parsed.build))) {
    invalid('packageInfo.buildVersion');
  }
  const entryVersion = parseVersion(own(versionEntry, 'version', 'versionEntry.version'), 'versionEntry.version');
  if (entryVersion.version !== parsed.version) invalid('versionEntry.version');
  if (own(versionEntry, 'arch', 'versionEntry.arch') !== arch) invalid('versionEntry.arch');
  const appid = appId(own(versionEntry, 'appid', 'versionEntry.appid'));
  const qua = text(own(versionEntry, 'qua', 'versionEntry.qua'), 'versionEntry.qua');
  const osRelease = text(own(options, 'osRelease', 'osRelease'), 'osRelease');
  const hostname = text(own(options, 'hostname', 'hostname'), 'hostname');
  const dataPath = linuxDataPath(own(options, 'dataPath', 'dataPath'));
  const profile = desktopProfile(Object.hasOwn(options, 'desktopSettings') ? options.desktopSettings : undefined);
  // Both native entrypoints must receive the exact same caller-owned state root.
  return deepFreeze({
    engineConfig: {
      base_path_prefix: profile.base_path_prefix,
      platform_type: 5,
      app_type: profile.app_type,
      app_version: parsed.version,
      os_version: osRelease,
      use_xlog: profile.use_xlog,
      qua,
      global_path_config: {desktopGlobalPath: dataPath},
      thumb_config: profile.thumb_config,
    },
    loginConfig: {machineId: '', appid, platVer: osRelease, commonPath: dataPath,
      clientVer: parsed.version, hostName: hostname, externalVersion: false},
  });
}

module.exports = Object.freeze({resolveNativeConfiguration});
