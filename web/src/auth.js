const TOKEN_KEY  = "shortlistly.session.token";
const EMAIL_KEY  = "shortlistly.session.email";
const LIMITS_KEY = "shortlistly.session.limits";

const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export function getStoredToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

export function getStoredEmail() {
  return localStorage.getItem(EMAIL_KEY) || "";
}

export function getStoredLimits() {
  try {
    return JSON.parse(localStorage.getItem(LIMITS_KEY) || "{}");
  } catch {
    return {};
  }
}

export function isAuthenticated() {
  return Boolean(getStoredToken() && getStoredEmail());
}

export function isAuthConfigured() {
  return true;
}

export async function signIn(email, password) {
  try {
    const res = await fetch(`${API_BASE}/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email.trim().toLowerCase(), password }),
    });
    const data = await res.json();
    if (!res.ok) {
      return { ok: false, error: data?.detail || "Invalid email or password." };
    }
    localStorage.setItem(TOKEN_KEY, data.token);
    localStorage.setItem(EMAIL_KEY, data.email);
    localStorage.setItem(LIMITS_KEY, JSON.stringify({
      daily_limit: data.daily_limit,
      scans_today: data.scans_today,
      scans_remaining: data.scans_remaining,
    }));
    return { ok: true, ...data };
  } catch {
    return { ok: false, error: "Could not reach the server. Please try again." };
  }
}

export async function refreshLimits() {
  const token = getStoredToken();
  if (!token) return null;
  try {
    const res = await fetch(`${API_BASE}/auth/status`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ _token: token }),
    });
    if (!res.ok) { signOut(); return null; }
    const data = await res.json();
    localStorage.setItem(LIMITS_KEY, JSON.stringify({
      daily_limit: data.daily_limit,
      scans_today: data.scans_today,
      scans_remaining: data.scans_remaining,
    }));
    return data;
  } catch {
    return null;
  }
}

export function signOut() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(EMAIL_KEY);
  localStorage.removeItem(LIMITS_KEY);
  window.dispatchEvent(new Event("shortlistly:signout"));
}
