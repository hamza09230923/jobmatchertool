import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import PageLayout from "./PageLayout";
import { signOut, getStoredToken, refreshLimits } from "./auth";
import "./AnalyzePage.css";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").trim();
const MAX_PDF_BYTES = 10 * 1024 * 1024;
const PLACEHOLDER_HINTS = [
  "Senior Software Engineer at Stripe...",
  "Operations Manager leading multi-site teams...",
  "Product Manager owning roadmap delivery...",
  "Data Analyst with SQL and dashboarding...",
  "Customer Success Lead improving retention...",
];

const LONGEST_PLACEHOLDER_HINT = PLACEHOLDER_HINTS.reduce(
  (longest, hint) => (hint.length > longest.length ? hint : longest),
  "",
);

function TypingPlaceholder() {
  const [text, setText] = useState("");
  const [idx, setIdx] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const target = PLACEHOLDER_HINTS[idx];
    if (paused) {
      const timer = setTimeout(() => {
        setPaused(false);
        setDeleting(true);
      }, 1800);
      return () => clearTimeout(timer);
    }
    if (!deleting) {
      if (text.length < target.length) {
        const timer = setTimeout(() => setText(target.slice(0, text.length + 1)), 42);
        return () => clearTimeout(timer);
      }
      const timer = setTimeout(() => setPaused(true), 0);
      return () => clearTimeout(timer);
    }
    if (text.length > 0) {
      const timer = setTimeout(() => setText(text.slice(0, -1)), 22);
      return () => clearTimeout(timer);
    }
    const timer = setTimeout(() => {
      setDeleting(false);
      setIdx((value) => (value + 1) % PLACEHOLDER_HINTS.length);
    }, 0);
    return () => clearTimeout(timer);
  }, [text, deleting, paused, idx]);

  return (
    <span className="az-typing-hint">
      Paste a job description like{" "}
      <span className="az-typing-shell">
        <span className="az-typing-sizer" aria-hidden="true">
          <span className="az-typing-text">{LONGEST_PLACEHOLDER_HINT}</span>
          <span className="az-typing-cursor" />
        </span>
        <span className="az-typing-live">
          <span className="az-typing-text">{text}</span>
          <span className="az-typing-cursor" />
        </span>
      </span>
    </span>
  );
}

async function readErrorMessage(res) {
  try {
    const data = await res.json();
    return data?.detail || `Server error ${res.status}`;
  } catch {
    return `Server error ${res.status}`;
  }
}

