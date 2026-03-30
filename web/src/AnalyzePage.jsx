import { useState, useRef, useEffect } from "react";
import { useNavigate, Link } from "react-router-dom";
import PageLayout from "./PageLayout";
import "./AnalyzePage.css";

const PLACEHOLDER_HINTS = [
  "Senior Software Engineer at Stripe…",
  "Product Manager, AI/ML at Google…",
  "Frontend Engineer · React, TypeScript…",
  "Data Scientist — 5+ years experience…",
  "UX Designer, Figma & Design Systems…",
];

function TypingPlaceholder() {
  const [text, setText] = useState("");
  const [idx, setIdx] = useState(0);
  const [deleting, setDeleting] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const target = PLACEHOLDER_HINTS[idx];
    if (paused) {
      const t = setTimeout(() => { setPaused(false); setDeleting(true); }, 1800);
      return () => clearTimeout(t);
    }
    if (!deleting) {
      if (text.length < target.length) {
        const t = setTimeout(() => setText(target.slice(0, text.length + 1)), 42);
        return () => clearTimeout(t);
      } else { setPaused(true); }
    } else {
      if (text.length > 0) {
        const t = setTimeout(() => setText(text.slice(0, -1)), 22);
        return () => clearTimeout(t);
      } else {
        setDeleting(false);
        setIdx((i) => (i + 1) % PLACEHOLDER_HINTS.length);
      }
    }
  }, [text, deleting, paused, idx]);

  return (
    <span className="az-typing-hint">
      Paste a job description like: <span className="az-typing-text">{text}<span className="az-typing-cursor" /></span>
    </span>
  );
}

