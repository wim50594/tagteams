/**
 * Centralised API client.
 * Automatically injects the JWT access token from localStorage.
 * Throws on non-2xx responses with the server's error detail.
 *
 * Silent refresh
 * ---------------
 * The access token is short-lived (15 min by default). The refresh token
 * lives in an httpOnly cookie set by the backend - it's never visible to
 * JS, only sent automatically by the browser to /api/auth/* (hence
 * `credentials: "include"` on every request).
 *
 * When a request gets a 401, we assume the access token expired and:
 *   1. Call POST /api/auth/refresh (cookie sent automatically) to get a
 *      fresh access token.
 *   2. Retry the original request once with the new token.
 *   3. If the refresh itself fails (refresh token also expired/invalid),
 *      give up: clear local auth state and redirect to /login.
 *
 * Concurrent requests that all 401 around the same time share a single
 * in-flight refresh call instead of each independently hitting
 * /api/auth/refresh.
 */

const BASE = import.meta.env.VITE_API_BASE_URL || "";

function getToken() {
  return localStorage.getItem("tt_token");
}

function setToken(token) {
  localStorage.setItem("tt_token", token);
}

function clearAuthAndRedirect() {
  localStorage.removeItem("tt_token");
  localStorage.removeItem("tt_user");
  if (!window.location.pathname.startsWith("/login")) {
    window.location.assign("/login");
  }
}

let refreshPromise = null;

/** Calls /api/auth/refresh at most once concurrently; returns the new
 * access token, or throws if the refresh token is invalid/expired. */
function refreshAccessToken() {
  if (!refreshPromise) {
    refreshPromise = fetch(`${BASE}/api/auth/refresh`, {
      method: "POST",
      credentials: "include",
    })
      .then(async (res) => {
        if (!res.ok) throw new Error("refresh failed");
        const data = await res.json();
        setToken(data.access_token);
        if (data.user) localStorage.setItem("tt_user", JSON.stringify(data.user));
        return data.access_token;
      })
      .finally(() => {
        refreshPromise = null;
      });
  }
  return refreshPromise;
}

async function rawRequest(method, path, { body, form, params, token } = {}) {
  const headers = {};
  const authToken = token ?? getToken();
  if (authToken) headers["Authorization"] = `Bearer ${authToken}`;

  let url = `${BASE}${path}`;
  if (params) {
    const q = new URLSearchParams(params);
    url += `?${q}`;
  }

  const init = { method, headers, credentials: "include" };

  if (form) {
    init.body = form; // FormData – no Content-Type header needed
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }

  return fetch(url, init);
}

async function request(method, path, opts = {}) {
  let res = await rawRequest(method, path, opts);

  if (res.status === 401 && path !== "/api/auth/login" && path !== "/api/auth/refresh") {
    try {
      const newToken = await refreshAccessToken();
      res = await rawRequest(method, path, { ...opts, token: newToken });
    } catch {
      clearAuthAndRedirect();
      const e = new Error("Session expired");
      e.status = 401;
      throw e;
    }
  }

  if (!res.ok) {
    if (res.status === 401) {
      // Refresh succeeded but the retried request still 401'd (or this
      // was a 401 on login/refresh itself) - nothing more we can do.
      clearAuthAndRedirect();
    }
    const err = await res.json().catch(() => ({ detail: res.statusText }));
    // FastAPI 422 returns detail as an array of validation errors
    let message = err.detail || "Request failed";
    if (Array.isArray(message)) {
      message = message.map((e) => e.msg || JSON.stringify(e)).join("; ");
    }
    const e = new Error(message);
    e.status = res.status;
    throw e;
  }

  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) return res.json();
  if (ct.includes("text/csv")) return res; // return raw response for file download
  return res;
}

export const api = {
  get: (path, opts) => request("GET", path, opts),
  post: (path, opts) => request("POST", path, opts),
  put: (path, opts) => request("PUT", path, opts),
  patch: (path, opts) => request("PATCH", path, opts),
  delete: (path, opts) => request("DELETE", path, opts),

  // Auth
  login: (username, password) => {
    const form = new FormData();
    form.append("username", username);
    form.append("password", password);
    return request("POST", "/api/auth/login", { form });
  },
  logout: () => request("POST", "/api/auth/logout"),

  // Media URL helper
  mediaUrl: (filename) => `${BASE}/api/media/${filename}`,
};