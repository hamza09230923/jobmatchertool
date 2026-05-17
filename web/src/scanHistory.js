// Local scan history — stored in this browser only, free, no backend cost.
const KEY = "shortlistly.scan_history";
const MAX_ENTRIES = 10;

function _read() {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

function _write(list) {
  try {
    localStorage.setItem(KEY, JSON.stringify(list));
  } catch {
    // QuotaExceeded — drop oldest half and retry once.
    try {
      localStorage.setItem(KEY, JSON.stringify(list.slice(0, Math.floor(MAX_ENTRIES / 2))));
    } catch {}
  }
}

export function getScans() {
  return _read();
}

export function saveScan({ result, fileName, jobSource, jobDescription }) {
  if (!result) return null;
  const entry = {
    id: `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    savedAt: new Date().toISOString(),
    fileName: fileName || "CV.pdf",
    jobSource: jobSource || "paste",
    jobDescription: jobDescription || "",
    matchScore: Math.round(result.match_score || 0),
    result,
  };
  const next = [entry, ..._read()].slice(0, MAX_ENTRIES);
  _write(next);
  return entry;
}

export function removeScan(id) {
  _write(_read().filter((e) => e.id !== id));
}

export function clearScans() {
  _write([]);
}

export function formatRelativeDate(iso) {
  try {
    const then = new Date(iso).getTime();
    const diff = Date.now() - then;
    const min = Math.floor(diff / 60000);
    if (min < 1) return "just now";
    if (min < 60) return `${min} min ago`;
    const hr = Math.floor(min / 60);
    if (hr < 24) return `${hr} hr ago`;
    const d = Math.floor(hr / 24);
    if (d < 7) return `${d}d ago`;
    return new Date(iso).toLocaleDateString();
  } catch {
    return "";
  }
}