export default function AnalyzePage() {
  const navigate = useNavigate();
  const fileInputRef = useRef(null);

  const [file, setFile] = useState(null);
  const [jobDesc, setJobDesc] = useState("");
  const [dragging, setDragging] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [loadingStep, setLoadingStep] = useState(0);
  const [jobFocused, setJobFocused] = useState(false);

  const LOADING_STEPS = [
    "Reading your CV…",
    "Parsing job description…",
    "Computing semantic match…",
    "Scoring keyword coverage…",
    "Building your report…",
  ];

  const handleFile = (f) => {
    if (!f) return;
    if (f.type !== "application/pdf") { setError("Please upload a PDF file."); return; }
    setError("");
    setFile(f);
  };

  const handleDrop = (e) => {
    e.preventDefault();
    setDragging(false);
    handleFile(e.dataTransfer.files[0]);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!file) { setError("Please upload your CV."); return; }
    if (!jobDesc.trim()) { setError("Please paste a job description."); return; }
    setError("");
    setLoading(true);
    setLoadingStep(0);

    const stepInterval = setInterval(() => {
      setLoadingStep((s) => (s < LOADING_STEPS.length - 1 ? s + 1 : s));
    }, 1000);

    try {
      const form = new FormData();
      form.append("resume", file);
      form.append("job_description", jobDesc);
      const res = await fetch("http://localhost:8000/analyze", { method: "POST", body: form });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      const data = await res.json();
      clearInterval(stepInterval);
      setLoading(false);
      navigate("/results", { state: { result: data, fileName: file.name } });
    } catch (err) {
      clearInterval(stepInterval);
      setLoading(false);
      setError(err.message || "Something went wrong. Is the backend running?");
    }
  };

  return (
    <PageLayout
      navRight={
        <Link to="/login" className="cta-button ghost-button" style={{ fontSize: "0.88rem", minHeight: "40px", padding: "0 18px" }}>
          Sign in
        </Link>
      }
    >
      <div className="analyze-page">

        {/* Header */}
        <div className="analyze-header">
          <div className="az-kicker">
            <span className="az-kicker-dot" />
            CV Analyzer
          </div>
          <h1 className="analyze-title">
            Match your CV to
            <br />
            <span className="az-title-gradient">any role, instantly.</span>
          </h1>
          <p className="analyze-subtitle">
            Upload your CV and paste a job description. We score the fit and show you exactly what to sharpen.
          </p>
          <div className="az-trust-row">
            <div className="az-trust-pill">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
              No data stored
            </div>
            <div className="az-trust-pill">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
              5–10 second analysis
            </div>
            <div className="az-trust-pill">
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
              AI-powered scoring
            </div>
          </div>
        </div>

        {loading ? (
          /* Loading screen */
          <div className="az-loading">
            <div className="az-loading-orbit">
              <div className="az-loading-ring az-ring-1" />
              <div className="az-loading-ring az-ring-2" />
              <div className="az-loading-ring az-ring-3" />
              <div className="az-loading-center">
                <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                  <line x1="16" y1="13" x2="8" y2="13"/>
                  <line x1="16" y1="17" x2="8" y2="17"/>
                </svg>
              </div>
            </div>
            <div className="az-loading-text">
              <p className="az-loading-step">{LOADING_STEPS[loadingStep]}</p>
              <p className="az-loading-sub">Hang tight, your analysis is being prepared</p>
            </div>
            <div className="az-loading-track">
              <div className="az-loading-fill" style={{ width: `${((loadingStep + 1) / LOADING_STEPS.length) * 100}%` }} />
            </div>
            <div className="az-loading-steps">
              {LOADING_STEPS.map((step, i) => (
                <div key={i} className={`az-step-item${i < loadingStep ? " done" : i === loadingStep ? " active" : ""}`}>
                  <span className="az-step-dot">
                    {i < loadingStep ? (
                      <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                    ) : null}
                  </span>
                  <span className="az-step-label">{step}</span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <form className="analyze-form" onSubmit={handleSubmit}>
            {error && (
              <div className="analyze-error">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                {error}
              </div>
            )}

            <div className="az-panels">
              {/* CV Upload */}
              <div className="az-panel">
                <div className="az-panel-head">
                  <div className="az-panel-num">01</div>
                  <div>
                    <div className="az-panel-title">Upload your CV</div>
                    <div className="az-panel-sub">PDF format · max 10 MB</div>
                  </div>
                </div>

                <div
                  className={`az-drop${dragging ? " az-drop--drag" : ""}${file ? " az-drop--filled" : ""}`}
                  onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={handleDrop}
                  onClick={() => !file && fileInputRef.current?.click()}
                  role="button"
                  tabIndex={0}
                  onKeyDown={(e) => e.key === "Enter" && !file && fileInputRef.current?.click()}
                >
                  <input ref={fileInputRef} type="file" accept=".pdf" style={{ display: "none" }} onChange={(e) => handleFile(e.target.files[0])} />

                  {/* Corner accents */}
                  <span className="az-drop-corner az-drop-corner--tl" />
                  <span className="az-drop-corner az-drop-corner--tr" />
                  <span className="az-drop-corner az-drop-corner--bl" />
                  <span className="az-drop-corner az-drop-corner--br" />

                  {file ? (
                    <div className="az-file">
                      <div className="az-file-icon">
                        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round">
                          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                          <polyline points="14 2 14 8 20 8"/>
                        </svg>
                      </div>
                      <div className="az-file-meta">
                        <strong>{file.name}</strong>
                        <span>{(file.size / 1024).toFixed(0)} KB · PDF ready</span>
                      </div>
                      <div className="az-file-check">
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                      </div>
                      <button type="button" className="az-file-remove" onClick={(e) => { e.stopPropagation(); setFile(null); }}>
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                      </button>
                    </div>
                  ) : (
                    <div className="az-drop-prompt">
                      <div className="az-drop-icon">
                        <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round">
                          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>
                          <polyline points="17 8 12 3 7 8"/>
                          <line x1="12" y1="3" x2="12" y2="15"/>
                        </svg>
                      </div>
                      <strong>Drop your CV here</strong>
                      <span>or <u>click to browse</u></span>
                    </div>
                  )}
                </div>
              </div>

              {/* Connector */}
              <div className="az-connector">
                <div className="az-connector-line" />
                <div className="az-connector-node">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
                </div>
                <div className="az-connector-line" />
              </div>

              {/* Job desc */}
              <div className="az-panel">
                <div className="az-panel-head">
                  <div className="az-panel-num">02</div>
                  <div>
                    <div className="az-panel-title">Paste job description</div>
                    <div className="az-panel-sub">Copy from LinkedIn, Indeed, or any job board</div>
                  </div>
                </div>

                <div className={`az-textarea-wrap${jobFocused ? " focused" : ""}${jobDesc ? " filled" : ""}`}>
                  <textarea
                    className="az-textarea"
                    value={jobDesc}
                    onChange={(e) => setJobDesc(e.target.value)}
                    onFocus={() => setJobFocused(true)}
                    onBlur={() => setJobFocused(false)}
                    rows={12}
                  />
                  {!jobDesc && !jobFocused && (
                    <div className="az-textarea-overlay">
                      <TypingPlaceholder />
                    </div>
                  )}
                  <div className="az-textarea-footer">
                    <span className={`az-char${jobDesc.length > 0 && jobDesc.length < 200 ? " warn" : ""}`}>
                      {jobDesc.length > 0 ? `${jobDesc.length} chars${jobDesc.length < 200 ? " · paste more for better results" : ""}` : "Paste the full listing for best results"}
                    </span>
                    {jobDesc.length > 0 && (
                      <button type="button" className="az-clear" onClick={() => setJobDesc("")}>
                        Clear
                      </button>
                    )}
                  </div>
                </div>
              </div>
            </div>

            {/* Submit */}
            <div className="az-submit-area">
              <button type="submit" className="az-submit-btn">
                <span className="az-submit-inner">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/></svg>
                  Analyze my CV
                </span>
                <span className="az-submit-shimmer" />
              </button>
              <div className="az-submit-meta">
                <span>Powered by AI</span>
                <span className="az-meta-dot" />
                <span>5–10 seconds</span>
                <span className="az-meta-dot" />
                <span>No account needed</span>
              </div>
            </div>
          </form>
        )}
      </div>
    </PageLayout>
  );
}
