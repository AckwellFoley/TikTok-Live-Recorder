export const SITE = "https://www.tiktok.com";
export const FILE_NAME = "tiktok-recorder-cookies.json";
const LOGIN_NAMES = new Set(["sessionid_ss", "sessionid", "sid_tt"]);

export function normalizeCookies(cookies) {
  if (!Array.isArray(cookies)) return [];
  const host = new URL(SITE).hostname;
  return cookies.filter(cookie => {
    if (!cookie || typeof cookie.name !== "string" || !cookie.name ||
        typeof cookie.value !== "string" || !cookie.value ||
        typeof cookie.domain !== "string") return false;
    const domain = cookie.domain.toLowerCase().replace(/^\./, "");
    const isTikTok = domain === "tiktok.com" || domain.endsWith(".tiktok.com");
    const appliesToHost = host === domain || host.endsWith("." + domain);
    return isTikTok && appliesToHost && cookie.path === "/";
  }).map(cookie => ({
    name: cookie.name,
    value: cookie.value,
    domain: cookie.domain,
    path: cookie.path,
    secure: Boolean(cookie.secure),
  })).sort((a, b) => a.name.localeCompare(b.name) || a.domain.localeCompare(b.domain));
}

export function buildExport(cookies) {
  const selected = normalizeCookies(cookies);
  if (!selected.some(cookie => LOGIN_NAMES.has(cookie.name))) {
    throw new Error("Sign into tiktok.com in this Chrome profile, open the LIVE, then try Export again.");
  }
  return {
    format: "tiktok-recorder-cookies",
    version: 1,
    site: SITE,
    cookies: selected,
  };
}
