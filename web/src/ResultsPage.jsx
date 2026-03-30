import { useEffect, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import PageLayout from "./PageLayout";
import "./ResultsPage.css";

const SECTION_LABELS = {
  summary: "Summary",
  experience: "Experience",
  projects: "Projects",
  skills: "Skills",
  education: "Education",
  other: "Other",
};

function ScoreRing({ score }) {
  const r = 54;
  const circ = 2 * Math.PI * r;
  const [animScore, setAnimScore] = useState(0);

  useEffect(() => {
    let start = null;
    const duration = 1400;
    const target = score;
    const step = (ts) => {
      if (!start) start = ts;
      const progress = Math.min((ts - start) / duration, 1);
      const ease = 1 - Math.pow(1 - progress, 3);
      setAnimScore(Math.round(ease * target));
      if (progress < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }, [score]);

  const offset = circ - (animScore / 100) * circ;
  const color = animScore >= 70 ? "#4ade80" : animScore >= 45 ? "#ffd166" : "#ff7070";
  const label = animScore >= 70 ? "Strong match" : animScore >= 45 ? "Partial match" : "Weak match";

  return (
    <div className="score-ring-wrap">
      <div className="score-ring-glow" style={{ background: `radial-gradient(circle, ${color}22, transparent 68%)` }} />
      <svg className="score-ring-svg" viewBox="0 0 128 128" fill="none">
        <circle cx="64" cy="64" r={r} stroke="rgba(255,255,255,0.06)" strokeWidth="8" />
        <circle
          cx="64" cy="64" r={r}
          stroke={color}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circ}
          strokeDashoffset={offset}
          transform="rotate(-90 64 64)"
          style={{ filter: `drop-shadow(0 0 10px ${color}88)`, transition: "stroke 0.5s ease" }}
        />
      </svg>
      <div className="score-ring-center">
        <span className="score-ring-value" style={{ color }}>{animScore}</span>
        <span className="score-ring-denom">/100</span>
      </div>
      <div className="score-ring-label" style={{ color }}>{label}</div>
    </div>
  );
}

function KeywordChips({ keywords }) {
  if (!keywords?.length) {
    return <p className="results-empty-note">No missing keywords — great alignment!</p>;
  }
  return (
    <div className="keyword-chips">
      {keywords.map((kw, i) => (
        <span key={i} className="keyword-chip">{kw}</span>
      ))}
    </div>
  );
}

function SectionCard({ name, feedback }) {
  const [open, setOpen] = useState(false);
  const { good = [], not_good = [] } = feedback;
  const hasIssues = not_good.length > 0;

  return (
    <div className={`section-card${hasIssues ? " has-issues" : " all-good"}`}>
      <button className="section-card-header" onClick={() => setOpen((o) => !o)}>
        <div className="section-card-left">
          <span className={`section-card-dot${hasIssues ? " dot-warn" : " dot-ok"}`} />
          <span className="section-card-name">{SECTION_LABELS[name] || name}</span>
          <div className="section-card-badges">
            {good.length > 0 && <span className="badge badge-ok">{good.length} good</span>}
            {not_good.length > 0 && <span className="badge badge-warn">{not_good.length} to fix</span>}
          </div>
        </div>
        <span className={`section-card-chevron${open ? " open" : ""}`}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="6 9 12 15 18 9"/>
          </svg>
        </span>
      </button>

      {open && (
        <div className="section-card-body">
          {good.map((item, i) => (
            <div key={i} className="section-feedback-item ok">
              <span className="section-feedback-icon">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round">
                  <polyline points="20 6 9 17 4 12"/>
                </svg>
              </span>
              <span>{item}</span>
            </div>
          ))}
          {not_good.map((item, i) => (
            <div key={i} className="section-feedback-item warn">
              <span className="section-feedback-icon">
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round">
                  <line x1="12" y1="8" x2="12" y2="12"/>
                  <line x1="12" y1="16" x2="12.01" y2="16"/>
                </svg>
              </span>
              <span>{item}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export default function ResultsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { result, fileName } = location.state || {};

  useEffect(() => {
    if (!result) navigate("/analyze");
  }, [result, navigate]);

  if (!result) return null;

  const { match_score, missing_keywords = [], section_feedback = {} } = result;
  const sectionKeys = Object.keys(section_feedback);

  return (
    <PageLayout
      navRight={
        <button
          className="cta-button ghost-button"
          style={{ fontSize: "0.88rem", minHeight: "40px", padding: "0 18px", cursor: "pointer", border: "1px solid rgba(255,255,255,0.16)", background: "rgba(255,255,255,0.05)", color: "var(--text)", borderRadius: "999px" }}
          onClick={() => navigate("/analyze")}
        >
          Analyze another
        </button>
      }
    >
      <div className="results-page">
        <div className="results-intro">
          <div className="section-kicker">Analysis complete</div>
          {fileName && <p className="results-filename">{fileName}</p>}
        </div>

        <div className="results-hero">
          <ScoreRing score={Math.round(match_score)} />

          <div className="results-hero-summary">
            <h1 className="results-title">
              Your CV scored <span className="accent-copy">{Math.round(match_score)}</span> out of 100.
            </h1>
            <p className="results-desc">
              {match_score >= 70
                ? "Strong alignment with this role. A few tweaks can push you to the top of the stack."
                : match_score >= 45
                ? "Solid foundation, but some gaps are lowering your visibility to recruiters and ATS systems."
                : "Significant gaps between your CV and this role. The improvements below will make a real difference."}
            </p>
            <div className="results-score-pills">
              <div className="results-score-pill">
                <span className="pill-label">Missing keywords</span>
                <strong className="pill-value">{missing_keywords.length}</strong>
              </div>
              <div className="results-score-pill">
                <span className="pill-label">Sections reviewed</span>
                <strong className="pill-value">{sectionKeys.length}</strong>
              </div>
              <div className="results-score-pill">
                <span className="pill-label">Issues found</span>
                <strong className="pill-value">
                  {sectionKeys.reduce((acc, k) => acc + (section_feedback[k]?.not_good?.length || 0), 0)}
                </strong>
              </div>
            </div>
          </div>
        </div>

        <div className="results-sections">
          <section className="results-block">
            <div className="results-block-header">
              <div className="results-block-icon keyword-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <circle cx="11" cy="11" r="8"/>
                  <path d="M21 21l-4.35-4.35"/>
                </svg>
              </div>
              <div>
                <h2 className="results-block-title">Missing Keywords</h2>
                <p className="results-block-sub">Add these to improve ATS visibility and keyword match.</p>
              </div>
            </div>
            <KeywordChips keywords={missing_keywords} />
          </section>

          <section className="results-block">
            <div className="results-block-header">
              <div className="results-block-icon section-icon">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
                  <polyline points="14 2 14 8 20 8"/>
                </svg>
              </div>
              <div>
                <h2 className="results-block-title">Section Feedback</h2>
                <p className="results-block-sub">Expand each section to see what's working and what to fix.</p>
              </div>
            </div>
            <div className="section-cards">
              {sectionKeys.map((key) => (
                <SectionCard key={key} name={key} feedback={section_feedback[key]} />
              ))}
            </div>
          </section>
        </div>

        <div className="results-cta-row">
          <button className="cta-button primary-button results-cta" onClick={() => navigate("/analyze")}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <polyline points="1 4 1 10 7 10"/>
              <path d="M3.51 15a9 9 0 1 0 .49-4.6"/>
            </svg>
            Analyze another CV
          </button>
          <Link to="/" className="cta-button ghost-button results-cta">Back to home</Link>
        </div>
      </div>
    </PageLayout>
  );
}
