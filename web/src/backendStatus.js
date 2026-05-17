// Starts pinging the backend immediately on module import (before any component mounts).
// Shared across all routes so the cold start is hidden behind the landing page / login wait.

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").trim();

let _status = "checking";
let _ready  = false;
const _listeners = new Set();

export function getStatus() { return _status; }

export function subscribe(fn) {
  _listeners.add(fn);
  return () => _listeners.delete(fn);
}

function _broadcast(s) {
  _status = s;
  _listeners.forEach(fn => fn(s));
}

let _pollTimer = null;
const _wakeTimer = setTimeout(() => {
  if (!_ready) _broadcast("waking");
}, 2000);

function _ping(initialPromise) {
  let p;
  if (initialPromise) {
    p = initialPromise;
  } else {
    const opts = AbortSignal.timeout ? { signal: AbortSignal.timeout(8000) } : {};
    p = fetch(`${API_BASE_URL}/status`, opts);
  }
  p.then(r => (r.ok ? r.json() : Promise.reject()))
    .then(() => {
      clearTimeout(_wakeTimer);
      clearTimeout(_pollTimer);
      _ready = true;
      _broadcast("ready");
    })
    .catch(() => {
      clearTimeout(_pollTimer);
      _pollTimer = setTimeout(() => _ping(), 4000);
    });
}

// Adopt the warmup fetch fired from index.html <head> if it's there; otherwise start fresh.
_ping(typeof window !== "undefined" ? window.__shortlistlyWarmup : null);

// Keep Render from spinning down while the user fills in the form
setInterval(() => {
  fetch(`${API_BASE_URL}/status`).catch(() => {});
}, 12 * 60 * 1000);
