import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import PageLayout from "./PageLayout";
import { signOut, getAuthHeader, getCurrentUser, subscribe as subscribeAuth, refreshCurrentUser, resendVerificationEmail, deleteAccount } from "./auth";
import { getStatus, subscribe } from "./backendStatus";
import { getScans, saveScan, removeScan, formatRelativeDate } from "./scanHistory";
import { SAMPLE_STATE } from "./sampleScan";
import "./AnalyzePage.css";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").trim();
const MAX_PDF_BYTES = 10 * 1024 * 1024;

const ANALYSIS_TIPS = [
  "Recruiters spend on average 7 seconds scanning a CV before deciding.",
  "CVs with quantified achievements are 40% more likely to get an interview.",
  "ATS systems filter out up to 75% of applications before a human reads them.",
  "Mirroring the job description's language can triple your ATS match score.",
  "The strongest CVs prove every responsibility with a measurable outcome.",
  "A tailored summary section increases interview callbacks by over 50%.",
];
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
  const [tipIndex, setTipIndex] = useState(0);
  const [jobFocused, setJobFocused] = useState(false);
  const [user, setUser] = useState(() => getCurrentUser());
  const [resendState, setResendState] = useState("idle"); // "idle" | "sending" | "sent"
  // "checking" → backend ping in flight; "waking" → slow response, show banner; "ready" → responded
  const [backendStatus, setBackendStatus] = useState(getStatus);
  const [history, setHistory] = useState(() => getScans());

  useEffect(() => { refreshCurrentUser(); }, []);
  useEffect(() => subscribeAuth(setUser), []);
  useEffect(() => subscribe(setBackendStatus), []);

  const handleResendVerification = async () => {
    setResendState("sending");
    const result = await resendVerificationEmail();
    setResendState(result.ok ? "sent" : "idle");
  };

  const handleDeleteAccount = async () => {
    const password = window.prompt(
      "This will permanently delete your account, scan limits, and any server-side data we hold about you.\n\n" +
      "Type your password to confirm:"
    );
    if (!password) return;
    const confirmed = window.confirm(
      "Are you sure? This cannot be undone."
    );
    if (!confirmed) return;
    const result = await deleteAccount(password);
    if (!result.ok) {
      window.alert(result.error || "Could not delete account.");
      return;
    }
    navigate("/", { replace: true });
  };

  const atFreeTierWall = !!(user && user.tier !== "paid" && user.scans_remaining === 0);

  useEffect(() => {
    if (!loading) return;
    const t = setInterval(() => setTipIndex(i => (i + 1) % ANALYSIS_TIPS.length), 4000);
    return () => clearInterval(t);
  }, [loading]);

  const loadingSteps = [
    "Reading your CV...",
    "Extracting responsibility signals...",
    "Matching experience evidence...",
    "Scoring skill coverage...",
    "Building your role-fit report...",
  ];

  const handleOpenSample = () => {
    navigate("/results?sample=1", { state: SAMPLE_STATE });
  };

  const handleOpenHistory = (entry) => {
    navigate("/results", {
      state: {
        result: entry.result,
        fileName: entry.fileName,
        jobSource: entry.jobSource,
        jobDescription: entry.jobDescription,
        fromHistory: true,
      },
    });
  };

  const handleRemoveHistory = (id) => {
    removeScan(id);
    setHistory(getScans());
  };

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
      // Cap at second-to-last step so the bar never hits 100% while still waiting
      setLoadingStep((step) => (step < loadingSteps.length - 2 ? step + 1 : step));
    }, 5000);

    const analyzeController = new AbortController();
    const analyzeTimeout = setTimeout(() => analyzeController.abort(), 90000);

    try {
      const form = new FormData();
      form.append("resume", file);
      form.append("job_description", jobDesc.trim());
      form.append("job_source", jobInputMode);
      const res = await fetch(`${API_BASE_URL}/analyze`, {
        method: "POST",
        body: form,
        headers: { ...getAuthHeader() },
        signal: analyzeController.signal,
      });
      clearTimeout(analyzeTimeout);
      if (res.status === 402) {
        // Hit free-tier wall — refresh user so the upgrade screen shows.
        await refreshCurrentUser();
        clearInterval(stepInterval);
        setLoading(false);
        const d = await res.json().catch(() => ({}));
        setError(d?.detail || "You've used your free scans. Email gptc2903@gmail.com to upgrade.");
        return;
      }
      if (res.status === 401) {
        clearInterval(stepInterval);
        setLoading(false);
        signOut();
        navigate("/login", { replace: true });
        return;
      }
      if (!res.ok) {
        throw new Error(await readErrorMessage(res));
      }
      const data = await res.json();
      if (data?.user) setUser(data.user);
      clearInterval(stepInterval);
      setLoading(false);
      saveScan({
        result: data,
        fileName: file.name,
        jobSource: jobInputMode,
        jobDescription: jobDesc.trim(),
      });
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
          {user && user.tier !== "paid" && typeof user.scans_remaining === "number" && (
            <span style={{ fontSize: "0.78rem", color: "rgba(184,192,212,0.55)", fontWeight: 600 }}>
              <span style={{ color: user.scans_remaining === 0 ? "#f87171" : user.scans_remaining === 1 ? "#fbbf24" : "#4ade80", fontWeight: 800 }}>
                {user.scans_remaining}
              </span>
              {" "}of {user.free_tier_limit} free scans left
            </span>
          )}
          {user && user.tier === "paid" && (
            <span style={{ fontSize: "0.78rem", color: "#4ade80", fontWeight: 700, letterSpacing: "0.04em" }}>
              UNLIMITED
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
          {!loading && (
            <div className="az-quick-row">
              <button type="button" className="az-sample-link" onClick={handleOpenSample}>
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="12" cy="12" r="10" />
                  <path d="M12 16v-4M12 8h.01" />
                </svg>
                See an example analysis
              </button>
            </div>
          )}
        </div>

        {!loading && user && !user.email_verified && (
          <div style={{
            display: "flex", alignItems: "center", gap: 12, padding: "12px 16px",
            background: "rgba(251,191,36,0.07)", border: "1px solid rgba(251,191,36,0.22)",
            borderRadius: 12, margin: "20px 0 4px", flexWrap: "wrap",
          }}>
            <span style={{ fontSize: "0.85rem", color: "#fbbf24", lineHeight: 1.5, flex: 1, minWidth: 220 }}>
              <strong>Verify your email</strong> — we sent a verification link to <strong>{user.email}</strong>.
              Verifying keeps your scan history safe if you switch devices.
            </span>
            <button
              type="button"
              onClick={handleResendVerification}
              disabled={resendState === "sending" || resendState === "sent"}
              style={{
                fontSize: "0.78rem", fontWeight: 700, padding: "7px 14px", borderRadius: 999,
                border: "1px solid rgba(251,191,36,0.4)", background: "rgba(251,191,36,0.1)",
                color: "#fbbf24", cursor: resendState === "sending" ? "wait" : "pointer",
                opacity: resendState === "sent" ? 0.6 : 1,
              }}
            >
              {resendState === "sent" ? "Sent ✓" : resendState === "sending" ? "Sending…" : "Resend"}
            </button>
          </div>
        )}

        {!loading && user && user.tier !== "paid" && user.scans_remaining === 0 ? (
          <section style={{
            margin: "32px 0",
            padding: "40px 36px",
            border: "1px solid rgba(255,255,255,0.08)",
            borderRadius: 16,
            background: "linear-gradient(180deg, rgba(94,228,255,0.04), rgba(167,139,250,0.03))",
            textAlign: "center",
          }}>
            <div style={{
              display: "inline-flex", padding: "10px 14px", borderRadius: 999,
              background: "rgba(94,228,255,0.08)", border: "1px solid rgba(94,228,255,0.22)",
              color: "#5ee4ff", fontSize: "0.72rem", fontWeight: 700, letterSpacing: "0.16em",
              textTransform: "uppercase", marginBottom: 16,
            }}>Free scans used</div>
            <h2 style={{ fontSize: "1.6rem", fontWeight: 800, margin: "0 0 12px", letterSpacing: "-0.015em" }}>
              You've used your {user.free_tier_limit} free scans.
            </h2>
            <p style={{ fontSize: "0.98rem", color: "rgba(232,237,245,0.85)", maxWidth: 480, margin: "0 auto 24px", lineHeight: 1.6 }}>
              Want unlimited scans, company insights, and recruiter intel?
              Email <a href="mailto:gptc2903@gmail.com" style={{ color: "#5ee4ff", textDecoration: "none", fontWeight: 600 }}>gptc2903@gmail.com</a>
              {" "}to get upgraded — usually within a day.
            </p>
            <div style={{ display: "flex", gap: 12, justifyContent: "center", flexWrap: "wrap" }}>
              <a
                href="mailto:gptc2903@gmail.com?subject=Shortlistly upgrade request"
                className="cta-button primary-button"
                style={{ textDecoration: "none" }}
              >
                Email to upgrade
              </a>
              {history.length > 0 && (
                <button
                  type="button"
                  className="cta-button ghost-button"
                  style={{ cursor: "pointer" }}
                  onClick={() => {
                    const newest = history[0];
                    navigate("/results", {
                      state: {
                        result: newest.result, fileName: newest.fileName,
                        jobSource: newest.jobSource, jobDescription: newest.jobDescription,
                        fromHistory: true,
                      },
                    });
                  }}
                >
                  Re-open your latest scan
                </button>
              )}
            </div>
          </section>
        ) : null}

        {!loading && history.length > 0 && (
          <div className="az-history">
            <div className="az-history-head">
              <span className="az-history-title">Recent scans</span>
              <span className="az-history-sub">Stored on this device · free to re-open</span>
            </div>
            <div className="az-history-list">
              {history.slice(0, 5).map((entry) => {
                const score = entry.matchScore || 0;
                const scoreClass = score >= 70 ? "good" : score >= 40 ? "mid" : "low";
                return (
                  <div key={entry.id} className="az-history-card">
                    <button
                      type="button"
                      className="az-history-main"
                      onClick={() => handleOpenHistory(entry)}
                    >
                      <span className={`az-history-score az-history-score--${scoreClass}`}>{score}</span>
                      <span className="az-history-meta">
                        <span className="az-history-file">{entry.fileName}</span>
                        <span className="az-history-date">{formatRelativeDate(entry.savedAt)}</span>
                      </span>
                    </button>
                    <button
                      type="button"
                      className="az-history-remove"
                      onClick={() => handleRemoveHistory(entry.id)}
                      aria-label="Remove from history"
                    >
                      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                        <line x1="18" y1="6" x2="6" y2="18" />
                        <line x1="6" y1="6" x2="18" y2="18" />
                      </svg>
                    </button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {loading ? (
          <div className="az-loading">
            {/* Header */}
            <div className="az-loading-head">
              <span className="az-live-badge">
                <span className="az-live-dot" />
                AI Analysis Running
              </span>
              <h2 className="az-loading-title">
                Analysing <span className="az-loading-filename">{file?.name || "your CV"}</span>
              </h2>
              <p className="az-loading-caption">Comparing your experience against the job requirements</p>
            </div>

            {/* Scanner visual */}
            <div className="az-scanner">
              <div className="az-scanner-rings">
                <div className="az-loading-ring az-ring-1" />
                <div className="az-loading-ring az-ring-2" />
                <div className="az-loading-ring az-ring-3" />
              </div>
              <svg className="az-scanner-sweep" viewBox="0 0 160 160" fill="none">
                <defs>
                  <linearGradient id="sweepGrad" x1="0%" y1="0%" x2="100%" y2="0%">
                    <stop offset="0%" stopColor="#5ee4ff" stopOpacity="0" />
                    <stop offset="80%" stopColor="#5ee4ff" stopOpacity="0.9" />
                    <stop offset="100%" stopColor="#a78bfa" stopOpacity="1" />
                  </linearGradient>
                </defs>
                <circle cx="80" cy="80" r="66" stroke="rgba(255,255,255,0.04)" strokeWidth="2" />
                <circle cx="80" cy="80" r="66" stroke="url(#sweepGrad)" strokeWidth="3" strokeLinecap="round" strokeDasharray="55 360" className="az-sweep-arc" />
              </svg>
              <div className="az-scanner-center">
                <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <polyline points="14 2 14 8 20 8" />
                  <line x1="16" y1="13" x2="8" y2="13" />
                  <line x1="16" y1="17" x2="8" y2="17" />
                </svg>
                <div className="az-scan-line" />
              </div>
            </div>

            {/* Step checklist */}
            <div className="az-steps-list">
              {loadingSteps.map((step, index) => {
                const isDone = index < loadingStep;
                const isActive = index === loadingStep;
                return (
                  <div key={step} className={`az-step${isDone ? " az-step--done" : isActive ? " az-step--active" : ""}`}>
                    <div className="az-step-icon">
                      {isDone ? (
                        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
                          <polyline points="20 6 9 17 4 12" />
                        </svg>
                      ) : isActive ? (
                        <div className="az-step-pulse" />
                      ) : null}
                    </div>
                    <span className="az-step-text">{step}</span>
                  </div>
                );
              })}
            </div>

            {/* Rotating insight — key forces remount to trigger fade-in */}
            <div className="az-insight" key={tipIndex}>
              <span className="az-insight-label">💡 Did you know?</span>
              <span className="az-insight-text">{ANALYSIS_TIPS[tipIndex]}</span>
            </div>
          </div>
        ) : atFreeTierWall ? null : (
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

        {user && !loading && (
          <div style={{
            marginTop: 48,
            paddingTop: 20,
            borderTop: "1px solid rgba(255,255,255,0.05)",
            textAlign: "center",
          }}>
            <button
              type="button"
              onClick={handleDeleteAccount}
              style={{
                background: "transparent",
                border: 0,
                color: "rgba(184,192,212,0.4)",
                fontSize: "0.78rem",
                cursor: "pointer",
                padding: "6px 10px",
                textDecoration: "underline",
                textUnderlineOffset: 3,
              }}
            >
              Delete my account
            </button>
          </div>
        )}
      </div>
    </PageLayout>
  );
}
