/**
 * Vite `base`（如 `/hub`），已去掉尾斜杠；部署在站点根目录时为空字符串。
 * 与 `BrowserRouter` 的 `basename` 一致（见 `main.tsx`）。
 */
export function getAppBasePath(): string {
  return (import.meta.env.BASE_URL || '/').replace(/\/$/, '')
}

/** 站内路径（须以 `/` 开头），拼上 `base` 后供 `location` 使用。 */
export function resolveAppPath(pathWithLeadingSlash: string): string {
  const base = getAppBasePath()
  const p = pathWithLeadingSlash.startsWith('/') ? pathWithLeadingSlash : `/${pathWithLeadingSlash}`
  return base ? `${base}${p}` : p
}

/** 当前环境下的完整 URL（含 origin），用于 `window.open` 等。 */
export function getAppAbsoluteUrl(pathWithLeadingSlash: string): string {
  return `${window.location.origin}${resolveAppPath(pathWithLeadingSlash)}`
}
