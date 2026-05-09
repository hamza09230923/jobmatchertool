const EMAIL_KEY = "shortlistly.session.email";

const ALLOWED_EMAIL    = (import.meta.env.VITE_ALLOWED_LOGIN_EMAIL    || "").trim().toLowerCase();
const ALLOWED_PASSWORD = (import.meta.env.VITE_ALLOWED_LOGIN_PASSWORD || "").trim();

export function isAuthConfigured() {
  return Boolean(ALLOWED_EMAIL && ALLOWED_PASSWORD);
}

export function isAuthenticated() {
  return Boolean(localStorage.getItem(EMAIL_KEY));
}

export async function signIn(email, password) {
  if (!isAuthConfigured()) {
    return { ok: false, error: "Auth not configured. Set VITE_ALLOWED_LOGIN_EMAIL and VITE_ALLOWED_LOGIN_PASSWORD in web/.env.local." };
  }
  if (email.trim().toLowerCase() !== ALLOWED_EMAIL || password !== ALLOWED_PASSWORD) {
    return { ok: false, error: "Invalid email or password." };
  }
  localStorage.setItem(EMAIL_KEY, email.trim().toLowerCase());
  return { ok: true };
}

export function signOut() {
  localStorage.removeItem(EMAIL_KEY);
}

export function getStoredEmail() {
  return localStorage.getItem(EMAIL_KEY) || "";
}

export function getStoredToken() {
  return "";
}

export async function refreshLimits() {
  return null;
}
