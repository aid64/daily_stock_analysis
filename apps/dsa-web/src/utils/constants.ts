import { desktopBackendUrl } from './desktop';

const configuredApiBaseUrl = import.meta.env.VITE_API_URL?.trim();

declare const __APP_PACKAGE_VERSION__: string | undefined;
declare const __APP_REVISION__: string | undefined;
declare const __APP_BUILD_TIME__: string | undefined;

const PLACEHOLDER_WEB_VERSION = '0.0.0';
const DEVELOPMENT_WEB_VERSION = 'development';
const UNKNOWN_REVISION = 'unknown';
const UNKNOWN_BUILD_TIME = '未提供';

// 桌面端打包模式下，前端通过 file:// 加载，必须使用后端注入的绝对地址；
// dev 模式下 desktopBackendUrl 为空字符串，回退到同源/显式配置。
// Web 端 desktopBackendUrl 为 undefined，走原有同源逻辑。

// 优先级：桌面端注入 > VITE_API_URL 显式配置 > 同源（空字符串）。
// 空字符串表示同源，浏览器会把请求发到当前页面所在的 host。
export const API_BASE_URL = desktopBackendUrl || configuredApiBaseUrl || '';

export type WebBuildInfo = {
  version: string;
  rawVersion: string;
  revision: string;
  buildId: string;
  buildTime: string;
  isFallbackVersion: boolean;
};

function padBuildPart(value: number) {
  return value.toString().padStart(2, '0');
}

export function normalizeBuildTimestamp(buildTimestamp?: string) {
  const normalized = buildTimestamp?.trim();
  if (!normalized) {
    return UNKNOWN_BUILD_TIME;
  }

  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.getTime())) {
    return normalized;
  }

  return parsed.toISOString();
}

export function createBuildIdentifier(buildTimestamp?: string) {
  const normalized = buildTimestamp?.trim();
  if (!normalized || normalized === UNKNOWN_BUILD_TIME) {
    return 'build-local';
  }

  const parsed = new Date(normalized);
  if (!Number.isNaN(parsed.getTime())) {
    const datePart = `${parsed.getUTCFullYear()}${padBuildPart(parsed.getUTCMonth() + 1)}${padBuildPart(parsed.getUTCDate())}`;
    const timePart = `${padBuildPart(parsed.getUTCHours())}${padBuildPart(parsed.getUTCMinutes())}${padBuildPart(parsed.getUTCSeconds())}`;
    return `build-${datePart}-${timePart}Z`;
  }

  const compactValue = normalized.replace(/[^0-9A-Za-z]+/g, '-').replace(/^-+|-+$/g, '');
  return compactValue ? `build-${compactValue}` : 'build-local';
}

export function resolveWebBuildInfo({
  packageVersion,
  revision,
  buildTimestamp,
}: {
  packageVersion?: string;
  revision?: string;
  buildTimestamp?: string;
}): WebBuildInfo {
  const rawVersion = packageVersion?.trim() || PLACEHOLDER_WEB_VERSION;
  const normalizedRevision = revision?.trim() || UNKNOWN_REVISION;
  const buildTime = normalizeBuildTimestamp(buildTimestamp);
  const buildId = createBuildIdentifier(buildTime);
  const isFallbackVersion = rawVersion === PLACEHOLDER_WEB_VERSION;

  return {
    version: isFallbackVersion ? DEVELOPMENT_WEB_VERSION : rawVersion,
    rawVersion,
    revision: normalizedRevision,
    buildId,
    buildTime,
    isFallbackVersion,
  };
}

const runtimePackageVersion = typeof __APP_PACKAGE_VERSION__ === 'string'
  ? __APP_PACKAGE_VERSION__.trim()
  : PLACEHOLDER_WEB_VERSION;
const runtimeRevision = typeof __APP_REVISION__ === 'string'
  ? __APP_REVISION__.trim()
  : UNKNOWN_REVISION;
const runtimeBuildTime = typeof __APP_BUILD_TIME__ === 'string'
  ? __APP_BUILD_TIME__.trim()
  : '';

export const WEB_BUILD_INFO = resolveWebBuildInfo({
  packageVersion: runtimePackageVersion,
  revision: runtimeRevision,
  buildTimestamp: runtimeBuildTime,
});