export default function AnalyzePage() {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [jobDesc, setJobDesc] = useState("");
  const [jobUrl, setJobUrl] = useState("");
  const [jobInputMode, setJobInputMode] = useState("paste");
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [scraping, setScraping] = useState(false);
  const [error, setError] = useState("");
  const [loadingStep, setLoadingStep] = useState(0);
  const [jobFocused, setJobFocused] = useState(false);
  const [scanLimits, setScanLimits] = useState(() => {
    try { return JSON.parse(localStorage.getItem("shortlistly.session.limits") || "{}"); } catch { return {}; }
  });
  // "checking" → backend ping in flight; "waking" → slow response, show banner; "ready" → responded
  const [backendStatus, setBackendStatus] = useState("checking");
  const backendReadyRef = useRef(false);

  useEffect(() => {
    refreshLimits().then(d => { if (d) setScanLimits(d); });

    let cancelled = false;
    let pollTimer = null;
    let wakeTimer = null;

    const ping = () => {
      fetch(`${API_BASE_URL}/status`, { signal: AbortSignal.timeout ? AbortSignal.timeout(8000) : undefined })
        .then(r => r.ok ? r.json() : Promise.reject())
        .then(() => {
          if (cancelled) return;
          clearTimeout(wakeTimer);
          clearTimeout(pollTimer);
          backendReadyRef.current = true;
          setBackendStatus("ready");
        })
        .catch(() => {
          if (cancelled) return;
          pollTimer = setTimeout(ping, 4000);
        });
    };

    // Show the "waking up" banner only if the first ping takes > 2 s
    wakeTimer = setTimeout(() => {
      if (!backendReadyRef.current && !cancelled) setBackendStatus("waking");
    }, 2000);

    ping();

    // Keep-alive: re-ping every 12 min so Render doesn't spin down while user fills the form
    const keepAlive = setInterval(() => {
      if (!cancelled) fetch(`${API_BASE_URL}/status`).catch(() => {});
    }, 12 * 60 * 1000);

    return () => {
      cancelled = true;
      clearTimeout(pollTimer);
      clearTimeout(wakeTimer);
      clearInterval(keepAlive);
    };
  }, []);

  const loadingSteps = [
    "Reading your CV...",
    "Extracting responsibility signals...",
    "Matching experience evidence...",
    "Scoring skill coverage...",
    "Building your role-fit report...",
  ];

  const handleFile = (selectedFile) => {
    if (!selectedFile) return;
    const isPdf =
      selectedFile.type === "application/pdf" || selectedFile.name.toLowerCase().endsWith(".pdf");
    if (!isPdf) {
      setError("Please upload a PDF CV.");
      return;
    }
    if (selectedFile.size > MAX_PDF_BYTES) {
      setError("PDF is too large. Keep it under 10 MB.");
      return;
    }
    setError("");
    setFile(selectedFile);
  };

  const handleDrop = (event) => {
    event.preventDefault();
    setDragging(false);
    handleFile(event.dataTransfer.files[0]);
  };

  const handleScrapeJob = async () => {
    if (!jobUrl.trim()) {
      setError("Paste a job URL first.");
      return;
    }

    setScraping(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE_URL}/scrape-job`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: jobUrl.trim() }),
      });
      if (!res.ok) {
        throw new Error(await readErrorMessage(res));
      }
      const data = await res.json();
      setJobDesc(data.job_text || "");
      setJobInputMode("url");
      if (!data.job_text) {
        setError("No job text was found at that URL.");
      }
    } catch (err) {
      setError(err.message || "Could not fetch the job description from that URL.");
    } finally {
      setScraping(false);
    }
  };

  const handleSubmit = async (event) => {
    event.preventDefault();
    if (!file) {
      setError("Please upload your CV.");
      return;
    }
    if (!jobDesc.trim()) {
      setError("Please paste or fetch a job description.");
      return;
    }

    setError("");
    setLoading(true);
    setLoadingStep(0);

    const stepInterval = setInterval(() => {
      setLoadingStep((step) => (step < loadingSteps.length - 1 ? step + 1 : step));
    }, 1000);

    const analyzeController = new AbortController();
    const analyzeTimeout = setTimeout(() => analyzeController.abort(), 90000);

    try {
      const form = new FormData();
      form.append("resume", file);
      form.append("job_description", jobDesc.trim());
      form.append("job_source", jobInputMode);
      const token = getStoredToken();
      if (token) form.append("session_token", token);
      const res = await fetch(`${API_BASE_URL}/analyze`, { method: "POST", body: form, signal: analyzeController.signal });
      clearTimeout(analyzeTimeout);
      if (res.status === 429) {
        const d = await res.json();
        throw new Error(d?.detail || "Daily scan limit reached. Try again tomorrow.");
      }
      if (res.status === 401) {
        clearInterval(stepInterval);
        setLoading(false);
        signOut();
        navigate("/login", { replace: true });
        return;
      }
      await refreshLimits();
      if (!res.ok) {
        throw new Error(await readErrorMessage(res));
      }
      const data = await res.json();
      clearInterval(stepInterval);
      setLoading(false);
      navigate("/results", {
        state: {
          result: data,
          fileName: file.name,
          jobSource: jobInputMode,
          jobDescription: jobDesc.trim(),
        },
      });
    } catch (err) {
      clearTimeout(analyzeTimeout);
      clearInterval(stepInterval);
      setLoading(false);
      setError(
        err?.name === "AbortError"
          ? "The server is taking too long to respond. It may be starting up — please wait a moment and try again."
          : err.message || "Something went wrong. Please try again."
      );
    }
  };

  return (
    <PageLayout
      navRight={(
        <div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
          {scanLimits.daily_limit > 0 && (
            <span style={{ fontSize: "0.78rem", color: "rgba(184,192,212,0.55)", fontWeight: 600 }}>
              <span style={{ color: scanLimits.scans_remaining === 0 ? "#f87171" : scanLimits.scans_remaining <= 2 ? "#fbbf24" : "#4ade80", fontWeight: 800 }}>
                {scanLimits.scans_remaining}
              </span>
              {" "}/ {scanLimits.daily_limit} scans left today
            </span>
          )}
          <button
            type="button"
            className="cta-button ghost-button"
            style={{ fontSize: "0.88rem", minHeight: "40px", padding: "0 18px", cursor: "pointer" }}
            onClick={() => { signOut(); navigate("/login", { replace: true }); }}
          >
            Sign out
          </button>
        </div>
      )}
    >
      <div className="analyze-page">
        <div className="analyze-header">
          <div className="az-kicker">
            <span className="az-kicker-dot" />
            CV Analyzer
          </div>
          <h1 className="analyze-title">
            Match your CV to
            <br />
            <span className="az-title-gradient">real job responsibilities.</span>
          </h1>
          <p className="analyze-subtitle">
            Upload your CV, add a job description, and see how your action words, experience, and skills line up with the role.
          </p>
          <div className="az-trust-row">
            <div className="az-trust-pill">Responsibility-led scoring</div>
            <div className="az-trust-pill">PDF upload only</div>
            <div className="az-trust-pill">Paste or fetch by URL</div>
          </div>
        </div>

        {loading ? (
          <div className="az-loading">
            <div className="az-loading-orbit">
              <div className="az-loading-ring az-ring-1" />
              <div className="az-loading-ring az-ring-2" />
              <div className="az-loading-ring az-ring-3" />
              <div className="az-loading-center">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="16" y1="13" x2="8" y2="13" />
                  <line x1="16" y1="17" x2="8" y2="17" />
                </svg>
              </div>
            </div>
            <div className="az-loading-text">
              <p className="az-loading-step">{loadingSteps[loadingStep]}</p>
              <p className="az-loading-sub">Building a responsibility-based match report</p>
            </div>
            <div className="az-loading-track">
              <div className="az-loading-fill" style={{ width: `${((loadingStep + 1) / loadingSteps.length) * 100}%` }} />
            </div>
            <div className="az-loading-steps">
              {loadingSteps.map((step, index) => (
                <div key={step} className={`az-step-item${index < loadingStep ? " done" : index === loadingStep ? " active" : ""}`}>
                  <span className="az-step-dot">
                    {index < loadingStep ? (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : null}
                  </span>
                  <span className="az-step-label">{step}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <form className="analyze-form" onSubmit={handleSubmit}>
            {error ? (
              <div className="analyze-error">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                  <circle cx="12" cy="12" r="10" />
                  <line x1="12" y1="8" x2="12" y2="12" />
                  <line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                {error}
              </div>
            ) : null}

            <div className="az-sections">
              <div className="az-panel">
                <div className="az-panel-head">
                  <div className="az-panel-num">01</div>
                  <div>
                    <div className="az-panel-title">Upload your CV</div>
                    <div className="az-panel-sub">PDF only · max 10 MB</div>
                  </div>
                </div>

                <div
                  className={`az-drop${dragging ? " az-drop--drag" : ""}${file ? " az-drop--filled" : ""}`}
                  onDragOver={(event) => {
                    event.preventDefault();
                    setDragging(true);
                  }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={handleDrop}
                  onClick={() => !file && fileInputRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !file) {
                      fileInputRef.current?.click();
                    }
                  }}
                >
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept=".pdf"
                    style={{ display: "none" }}
                    onChange={(event) => handleFile(event.target.files[0])}
                  />

                  <span className="az-drop-corner az-drop-corner--tl" />
                  <span className="az-drop-corner az-drop-corner--tr" />
                  <span className="az-drop-corner az-drop-corner--bl" />
                  <span className="az-drop-corner az-drop-corner--br" />

                  {file ? (
                    <div className="az-file">
                      <div className="az-file-icon">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                          <polyline points="14 2 14 8 20 8" />
                        </svg>
                      </div>
                      <div className="az-file-meta">
                        <strong>{file.name}</strong>
                        <span>{(file.size / 1024).toFixed(0)} KB · PDF ready</span>
                      </div>
                      <div className="az-file-check">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      </div>
                      <button
                        type="button"
                        className="az-file-remove"
                        onClick={(event) => {
                          event.stopPropagation();
                          setFile(null);
                        }}
                      >
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                          <line x1="18" y1="6" x2="6" y2="18" />
                          <line x1="6" y1="6" x2="18" y2="18" />
                        </svg>
                      </button>
                    </div>
                  ) : (
                    <div className="az-drop-prompt">
                      <div className="az-drop-icon">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
                          <polyline points="17 8 12 3 7 8" />
                          <line x1="12" y1="3" x2="12" y2="15" />
                        </svg>
                      </div>
                      <strong>Drop your CV here</strong>
                      <span>or <u>click to browse</u></span>
                    </div>
                  )}
                </div>
              </div>

              <div className="az-section-divider" />

              <div className="az-panel">
                <div className="az-panel-head">
                  <div className="az-panel-num">02</div>
                  <div>
                    <div className="az-panel-title">Add the job description</div>
                    <div className="az-panel-sub">Paste it directly or fetch it from a job URL</div>
                  </div>
                </div>

                <div className="az-input-mode">
                  <button
                    type="button"
                    className={`az-mode-chip${jobInputMode === "paste" ? " active" : ""}`}
                    onClick={() => setJobInputMode("paste")}
                  >
                    Paste text
                  </button>
                  <button
                    type="button"
                    className={`az-mode-chip${jobInputMode === "url" ? " active" : ""}`}
                    onClick={() => setJobInputMode("url")}
                  >
                    Fetch from URL
                  </button>
                </div>

                <div className={`az-url-panel${jobInputMode === "url" ? " active" : ""}`}>
                  <div className="az-url-row">
                    <input
                      className="az-url-input"
                      type="url"
                      value={jobUrl}
                      onChange={(event) => setJobUrl(event.target.value)}
                      placeholder="https://www.linkedin.com/jobs/view/..."
                    />
                    <button type="button" className="az-url-button" onClick={handleScrapeJob} disabled={scraping}>
                      {scraping ? "Fetching..." : "Fetch job"}
                    </button>
                  </div>
                  <p className="az-url-help">
                    Fetch the listing first, then edit the text below before running the analysis.
                  </p>
                </div>

                <div className={`az-textarea-wrap${jobFocused ? " focused" : ""}${jobDesc ? " filled" : ""}`}>
                  <textarea
                    className="az-textarea"
                    value={jobDesc}
                    onChange={(event) => setJobDesc(event.target.value)}
                    onFocus={() => setJobFocused(true)}
                    onBlur={() => setJobFocused(false)}
                    rows={12}
                  />
                  {!jobDesc && !jobFocused ? (
                    <div className="az-textarea-overlay">
                      <TypingPlaceholder />
                    </div>
                  ) : null}
                  <div className="az-textarea-footer">
                    <span className={`az-char${jobDesc.length > 0 && jobDesc.length < 250 ? " warn" : ""}`}>
                      {jobDesc.length > 0
                        ? `${jobDesc.length} chars${jobDesc.length < 250 ? " · add more detail for better matching" : ""}`
                        : "Use the full job description for stronger responsibility matching"}
                    </span>
                    {jobDesc ? (
                      <button type="button" className="az-clear" onClick={() => setJobDesc("")}>
                        Clear
                      </button>
                    ) : null}
                  </div>
                </div>
              </div>
            </div>

            {backendStatus === "waking" && (
              <div style={{ display: "flex", alignItems: "center", gap: "8px", padding: "10px 14px", background: "rgba(255,209,102,0.07)", border: "1px solid rgba(255,209,102,0.2)", borderRadius: "10px", marginBottom: "4px" }}>
                <span style={{ width: 8, height: 8, borderRadius: "50%", border: "2px solid rgba(255,209,102,0.4)", borderTopColor: "#ffd166", flexShrink: 0, animation: "azOrbit 0.9s linear infinite", display: "inline-block" }} />
                <span style={{ fontSize: "0.8rem", color: "#ffd166", lineHeight: 1.4 }}>
                  Server is starting up — this takes ~30 s on the first visit. You can submit and it will connect automatically.
                </span>
              </div>
            )}

            <div className="az-submit-area">
              <button type="submit" className="az-submit-btn">
                <span className="az-submit-inner">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <circle cx="11" cy="11" r="8" />
                    <path d="M21 21l-4.35-4.35" />
                  </svg>
                  Analyze my CV
                </span>
                <span className="az-submit-shimmer" />
              </button>
              <div className="az-submit-meta">
                <span>Responsibility match</span>
                <span className="az-meta-dot" />
                <span>Experience evidence</span>
                <span className="az-meta-dot" />
                <span>Skills coverage</span>
              </div>
            </div>
          </form>
        )}
      </div>
    </PageLayout>
  );
}
