// proxy.js — helper untuk merutekan stream lewat stream-proxy (Fase 2).
// Proxy dibutuhkan saat channel butuh header Referer/User-Agent/Origin (forbidden
// headers di browser) atau saat origin CDN tidak mengizinkan CORS.
//
// Konfigurasi proxy disimpan di localStorage ('dhany_proxy'). Worker contoh ada di
// web/proxy/worker.js (Cloudflare Worker).

const LS_KEY = 'dhany_proxy';

export function getProxyBase() {
  try { return (localStorage.getItem(LS_KEY) || '').trim().replace(/\/+$/, ''); } catch { return ''; }
}
export function setProxyBase(url) {
  try { localStorage.setItem(LS_KEY, (url || '').trim()); } catch {}
}
export function hasProxy() { return !!getProxyBase(); }

// Channel butuh proxy bila punya header khusus ATAU URL-nya http:// (CSP blokir mixed content).
export function needsProxy(channel) {
  if (!channel) return false;
  if (channel.headers && Object.keys(channel.headers).length > 0) return true;
  if (channel.url && channel.url.startsWith('http://')) return true;
  return false;
}

// HTTP stream di port non-standar / bare IP — tidak bisa diproxy lewat Cloudflare Workers.
export function isUnproxyable(channel) {
  if (!channel || !channel.url) return false;
  try {
    const u = new URL(channel.url);
    if (u.protocol !== 'http:') return false;
    const port = u.port || '80';
    if (!['80', '443', '8080', '8443'].includes(port)) return true;
    if (/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(u.hostname)) return true;
  } catch {}
  return false;
}

/**
 * Bungkus URL stream agar lewat proxy, menyertakan header sebagai query.
 * Bentuk: <PROXY>/?url=<enc>&h=<base64(JSON headers)>
 */
export function proxify(url, headers) {
  const base = getProxyBase();
  if (!base) return url;
  let q = `${base}/?url=${encodeURIComponent(url)}`;
  if (headers && Object.keys(headers).length) {
    const enc = b64(JSON.stringify(headers));
    q += `&h=${encodeURIComponent(enc)}`;
  }
  return q;
}

function b64(s) {
  try { return btoa(unescape(encodeURIComponent(s))); } catch { return btoa(s); }
}
