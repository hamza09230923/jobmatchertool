// Auth + current-user state. JWT bearer tokens, kept in localStorage.
// Components subscribe to currentUser changes via subscribe(fn).

const TOKEN_KEY = "shortlistly.session.token";
const USER_KEY  = "shortlistly.session.user";
// Legacy key from the old auth — read once and clean up so we don't keep stale data.
const LEGACY_EMAIL_KEY  = "shortlistly.session.email";
const LEGACY_LIMITS_KEY = "shortlistly.session.limits";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

let _currentUser = _readStoredUser();
const _listeners = new Set();

function _readStoredUser() {
  try {
    const raw = localStorage.getItem(USER_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

function _writeUser(user) {
  if (user) {
    localStorage.setItem(USER_KEY, JSON.stringify(user));
  } else {
    localStorage.removeItem(USER_KEY);
  }
  _currentUser = user;
  _listeners.forEach((fn) => fn(user));
}

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

export function getCurrentUser() {
  return _currentUser;
}

export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function getAuthHeader() {
  const t = getStoredToken();
  return t ? { Authorization: `Bearer ${t}` } : {};
}

export function isAuthenticated() {
  return Boolean(getStoredToken() && _currentUser);
}

// Kept for backwards compat — landing page references it.
export function isAuthConfigured() { return true; }

async function _readJson(res) {
  try { return await res.json(); } catch { return {}; }
}

function _storeSession({ token, user }) {
  localStorage.setItem(TOKEN_KEY, token);
  localStorage.removeItem(LEGACY_EMAIL_KEY);
  localStorage.removeItem(LEGACY_LIMITS_KEY);
  _writeUser(user);
}

export async function signUp(email, password) {
  try {
    const res = await fetch(`${API_BASE}/auth/signup`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not create your account." };
    _storeSession(data);
    return { ok: true, user: data.user };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function signIn(email, password) {
  try {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Invalid email or password." };
    _storeSession(data);
    return { ok: true, user: data.user };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function refreshCurrentUser() {
  const token = getStoredToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/auth/me`, { headers: { ...getAuthHeader() } });
    if (res.status === 401) { signOut(); return null; }
    if (!res.ok) return _currentUser;
    const data = await _readJson(res);
    if (data?.user) _writeUser(data.user);
    return data?.user || _currentUser;
  } catch {
    return _currentUser;
  }
}

// Kept name for backwards compat — old code calls refreshLimits().
export const refreshLimits = refreshCurrentUser;

export async function requestPasswordReset(email) {
  try {
    const res = await fetch(`${API_BASE}/auth/forgot-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase() }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not send the reset email." };
    return { ok: true };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function resetPassword(token, password) {
  try {
    const res = await fetch(`${API_BASE}/auth/reset-password`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, password }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not reset password." };
    return { ok: true };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function verifyEmail(token) {
  try {
    const res = await fetch(`${API_BASE}/auth/verify-email`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not verify email." };
    if (data?.user && getStoredToken()) _writeUser(data.user);
    return { ok: true, user: data.user };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function resendVerificationEmail() {
  const token = getStoredToken();
  if (!token) return { ok: false, error: "Not signed in." };
  try {
    const res = await fetch(`${API_BASE}/auth/resend-verification`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeader() },
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not resend verification email." };
    return { ok: true };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function deleteAccount(password) {
  try {
    const res = await fetch(`${API_BASE}/auth/delete-account`, {
      method: "POST",
      headers: { "Content-Type": "application/json", ...getAuthHeader() },
      body: JSON.stringify({ password }),
    });
    const data = await _readJson(res);
    if (!res.ok) return { ok: false, error: data?.detail || "Could not delete account." };
    signOut();
    return { ok: true };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export function signOut() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(USER_KEY);
  localStorage.removeItem(LEGACY_EMAIL_KEY);
  localStorage.removeItem(LEGACY_LIMITS_KEY);
  _writeUser(null);
  window.dispatchEvent(new Event("shortlistly:signout"));
}
