import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import PageLayout from "./PageLayout";
import { signOut, getAuthHeader } from "./auth";
import "./ResultsPage.css";

const API_BASE_URL = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").trim();

const SECTION_LABELS = {
  summary: "Summary",
  experience: "Experience",
  projects: "Projects",
  skills: "Skills",
  education: "Education",
  other: "Other",
};

async function readErrorMessage(res) {
  try {
    const data = await res.json();
    return data?.detail || `Server error ${res.status}`;
  } catch {
    return `Server error ${res.status}`;
  }
}

function scoreColor(n) {
  const hue = Math.round(Math.max(0, Math.min(100, n)) * 1.2);
  return `hsl(${hue}, 88%, 62%)`;
}

function ScoreRing({ score }) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const [animatedScore, setAnimatedScore] = useState(0);

  useEffect(() => {
    let start = null;
    let raf;
    const step = (timestamp) => {
      if (!start) start = timestamp;
      const progress = Math.min((timestamp - start) / 1500, 1);
      const eased = 1 - Math.pow(1 - progress, 3);
      setAnimatedScore(Math.round(eased * score));
      if (progress < 1) raf = requestAnimationFrame(step);
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [score]);

  const color = scoreColor(animatedScore);
  const finalColor = scoreColor(score);
  const strokeOffset = circumference - (animatedScore / 100) * circumference;
  const label = score >= 70 ? "Strong fit" : score >= 45 ? "Partial fit" : "Weak fit";

  return (
    <div className="score-ring-wrap">
      <div className="score-ring-glow" style={{ background: `radial-gradient(circle at 50% 50%, ${finalColor}28, transparent 68%)` }} />
      <div className="score-ring-svg-wrap">
        <svg className="score-ring-svg" viewBox="0 0 128 128" fill="none">
          <circle cx="64" cy="64" r={radius} stroke="rgba(255,255,255,0.06)" strokeWidth="8" />
          <circle
            cx="64" cy="64" r={radius}
            stroke={color}
            strokeWidth="8"
            strokeLinecap="round"
            strokeDasharray={circumference}
            strokeDashoffset={strokeOffset}
            transform="rotate(-90 64 64)"
            style={{ filter: `drop-shadow(0 0 16px ${color}70)`, transition: "stroke-dashoffset 0.04s linear, stroke 0.25s ease" }}
          />
        </svg>
        <div className="score-ring-center">
          <span className="score-ring-value" style={{ color }}>{animatedScore}</span>
        </div>
      </div>
      <div className="score-ring-footer">
        <span className="score-ring-label" style={{ color }}>{label}</span>
        <span className="score-ring-out-of">out of 100</span>
      </div>
    </div>
  );
}

function PanelSkeleton({ label = "Loading…" }) {
  return (
    <section className="results-block" style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 10, padding: "32px 0", color: "rgba(184,192,212,0.45)", fontSize: "0.85rem" }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: "spin 1.2s linear infinite", flexShrink: 0 }}>
        <path d="M21 12a9 9 0 1 1-6.219-8.56" />
      </svg>
      {label}
    </section>
  );
}

function ChipList({ items, tone = "default", emptyLabel }) {
  if (!items?.length) {
    return <p className="results-empty-note">{emptyLabel}</p>;
  }
  return (
    <div className="keyword-chips">
      {items.map((item) => (
        <span key={typeof item === "string" ? item : JSON.stringify(item)} className={`keyword-chip ${tone}`}>
          {typeof item === "string" ? item : item.responsibility}
        </span>
      ))}
    </div>
  );
}

function FeedbackIcon({ type }) {
  if (type === "ok") {
    return (
      <svg className="feedback-icon ok" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
        <polyline points="20 6 9 17 4 12" />
      </svg>
    );
  }
  return (
    <svg className="feedback-icon warn" width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round">
      <line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
      <circle cx="12" cy="12" r="10" />
    </svg>
  );
}

function ScoreSignalCard({ label, value, description, icon }) {
  const [animated, setAnimated] = useState(0);
  const score = Math.round(Math.max(0, Math.min(100, value)));
  const color = score >= 70 ? "#4ade80" : score >= 45 ? "#ffd166" : "#ff7070";
  const strength = score >= 70 ? "Strong" : score >= 45 ? "Partial" : "Low";
  const radius = 26;
  const circ = 2 * Math.PI * radius;
  const offset = circ - (animated / 100) * circ;

  useEffect(() => {
    let start = null;
    let raf;
    const step = (ts) => {
      if (!start) start = ts;
      const p = Math.min((ts - start) / 1100, 1);
      setAnimated(Math.round((1 - Math.pow(1 - p, 3)) * score));
      if (p < 1) { raf = requestAnimationFrame(step); }
    };
    raf = requestAnimationFrame(step);
    return () => cancelAnimationFrame(raf);
  }, [score]);

  return (
    <div className="signal-card">
      <div className="signal-card-head">
        <div className="signal-icon">{icon}</div>
        <div className="signal-label-wrap">
          <span className="signal-label">{label}</span>
          <span className="signal-desc">{description}</span>
        </div>
      </div>
      <div className="signal-card-foot">
        <div className="signal-ring-wrap">
          <svg viewBox="0 0 64 64" fill="none" className="signal-ring-svg">
            <circle cx="32" cy="32" r={radius} stroke="rgba(255,255,255,0.07)" strokeWidth="5" />
            <circle
              cx="32" cy="32" r={radius}
              stroke={color} strokeWidth="5" strokeLinecap="round"
              strokeDasharray={circ} strokeDashoffset={offset}
              transform="rotate(-90 32 32)"
              style={{ filter: `drop-shadow(0 0 6px ${color}99)`, transition: "stroke 0.5s" }}
            />
          </svg>
          <span className="signal-ring-val" style={{ color }}>{animated}</span>
        </div>
        <div className="signal-bar-wrap">
          <div className="signal-bar-track">
            <div className="signal-bar-fill" style={{ width: `${animated}%`, background: color, boxShadow: `0 0 10px ${color}44` }} />
          </div>
          <span className="signal-strength-label" style={{ color }}>{strength}</span>
        </div>
      </div>
    </div>
  );
}

function CombinedMatchPanel({ matched = [], missing = [], atsKeywords = {}, responsibilityDetail = {} }) {
  const parseEvidence = (evidence) => {
    if (!evidence) return { label: null, text: "" };
    const m = evidence.match(/^\[([^\]]+)\]\s*(.*)/s);
    if (m) return { label: m[1].trim(), text: m[2].trim() };
    return { label: null, text: evidence };
  };

  const strong = matched.filter(r => r.confidence === "strong");
  const partial = matched.filter(r => r.confidence === "partial");
  const sorted = [...strong, ...partial];

  const total = responsibilityDetail.total_responsibilities || (matched.length + missing.length);
  const strongCount = strong.length;
  const partialCount = partial.length;
  const missingCount = missing.length;
  const strongPct = total ? (strongCount / total) * 100 : 0;
  const partialPct = total ? (partialCount / total) * 100 : 0;
  const missingPct = total ? (missingCount / total) * 100 : 0;

  const hardSkills = atsKeywords.hard_skills || [];
  const softSkills = atsKeywords.soft_skills || [];
  const allSkills = [...hardSkills, ...softSkills];
  const presentCount = allSkills.filter(s => s.status === "present").length;
  const lowCount = allSkills.filter(s => s.status === "low").length;
  const atsMissingCount = allSkills.filter(s => s.status === "missing").length;
  const atsTotal = allSkills.length;
  const atsCoveragePct = atsTotal ? Math.round((presentCount / atsTotal) * 100) : 0;
  const maxJd = Math.max(...allSkills.map(s => s.jd_count), 1);
  const hasAts = hardSkills.length > 0 || softSkills.length > 0;

  return (
    <section className="results-block rm-block">
      <div className="results-block-header">
        <div className="results-block-icon keyword-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M9 11l3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
          </svg>
        </div>
        <div>
          <h2 className="results-block-title">Responsibility & Keyword Match</h2>
          <p className="results-block-sub">How well your CV proves each stated requirement, and how closely it mirrors the job&apos;s exact language.</p>
        </div>
      </div>

      <div className="rm-coverage">
        <div className="rm-coverage-stats">
          <span className="rm-cov-strong">{strongCount} strong</span>
          <span className="rm-cov-sep">·</span>
          <span className="rm-cov-partial">{partialCount} partial</span>
          <span className="rm-cov-sep">·</span>
          <span className="rm-cov-missing">{missingCount} missing</span>
          <span className="rm-cov-total">of {total} responsibilities</span>
          <span className="rm-score-note">Partial counts as 55% — add explicit evidence to upgrade</span>
        </div>
        <div className="rm-cov-bar rm-cov-bar--segmented">
          <div className="rm-cov-seg rm-cov-seg--strong" style={{ width: `${strongPct}%` }} />
          <div className="rm-cov-seg rm-cov-seg--partial" style={{ width: `${partialPct}%` }} />
          <div className="rm-cov-seg rm-cov-seg--missing" style={{ width: `${missingPct}%` }} />
        </div>
      </div>

      <div className="rm-two-col">
        <div className="rm-col">
          <div className="rm-col-header rm-col-header--have">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
            What you have
            <span className="rm-col-count rm-col-count--have">{matched.length}</span>
          </div>
          {sorted.length === 0
            ? <p className="rm-col-empty">No matches found.</p>
            : sorted.map((item, i) => {
                const { label, text } = parseEvidence(item.evidence);
                const isPartial = item.confidence === "partial";
                const isEssential = item.category !== "nice_to_have";
                return (
                  <div key={i} className={`rm-row rm-row--have${isPartial ? " partial" : ""}`}>
                    <span className={`rm-dot rm-dot--${item.confidence}`} />
                    <div className="rm-row-body">
                      <div className="rm-row-top">
                        <span className="rm-row-resp">{item.responsibility}</span>
                        <span className={`rm-cat-tag${isEssential ? " rm-cat-tag--essential" : " rm-cat-tag--nice"}`}>
                          {isEssential ? "Essential" : "Nice to have"}
                        </span>
                        {isPartial && <span className="rm-partial-tag">Partial</span>}
                      </div>
                      {text && (
                        <p className="rm-row-evidence">
                          {label && <span className="rm-row-label">{label} · </span>}
                          {text}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })
          }
        </div>

        <div className="rm-col">
          <div className="rm-col-header rm-col-header--missing">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
            What you&apos;re missing
            <span className="rm-col-count rm-col-count--missing">{missing.length}</span>
          </div>
          {missing.length === 0
            ? <p className="rm-col-empty">Full coverage — no gaps.</p>
            : missing.map((item, i) => {
                const isEssential = item.category !== "nice_to_have";
                return (
                  <div key={i} className="rm-row rm-row--missing">
                    <span className="rm-dot rm-dot--missing" />
                    <div className="rm-row-body">
                      <div className="rm-row-top">
                        <span className="rm-row-resp rm-row-resp--missing">{typeof item === "string" ? item : item.responsibility}</span>
                        <span className={`rm-cat-tag${isEssential ? " rm-cat-tag--essential" : " rm-cat-tag--nice"}`}>
                          {isEssential ? "Essential" : "Nice to have"}
                        </span>
                      </div>
                      {item.gap && <p className="rm-row-gap">{item.gap}</p>}
                    </div>
                  </div>
                );
              })
          }
        </div>
      </div>

      {hasAts && (
        <div className="ats-panel">
          <div className="ats-panel-header">
            <div>
              <h3 className="ats-panel-title">ATS Keyword Coverage</h3>
              <p className="ats-panel-sub">Keywords the ATS scans for in this role</p>
            </div>
            <div className="ats-score-badge">
              <span className="ats-score-num">{atsCoveragePct}%</span>
              <span className="ats-score-label">covered</span>
            </div>
          </div>
          <div className="ats-bar">
            <div className="ats-bar-fill ats-bar-fill--present" style={{ width: `${atsTotal ? (presentCount / atsTotal) * 100 : 0}%` }} />
            <div className="ats-bar-fill ats-bar-fill--low" style={{ width: `${atsTotal ? (lowCount / atsTotal) * 100 : 0}%` }} />
          </div>
          <div className="ats-groups">
            {presentCount > 0 && (
              <div className="ats-group ats-group--present">
                <div className="ats-group-header">
                  <span className="ats-group-dot" />In your CV<span className="ats-group-count">{presentCount}</span>
                </div>
                <div className="ats-chips">
                  {allSkills.filter(s => s.status === "present").map((s, i) => (
                    <span key={i} className="ats-chip ats-chip--present">{s.skill}</span>
                  ))}
                </div>
              </div>
            )}
            {lowCount > 0 && (
              <div className="ats-group ats-group--low">
                <div className="ats-group-header">
                  <span className="ats-group-dot" />Needs more<span className="ats-group-count">{lowCount}</span>
                </div>
                <div className="ats-chips">
                  {allSkills.filter(s => s.status === "low").map((s, i) => (
                    <span key={i} className="ats-chip ats-chip--low">{s.skill}</span>
                  ))}
                </div>
              </div>
            )}
            {atsMissingCount > 0 && (
              <div className="ats-group ats-group--missing">
                <div className="ats-group-header">
                  <span className="ats-group-dot" />Missing<span className="ats-group-count">{atsMissingCount}</span>
                </div>
                <div className="ats-chips">
                  {allSkills.filter(s => s.status === "missing").map((s, i) => (
                    <span key={i} className="ats-chip ats-chip--missing">{s.skill}</span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );
}

function SkillsMatchPanel({ skillsDetail = {} }) {
  const mustHave    = skillsDetail.must_have    || [];
  const niceToHave  = skillsDetail.nice_to_have || [];

  const parseWhere = (cv_where) => {
    if (!cv_where) return { label: null, text: null };
    const m = cv_where.match(/^\[([^\]]+)\]\s*(.*)/s);
    if (m) return { label: m[1].trim(), text: m[2].trim() };
    return { label: null, text: cv_where };
  };

  const SkillRow = ({ item, isMust }) => {
    const { label, text } = parseWhere(item.cv_where);
    return (
      <div className={`sk-row${item.present ? "" : " sk-row--missing"}${isMust && !item.present ? " sk-row--critical" : ""}`}>
        <span className={`sk-dot ${item.present ? "present" : (isMust ? "critical" : "missing")}`} />
        <div className="sk-row-body">
          <div className="sk-row-top">
            <span className="sk-skill">{item.skill}</span>
            {isMust && !item.present && <span className="sk-must-tag">Required</span>}
          </div>
          {item.present && text && (
            <p className="sk-evidence">
              {label && <span className="sk-evidence-label">{label} · </span>}
              {text}
            </p>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="sk-two-col">
      <div className="sk-col">
        <div className="sk-col-header sk-col-header--have">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
          What you have
          <span className="sk-col-count sk-col-count--have">
            {[...mustHave, ...niceToHave].filter(s => s.present).length}
          </span>
        </div>
        {[...mustHave, ...niceToHave].filter(s => s.present).length === 0
          ? <p className="sk-col-empty">No skills matched.</p>
          : <>
              {mustHave.filter(s => s.present).map((item, i) => <SkillRow key={i} item={item} isMust={true} />)}
              {niceToHave.filter(s => s.present).map((item, i) => <SkillRow key={i} item={item} isMust={false} />)}
            </>
        }
      </div>

      <div className="sk-col">
        <div className="sk-col-header sk-col-header--missing">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          What you&apos;re missing
          <span className="sk-col-count sk-col-count--missing">
            {[...mustHave, ...niceToHave].filter(s => !s.present).length}
          </span>
        </div>
        {[...mustHave, ...niceToHave].filter(s => !s.present).length === 0
          ? <p className="sk-col-empty">All skills matched.</p>
          : <>
              {mustHave.filter(s => !s.present).map((item, i) => <SkillRow key={i} item={item} isMust={true} />)}
              {niceToHave.filter(s => !s.present).map((item, i) => <SkillRow key={i} item={item} isMust={false} />)}
            </>
        }
      </div>
    </div>
  );
}

function SimpleList({ items, emptyLabel }) {
  if (!items?.length) {
    return <p className="results-empty-note">{emptyLabel}</p>;
  }
  return (
    <div className="simple-list">
      {items.map((item) => (
        <div key={item} className="simple-list-item">{item}</div>
      ))}
    </div>
  );
}

function SectionCard({ name, feedback }) {
  const [open, setOpen] = useState(false);
  const { good = [], not_good: notGood = [] } = feedback;
  const hasIssues = notGood.length > 0;
  const isMissing = good.length === 0 && notGood.length === 1 && notGood[0]?.includes("missing or too short");

  return (
    <div className={`section-card${hasIssues ? " has-issues" : " all-good"}${isMissing ? " section-missing" : ""}`}>
      <button type="button" className="section-card-header" onClick={() => setOpen((value) => !value)}>
        <div className="section-card-left">
          <span className={`section-card-dot${isMissing ? " dot-missing" : hasIssues ? " dot-warn" : " dot-ok"}`} />
          <span className="section-card-name">{SECTION_LABELS[name] || name}</span>
          {isMissing ? (
            <span className="badge badge-missing">Not found</span>
          ) : (
            <div className="section-card-badges">
              {good.length > 0 ? <span className="badge badge-ok">{good.length} good</span> : null}
              {notGood.length > 0 ? <span className="badge badge-warn">{notGood.length} to fix</span> : null}
            </div>
          )}
        </div>
        <span className={`section-card-chevron${open ? " open" : ""}`}>
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </span>
      </button>

      <div className={`section-card-body${open ? " open" : ""}`}>
        <div className="section-card-body-inner">
          {good.map((item) => (
            <div key={item} className="section-feedback-item ok">
              <FeedbackIcon type="ok" />
              <span>{item}</span>
            </div>
          ))}
          {notGood.map((item) => (
            <div key={item} className="section-feedback-item warn">
              <FeedbackIcon type="warn" />
              <span>{item}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

const STRENGTH_META = {
  strong:  { label: "Strong match",   cls: "bf-strength strong"  },
  partial: { label: "Partial match",  cls: "bf-strength partial" },
  missing: { label: "Not addressed",  cls: "bf-strength missing" },
};

const DECISION_META = {
  shortlist: { label: "Strong candidate",    cls: "rv-verdict--shortlist", icon: "✓" },
  maybe:     { label: "Improvements needed", cls: "rv-verdict--maybe",     icon: "∼" },
  pass:      { label: "Improvements needed", cls: "rv-verdict--pass",      icon: "∼" },
};

function CombinedFitPanel({ fit, recruiterData }) {
  if (!fit && !recruiterData) return null;

  const {
    how_cv_solves = [],
    cv_strengths = [],
    cv_gaps = [],
    positioning_note = "",
  } = fit || {};

  const {
    verdict = null,
    company_fit = "",
    role_fit = "",
    quick_wins = [],
    screening_keywords = [],
    red_flags = [],
    green_flags = [],
  } = recruiterData || {};

  const decision = verdict?.decision || "maybe";
  const meta = DECISION_META[decision] || DECISION_META.maybe;

  return (
    <section className="results-block bf-panel">
      <div className="results-block-header">
        <div className="results-block-icon keyword-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <rect x="2" y="3" width="20" height="14" rx="2" />
            <path d="M8 21h8M12 17v4" />
          </svg>
        </div>
        <div>
          <h2 className="results-block-title">Recruiter Analysis</h2>
          <p className="results-block-sub">How a recruiter reads your CV against this company and role.</p>
        </div>
      </div>

      {verdict && (
        <div className={`rv-verdict ${meta.cls}`}>
          <div className="rv-verdict-top">
            <span className="rv-verdict-icon">{meta.icon}</span>
            <span className="rv-verdict-label">{meta.label}</span>
          </div>
          {verdict.reasoning && (
            <p className="rv-verdict-reasoning">{verdict.reasoning}</p>
          )}
        </div>
      )}

      {(company_fit || role_fit) && (
        <div className="rv-fit-row">
          {company_fit && (
            <div className="rv-fit-card">
              <div className="rv-fit-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><rect x="2" y="3" width="20" height="14" rx="2"/><path d="M8 21h8M12 17v4"/></svg>
                Company fit
              </div>
              <p className="rv-fit-text">{company_fit}</p>
            </div>
          )}
          {role_fit && (
            <div className="rv-fit-card">
              <div className="rv-fit-label">
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M12 2L2 7l10 5 10-5-10-5z"/><path d="M2 17l10 5 10-5"/><path d="M2 12l10 5 10-5"/></svg>
                Role fit
              </div>
              <p className="rv-fit-text">{role_fit}</p>
            </div>
          )}
        </div>
      )}


      {how_cv_solves.length > 0 && (
        <div className="results-subsection">
          <h3>Where your experience lands</h3>
          <div className="bf-mapping">
            {how_cv_solves.map((item, i) => {
              const strengthMeta = STRENGTH_META[item.strength] || STRENGTH_META.partial;
              return (
                <div key={i} className="bf-mapping-row">
                  <div className="bf-mapping-top">
                    <span className="bf-mapping-problem">{item.problem}</span>
                    <span className={strengthMeta.cls}>{strengthMeta.label}</span>
                  </div>
                  {item.cv_evidence && (
                    <p className="bf-mapping-evidence">{item.cv_evidence}</p>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}

      <div className="bf-two-col">
        {cv_strengths.length > 0 && (
          <div className="results-subsection">
            <h3>What works in your favour</h3>
            <div className="bf-list strengths">
              {cv_strengths.map((s, i) => (
                <div key={i} className="bf-item strength">
                  <span className="bf-item-icon">
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12" /></svg>
                  </span>
                  {s}
                </div>
              ))}
            </div>
          </div>
        )}
        {cv_gaps.length > 0 && (
          <div className="results-subsection">
            <h3>What is missing</h3>
            <div className="bf-list gaps">
              {cv_gaps.map((g, i) => (
                <div key={i} className="bf-item gap">
                  <span className="bf-item-icon">
                    <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                  </span>
                  {g}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {quick_wins.length > 0 && (
        <div className="results-subsection">
          <h3>Changes to make before you apply</h3>
          <div className="rv-quick-wins">
            {quick_wins.map((w, i) => (
              <div key={i} className="rv-qw-item">
                <span className="rv-qw-num">{i + 1}</span>
                <div className="rv-qw-body">
                  {w.cv_section && <span className="rv-qw-section">{w.cv_section}</span>}
                  <span className="rv-qw-action">{w.action}</span>
                  <span className="rv-qw-why">{w.why}</span>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {(positioning_note || screening_keywords.length > 0) && (
        <div className="results-subsection">
          {positioning_note && <div className="bf-positioning">{positioning_note}</div>}
          {screening_keywords.length > 0 && (
            <>
              <h3 style={{ marginTop: positioning_note ? "16px" : 0 }}>Keywords to add</h3>
              <p className="rv-keywords-note">Terms a recruiter or ATS would search for this role that are absent or buried in your CV.</p>
              <div className="keyword-chips">
                {screening_keywords.map((kw, i) => (
                  <span key={i} className="keyword-chip rv-keyword-chip">{kw}</span>
                ))}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}

function CompanyInsightsPanel({ insights }) {
  if (!insights) return null;
  const {
    company_name = "",
    why_hiring_now = {},
    company_momentum = [],
    current_focus = [],
    watch_outs = [],
    apply_intel = [],
    grounded = false,
  } = insights;

  const whyReason = why_hiring_now?.reason || "";
  const whySource = why_hiring_now?.source || "";
  const whyConfidence = why_hiring_now?.confidence || "low";

  const confidenceColor = { high: "#34d399", medium: "#fbbf24", low: "#94a3b8" };
  const confidenceLabel = { high: "Based on real news", medium: "Based on signals", low: "Inferred from JD" };

  const hasContent = whyReason || company_momentum.length > 0 || apply_intel.length > 0;

  return (
    <section className="results-block ci-panel">
      <div className="results-block-header">
        <div className="results-block-icon keyword-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="10"/>
            <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"/>
          </svg>
        </div>
        <div>
          <h2 className="results-block-title">Company Intelligence</h2>
          <p className="results-block-sub">
            {company_name} — what you need to know before applying
            {grounded && <span className="ci-grounded-badge">Live search</span>}
          </p>
        </div>
      </div>

      {!hasContent && <p className="ci-empty">No specific intelligence found for {company_name}.</p>}

      {whyReason && (
        <div className="ci-why-hiring">
          <div className="ci-why-header">
            <span className="ci-why-label">Why they&apos;re hiring now</span>
            <span className="ci-why-confidence" style={{ color: confidenceColor[whyConfidence] }}>
              {confidenceLabel[whyConfidence]}
            </span>
          </div>
          <p className="ci-why-text">{whyReason}</p>
        </div>
      )}

      <div className="ci-main-grid">
        {company_momentum.length > 0 && (
          <div className="results-subsection">
            <h3>Company momentum</h3>
            <div className="ci-momentum-list">
              {company_momentum.map((item, i) => (
                <div key={i} className="ci-momentum-card">
                  <div className="ci-momentum-top">
                    <p className="ci-momentum-fact">{item.fact}</p>
                    {item.date && <span className="ci-momentum-date">{item.date}</span>}
                  </div>
                  {item.candidate_relevance && (
                    <p className="ci-momentum-relevance">{item.candidate_relevance}</p>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {current_focus.length > 0 && (
          <div className="results-subsection">
            <h3>What they&apos;re working on</h3>
            <ul className="ci-focus-list">
              {current_focus.map((point, i) => (
                <li key={i} className="ci-focus-item">{point}</li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {apply_intel.length > 0 && (
        <div className="results-subsection ci-apply-intel">
          <h3>How to stand out</h3>
          <div className="ci-developments">
            {apply_intel.map((d, i) => (
              <div key={i} className="ci-dev-item">
                <span className="ci-dev-dot ci-dev-dot--blue" />
                <p className="ci-dev-text">{d}</p>
              </div>
            ))}
          </div>
        </div>
      )}

      {watch_outs.length > 0 && (
        <div className="ci-watchouts">
          <span className="ci-watchouts-label">Watch outs</span>
          <div className="ci-developments">
            {watch_outs.map((d, i) => (
              <div key={i} className="ci-dev-item">
                <span className="ci-dev-dot ci-dev-dot--amber" />
                <p className="ci-dev-text">{d}</p>
              </div>
            ))}
          </div>
        </div>
      )}

    </section>
  );
}

function InterviewPrepPanel({ data }) {
  const [openIdx, setOpenIdx] = useState(null);

  const SECTIONS = [
    {
      key: "role_questions",
      label: "Role-Specific Questions",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      ),
      accent: "#5ee4ff",
      items: (data.role_questions || []).map((q) => ({
        question: q.question,
        lines: [
          { label: "Why asked", text: q.why_asked },
          { label: "Tip", text: q.tip },
        ],
      })),
    },
    {
      key: "behavioral",
      label: "Behavioural Questions",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>
          <path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
        </svg>
      ),
      accent: "#a78bfa",
      items: (data.behavioral || []).map((q) => ({
        question: q.question,
        badge: q.competency,
        lines: [
          { label: "STAR hint", text: q.star_hint },
        ],
      })),
    },
    {
      key: "cv_deep_dive",
      label: "CV Deep Dive",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/>
        </svg>
      ),
      accent: "#4ade80",
      items: (data.cv_deep_dive || []).map((q) => ({
        question: q.question,
        lines: [
          { label: "Based on", text: q.cv_reference },
          { label: "Tip", text: q.tip },
        ],
      })),
    },
    {
      key: "gap_challenges",
      label: "Tough Questions",
      icon: (
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/>
          <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
        </svg>
      ),
      accent: "#fb923c",
      items: (data.gap_challenges || []).map((q) => ({
        question: q.question,
        lines: [
          { label: "Gap", text: q.gap },
          { label: "How to handle", text: q.how_to_handle },
        ],
      })),
    },
  ].filter((s) => s.items.length > 0);

  return (
    <section className="results-block ip-block">
      <div className="results-block-header">
        <div className="results-block-icon" style={{ background: "rgba(167,139,250,0.12)", color: "#a78bfa" }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
        </div>
        <div>
          <h2 className="results-block-title">Interview Prep</h2>
          <p className="results-block-sub">Questions tailored to your CV and this specific role — click each to see tips.</p>
        </div>
      </div>

      <div className="ip-sections">
        {SECTIONS.map((sec) => (
          <div key={sec.key} className="ip-section">
            <div className="ip-section-header" style={{ borderColor: sec.accent + "33" }}>
              <span className="ip-section-icon" style={{ color: sec.accent, background: sec.accent + "18" }}>{sec.icon}</span>
              <span className="ip-section-label" style={{ color: sec.accent }}>{sec.label}</span>
              <span className="ip-section-count">{sec.items.length}</span>
            </div>
            <div className="ip-questions">
              {sec.items.map((item, i) => {
                const key = `${sec.key}-${i}`;
                const isOpen = openIdx === key;
                return (
                  <div key={key} className={`ip-q${isOpen ? " ip-q--open" : ""}`}>
                    <button className="ip-q-head" onClick={() => setOpenIdx(isOpen ? null : key)}>
                      <span className="ip-q-num" style={{ color: sec.accent }}>{i + 1}</span>
                      <span className="ip-q-text">{item.question}</span>
                      {item.badge && <span className="ip-q-badge" style={{ background: sec.accent + "22", color: sec.accent }}>{item.badge}</span>}
                      <svg className="ip-q-chevron" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                        <polyline points="6 9 12 15 18 9"/>
                      </svg>
                    </button>
                    {isOpen && (
                      <div className="ip-q-body">
                        {item.lines.map((l, j) => l.text ? (
                          <div key={j} className="ip-q-line">
                            <span className="ip-q-line-label">{l.label}</span>
                            <span className="ip-q-line-text">{l.text}</span>
                          </div>
                        ) : null)}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function CvHighlightPanel({ highlights }) {
  const [activeSection, setActiveSection] = useState(null);

  useEffect(() => {
    if (highlights?.length && !activeSection) {
      setActiveSection(highlights[0].section);
    }
  }, [highlights, activeSection]);

  if (!highlights?.length) return null;

  const activeBlock = highlights.find((b) => b.section === activeSection) || highlights[0];
  const totalStrong = highlights.reduce((n, b) => n + b.lines.filter((l) => l.quality === "strong").length, 0);
  const totalGood   = highlights.reduce((n, b) => n + b.lines.filter((l) => l.quality === "good").length, 0);
  const totalWeak   = highlights.reduce((n, b) => n + b.lines.filter((l) => l.quality === "weak").length, 0);

  const blockStrong = activeBlock?.lines.filter((l) => l.quality === "strong").length || 0;
  const blockGood   = activeBlock?.lines.filter((l) => l.quality === "good").length || 0;
  const blockWeak   = activeBlock?.lines.filter((l) => l.quality === "weak").length || 0;

  return (
    <section className="results-block cvr-block">
      <div className="cvr-header">
        <div className="cvr-header-left">
          <div className="results-block-icon section-icon">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
              <polyline points="14 2 14 8 20 8" />
              <line x1="16" y1="13" x2="8" y2="13" />
              <line x1="16" y1="17" x2="8" y2="17" />
            </svg>
          </div>
          <div>
            <h2 className="results-block-title">CV Review</h2>
            <p className="results-block-sub">Line-by-line quality analysis across every section of your CV.</p>
          </div>
        </div>
        <div className="cvr-totals">
          <div className="cvr-total strong">
            <span className="cvr-total-num">{totalStrong}</span>
            <span>strong</span>
          </div>
          <div className="cvr-total good">
            <span className="cvr-total-num">{totalGood}</span>
            <span>good</span>
          </div>
          <div className="cvr-total weak">
            <span className="cvr-total-num">{totalWeak}</span>
            <span>to fix</span>
          </div>
        </div>
      </div>

      <div className="cvr-tabs">
        {highlights.map((block) => {
          const wCount = block.lines.filter((l) => l.quality === "weak").length;
          const sCount = block.lines.filter((l) => l.quality === "strong").length;
          const isActive = block.section === (activeSection || highlights[0].section);
          return (
            <button
              key={block.section}
              type="button"
              className={`cvr-tab${isActive ? " active" : ""}`}
              onClick={() => setActiveSection(block.section)}
            >
              <span className="cvr-tab-label">{block.section_label}</span>
              {wCount > 0 && <span className="cvr-tab-badge warn">{wCount}</span>}
              {wCount === 0 && sCount > 0 && (
                <span className="cvr-tab-badge ok">
                  <svg width="9" height="9" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                </span>
              )}
            </button>
          );
        })}
      </div>

      {activeBlock && (
        <div className="cvr-viewer">
          <div className="cvr-viewer-toolbar">
            <span className="cvr-file-label">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <polyline points="14 2 14 8 20 8" />
              </svg>
              {activeBlock.section_label}
            </span>
            <div className="cvr-toolbar-stats">
              {blockStrong > 0 && <span className="cvr-stat cvr-stat--strong">{blockStrong} strong</span>}
              {blockGood   > 0 && <span className="cvr-stat cvr-stat--good">{blockGood} good</span>}
              {blockWeak   > 0 && <span className="cvr-stat cvr-stat--weak">{blockWeak} to fix</span>}
              <span className="cvr-stat cvr-stat--total">{activeBlock.lines.length} lines</span>
            </div>
          </div>

          <div className="cvr-lines">
            {activeBlock.lines.map((line, idx) => {
              const q = line.quality || "neutral";
              return (
                <div key={idx} className={`cvr-line cvr-line--${q}`}>
                  <span className="cvr-line-gutter" />
                  <span className="cvr-line-num">{idx + 1}</span>
                  <div className="cvr-line-content">
                    <span className="cvr-line-text">{line.text}</span>
                    {line.reason && q !== "neutral" && (
                      <span className="cvr-line-note">{line.reason}</span>
                    )}
                  </div>
                  {q !== "neutral" && (
                    <span className={`cvr-line-badge cvr-line-badge--${q}`}>
                      {q === "strong" ? "Strong" : q === "good" ? "Good" : "Weak"}
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}

function MiniScoreRing({ score, grade }) {
  const s = typeof score === "number" ? Math.max(0, Math.min(100, score)) : null;
  const color = s === null ? "#888" : s >= 75 ? "#4ade80" : s >= 50 ? "#ffd166" : "#ff7070";
  const radius = 22;
  const circ = 2 * Math.PI * radius;
  const offset = s === null ? circ : circ - (s / 100) * circ;
  return (
    <div className="mini-ring-wrap">
      <svg className="mini-ring-svg" viewBox="0 0 56 56" fill="none">
        <circle cx="28" cy="28" r={radius} stroke="rgba(255,255,255,0.08)" strokeWidth="5" />
        {s !== null && (
          <circle cx="28" cy="28" r={radius} stroke={color} strokeWidth="5" strokeLinecap="round"
            strokeDasharray={circ} strokeDashoffset={offset} transform="rotate(-90 28 28)"
            style={{ filter: `drop-shadow(0 0 5px ${color}66)` }} />
        )}
      </svg>
      <div className="mini-ring-center">
        {s !== null ? <span className="mini-ring-val" style={{ color }}>{s}</span> : null}
        {grade ? <span className="mini-ring-grade" style={{ color }}>{grade}</span> : null}
      </div>
    </div>
  );
}

function CandidateProfileBar({ profile }) {
  if (!profile) return null;
  const { seniority_level, industry_domains = [], location, management_experience = {} } = profile;
  const hasContent = seniority_level || industry_domains.length || location || management_experience?.has_managed;
  if (!hasContent) return null;
  return (
    <div className="candidate-profile-bar">
      {seniority_level && (
        <span className="profile-badge seniority">{seniority_level.charAt(0).toUpperCase() + seniority_level.slice(1)}</span>
      )}
      {industry_domains.slice(0, 4).map((d) => (
        <span key={d} className="profile-badge domain">{d}</span>
      ))}
      {location && <span className="profile-badge location">📍 {location}</span>}
      {management_experience?.has_managed && (
        <span className="profile-badge management">
          {management_experience.max_team_size ? `Managed team of ${management_experience.max_team_size}` : "People manager"}
        </span>
      )}
    </div>
  );
}

function BulletRow({ bullet }) {
  const [showRewrite, setShowRewrite] = useState(false);
  const qualityCls = { strong: "bullet-strong", good: "bullet-good", weak: "bullet-weak" }[bullet.quality] || "";
  return (
    <div className={`bullet-row ${qualityCls}`}>
      <div className="bullet-row-top">
        <span className={`bullet-dot ${qualityCls}`} />
        <span className="bullet-text">{bullet.text}</span>
        {bullet.quality === "weak" && bullet.rewrite && (
          <button className="bullet-rewrite-toggle" onClick={() => setShowRewrite((v) => !v)}>
            {showRewrite ? "Hide rewrite" : "Suggest rewrite"}
          </button>
        )}
      </div>
      {bullet.issue && bullet.quality !== "strong" && (
        <p className="bullet-issue">{bullet.issue}</p>
      )}
      {showRewrite && bullet.rewrite && (
        <div className="bullet-rewrite-box">{bullet.rewrite}</div>
      )}
    </div>
  );
}

function ExperienceRoleCard({ role }) {
  const [open, setOpen] = useState(false);
  const weakCount = (role.bullets || []).filter((b) => b.quality === "weak").length;
  return (
    <div className="exp-role-card">
      <button className="exp-role-header" onClick={() => setOpen((v) => !v)}>
        <div className="exp-role-left">
          <span className="exp-role-title">{role.title}</span>
          <span className="exp-role-company">{role.company}</span>
          {role.dates && <span className="exp-role-dates">{role.dates}</span>}
        </div>
        <div className="exp-role-right">
          {role.role_score != null && (
            <span className="exp-role-score" style={{ color: role.role_score >= 70 ? "#4ade80" : role.role_score >= 45 ? "#ffd166" : "#ff7070" }}>
              {role.role_score}
            </span>
          )}
          {weakCount > 0 && <span className="exp-role-badge warn">{weakCount} to improve</span>}
          <span className={`section-card-chevron${open ? " open" : ""}`}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <polyline points="6 9 12 15 18 9" />
            </svg>
          </span>
        </div>
      </button>
      <div className={`exp-role-body-wrap${open ? " open" : ""}`}>
        <div className="exp-role-body">
          {role.quantification_rate && <p className="exp-quant-rate">{role.quantification_rate}</p>}
          {(role.bullets || []).map((b, i) => <BulletRow key={i} bullet={b} />)}
        </div>
      </div>
    </div>
  );
}

const SECTION_TAB_LABELS = { intro: "Intro", skills: "Skills", experience: "Experience", education: "Education", projects: "Projects" };

function CvIntelligencePanel({ analysis, profile }) {
  const [activeTab, setActiveTab] = useState("intro");
  if (!analysis || Object.keys(analysis).length === 0) return null;

  const { overall_quality_score, career_narrative, ats_compatibility = {}, red_flags = [], sections = {} } = analysis;
  const tabs = ["intro", "skills", "experience", "education", ...(sections.projects ? ["projects"] : [])];
  const activeSection = sections[activeTab] || {};

  return (
    <section className="results-block cv-intelligence">
      <div className="results-block-header">
        <div className="results-block-icon section-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4l3 3" />
          </svg>
        </div>
        <div>
          <h2 className="results-block-title">CV Intelligence</h2>
          <p className="results-block-sub">Deep section-by-section analysis — scores, issues, and bullet-level rewrites.</p>
        </div>
      </div>

      <div className="cvi-top-row">
        <div className="cvi-quality-block">
          <MiniScoreRing score={overall_quality_score} />
          <div className="cvi-quality-text">
            <span className="cvi-quality-label">CV Quality Score</span>
            {career_narrative && <p className="cvi-narrative">{career_narrative}</p>}
          </div>
        </div>
        <div className="cvi-ats-block">
          <div className="cvi-ats-head">
            <span>ATS Compatibility</span>
            <strong style={{ color: (ats_compatibility.score || 0) >= 70 ? "#4ade80" : "#ffd166" }}>{ats_compatibility.score ?? "—"}</strong>
          </div>
          <div className="breakdown-meter-track" style={{ marginBottom: 8 }}>
            <div className="breakdown-meter-fill" style={{ width: `${Math.max(0, Math.min(100, ats_compatibility.score || 0))}%` }} />
          </div>
          {(ats_compatibility.issues || []).slice(0, 3).map((iss, i) => (
            <p key={i} className="cvi-ats-issue">{iss}</p>
          ))}
        </div>
      </div>

      {red_flags.length > 0 && (
        <div className="cvi-red-flags">
          <span className="cvi-red-flags-label">Red flags</span>
          <div className="keyword-chips">
            {red_flags.map((f, i) => <span key={i} className="keyword-chip warn">{f}</span>)}
          </div>
        </div>
      )}

      <div className="cvi-tabs">
        {tabs.map((tab) => {
          const sec = sections[tab] || {};
          return (
            <button key={tab} className={`cvi-tab${activeTab === tab ? " active" : ""}`} onClick={() => setActiveTab(tab)}>
              {SECTION_TAB_LABELS[tab] || tab}
              {sec.grade && <span className="cvi-tab-grade">{sec.grade}</span>}
            </button>
          );
        })}
      </div>

      <div key={activeTab} className="cvi-section-body">
        {activeTab !== "experience" && activeTab !== "skills" && (
          <>
            <div className="cvi-section-header-row">
              <MiniScoreRing score={activeSection.score} grade={activeSection.grade} />
              <div className="cvi-section-meta">
                <span className="cvi-section-label">{SECTION_TAB_LABELS[activeTab] || activeTab}</span>
                {(activeSection.strengths || []).map((s, i) => (
                  <div key={i} className="section-feedback-item ok">{s}</div>
                ))}
                {(activeSection.issues || []).map((s, i) => (
                  <div key={i} className="section-feedback-item warn">{s}</div>
                ))}
              </div>
            </div>
            {activeTab === "intro" && activeSection.rewrite && (
              <div className="cvi-intro-rewrite">
                <span className="cvi-rewrite-label">Suggested intro rewrite</span>
                <p>{activeSection.rewrite}</p>
              </div>
            )}
          </>
        )}

        {activeTab === "skills" && (
          <>
            <div className="cvi-section-header-row">
              <MiniScoreRing score={activeSection.score} grade={activeSection.grade} />
              <div className="cvi-section-meta">
                {(activeSection.strengths || []).map((s, i) => (
                  <div key={i} className="section-feedback-item ok">{s}</div>
                ))}
                {(activeSection.issues || []).map((s, i) => (
                  <div key={i} className="section-feedback-item warn">{s}</div>
                ))}
              </div>
            </div>
            {(activeSection.jd_skills_present || []).length > 0 && (
              <div className="results-subsection">
                <h3>JD skills you have</h3>
                <div className="keyword-chips">
                  {activeSection.jd_skills_present.map((sk) => <span key={sk} className="keyword-chip">{sk}</span>)}
                </div>
              </div>
            )}
            {(activeSection.jd_skills_missing || []).length > 0 && (
              <div className="results-subsection">
                <h3>JD skills missing from your CV</h3>
                <div className="keyword-chips">
                  {activeSection.jd_skills_missing.map((sk) => <span key={sk} className="keyword-chip warn">{sk}</span>)}
                </div>
              </div>
            )}
            {(activeSection.listed_but_unevidenced || []).length > 0 && (
              <div className="results-subsection">
                <h3>Listed but not evidenced in experience</h3>
                <div className="keyword-chips">
                  {activeSection.listed_but_unevidenced.map((sk) => <span key={sk} className="keyword-chip neutral">{sk}</span>)}
                </div>
              </div>
            )}
          </>
        )}

        {activeTab === "experience" && (
          <>
            <div className="cvi-section-header-row">
              <MiniScoreRing score={activeSection.score} grade={activeSection.grade} />
              <div className="cvi-section-meta">
                {(activeSection.overall_strengths || []).map((s, i) => (
                  <div key={i} className="section-feedback-item ok">{s}</div>
                ))}
                {(activeSection.overall_issues || []).map((s, i) => (
                  <div key={i} className="section-feedback-item warn">{s}</div>
                ))}
              </div>
            </div>
            <div className="exp-roles-list">
              {(activeSection.roles || []).map((role, i) => <ExperienceRoleCard key={i} role={role} />)}
            </div>
          </>
        )}
      </div>
    </section>
  );
}

function RewriteSection({ items, emptyLabel }) {
  if (!items?.length) {
    return <p className="results-empty-note">{emptyLabel}</p>;
  }
  return (
    <div className="rewrite-section-list">
      {items.map((item) => (
        <article key={`${item.heading}-${item.bullets?.join("|")}`} className="rewrite-section-item">
          {item.heading ? <h4>{item.heading}</h4> : null}
          <ul className="rewrite-bullets">
            {(item.bullets || []).map((bullet) => (
              <li key={bullet}>{bullet}</li>
            ))}
          </ul>
        </article>
      ))}
    </div>
  );
}

export default function ResultsPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { result, fileName, jobDescription, isSample } = location.state || {};
  const [rewriteLoading, setRewriteLoading] = useState(false);
  const [rewriteError, setRewriteError] = useState("");
  const [coverLetterLoading, setCoverLetterLoading] = useState(false);
  const [coverLetterError, setCoverLetterError] = useState("");
  const [feedbackRating, setFeedbackRating] = useState(null); // "accurate" | "inaccurate"
  const [feedbackNote, setFeedbackNote] = useState("");
  const [feedbackIssues, setFeedbackIssues] = useState([]);
  const [feedbackSubmitted, setFeedbackSubmitted] = useState(false);
  const [feedbackSubmitting, setFeedbackSubmitting] = useState(false);
  const [activeTab, setActiveTab] = useState("overview");
  const [businessFit, setBusinessFit] = useState(null);
  const [fitError, setFitError] = useState("");
  const [companyInsights, setCompanyInsights] = useState(null);
  const [companyInsightsError, setCompanyInsightsError] = useState("");
  const [companyInsightsLocked, setCompanyInsightsLocked] = useState(false);
  const [companyInsightsLockMessage, setCompanyInsightsLockMessage] = useState("");
  const [recruiterView, setRecruiterView] = useState(null);
  const [interviewPrep, setInterviewPrep] = useState(null);
  const [interviewPrepError, setInterviewPrepError] = useState("");

  useEffect(() => {
    if (!result) { navigate("/analyze"); return; }
  }, [result, navigate]);

  useEffect(() => {
    if (!result || !jobDescription) return;
    if (isSample) {
      const note = "This is a sample analysis — run your own scan to see this section populated with live Gemini insights.";
      setFitError(note);
      setCompanyInsightsError(note);
      setInterviewPrepError(note);
      return;
    }
    const resumeText = result.resume_text;
    const controller = new AbortController();
    const { signal } = controller;
    const timeoutId = setTimeout(() => controller.abort(), 150000);

    const safeJson = (res) =>
      res.ok ? res.json() : res.json().then((d) => Promise.reject(d?.detail || `Error ${res.status}`));

    const wrapFetch = (promise, fallback) =>
      promise.catch((e) => ({
        ok: false,
        error: e?.name === "AbortError" ? "Request timed out." : (typeof e === "string" ? e : fallback),
      }));

    const bfFetch = wrapFetch(
      fetch(`${API_BASE_URL}/business-fit`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_text: resumeText, job_description: jobDescription }),
        signal,
      }).then(safeJson).then((d) => ({ ok: true, data: d.business_fit || null })),
      "Could not run business fit analysis."
    );

    const ciFetch = wrapFetch(
      fetch(`${API_BASE_URL}/company-insights`, {
        method: "POST", headers: { "Content-Type": "application/json", ...getAuthHeader() },
        body: JSON.stringify({ job_description: jobDescription }),
        signal,
      }).then(safeJson).then((d) => ({
        ok: true,
        data: d.company_insights || null,
        locked: !!d.locked,
        upgrade_message: d.upgrade_message || null,
      })),
      "Could not load company insights."
    );

    const rvFetch = wrapFetch(
      fetch(`${API_BASE_URL}/recruiter-view`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_text: resumeText, job_description: jobDescription, role_fit_breakdown: result.role_fit_breakdown || {} }),
        signal,
      }).then(safeJson).then((d) => ({ ok: true, data: d.recruiter_view || null })),
      "Could not load recruiter view."
    );

    const ipFetch = wrapFetch(
      fetch(`${API_BASE_URL}/interview-prep`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_text: resumeText, job_description: jobDescription, role_fit_breakdown: result.role_fit_breakdown || {} }),
        signal,
      }).then(safeJson).then((d) => ({ ok: true, data: d.interview_prep || null })),
      "Could not load interview prep."
    );

    Promise.all([bfFetch, ciFetch, rvFetch, ipFetch]).then(([bf, ci, rv, ip]) => {
      clearTimeout(timeoutId);
      if (bf.ok) setBusinessFit(bf.data); else setFitError(bf.error);
      if (ci.ok) {
        setCompanyInsights(ci.data);
        if (ci.locked) {
          setCompanyInsightsLocked(true);
          setCompanyInsightsLockMessage(ci.upgrade_message || "Company research is available on the full plan.");
        }
      } else {
        setCompanyInsightsError(ci.error);
      }
      if (rv.ok) setRecruiterView(rv.data);
      if (ip.ok) setInterviewPrep(ip.data); else setInterviewPrepError(ip.error);
    });

    return () => {
      clearTimeout(timeoutId);
      controller.abort();
    };
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (!result) return (
    <div style={{ display: "flex", alignItems: "center", justifyContent: "center", minHeight: "100vh", background: "var(--bg)" }}>
      <div className="az-loading-ring az-ring-1" />
    </div>
  );


  const {
    match_score: matchScore = 0,
    role_fit_breakdown: breakdown = {},
    section_feedback: sectionFeedback = {},
    resume_text: resumeText = "",
    cv_highlights: cvHighlights = [],
    candidate_profile: candidateProfile = null,
    cv_sections_analysis: cvSectionsAnalysis = null,
    ats_keywords: atsKeywords = {},
    score_breakdown: scoreBreakdown = null,
  } = result;
  const sortedSectionKeys = Object.keys(sectionFeedback)
    .filter((k) => {
      const fb = sectionFeedback[k];
      return (fb?.good?.length || 0) + (fb?.not_good?.length || 0) > 0;
    })
    .sort(
      (a, b) => (sectionFeedback[b]?.not_good?.length || 0) - (sectionFeedback[a]?.not_good?.length || 0),
    );
  const jobMeta = breakdown.job_description || {};
  const responsibilityDetail = breakdown.responsibility_detail || {};
  const experienceDetail = breakdown.experience_detail || {};
  const skillsDetail = breakdown.skills_detail || {};

  const handleGenerateRewrite = async () => {
    if (!resumeText || !jobDescription) {
      setRewriteError("The rewrite needs both the extracted CV text and the job description.");
      return;
    }
    setRewriteLoading(true);
    setRewriteError("");
    try {
      const res = await fetch(`${API_BASE_URL}/rewrite-cv`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          job_description: jobDescription,
          role_fit_breakdown: breakdown,
        }),
      });
      if (!res.ok) {
        throw new Error(await readErrorMessage(res));
      }
      const data = await res.json();
      navigate("/cv-rewrite", { state: { rewrite: data.rewrite, fileName } });
    } catch (err) {
      setRewriteError(err.message || "Could not generate a CV rewrite.");
    } finally {
      setRewriteLoading(false);
    }
  };

  const handleGenerateCoverLetter = async () => {
    if (!resumeText || !jobDescription) {
      setCoverLetterError("Cover letter needs both the extracted CV text and the job description.");
      return;
    }
    setCoverLetterLoading(true);
    setCoverLetterError("");
    try {
      const res = await fetch(`${API_BASE_URL}/generate-cover-letter`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...getAuthHeader() },
        body: JSON.stringify({ resume_text: resumeText, job_description: jobDescription }),
      });
      if (!res.ok) {
        throw new Error(await readErrorMessage(res));
      }
      const data = await res.json();
      navigate("/cover-letter", { state: { letter: data.cover_letter, fileName } });
    } catch (err) {
      setCoverLetterError(err.message || "Could not generate a cover letter.");
    } finally {
      setCoverLetterLoading(false);
    }
  };

  return (
    <PageLayout
      navRight={(
        <button
          className="cta-button ghost-button"
          style={{ fontSize: "0.88rem", minHeight: "40px", padding: "0 18px", cursor: "pointer", border: "1px solid rgba(255,255,255,0.16)", background: "rgba(255,255,255,0.05)", color: "var(--text)", borderRadius: "999px" }}
          onClick={() => {
            signOut();
            navigate("/login", { replace: true });
          }}
        >
          Sign out
        </button>
      )}
    >
      <div className="results-page">
        <div className="results-intro">
          <div className="section-kicker">
            {isSample ? "Sample analysis" : "Analysis complete"}
          </div>
          {fileName ? <p className="results-filename">{fileName}</p> : null}
          {isSample && (
            <div style={{
              marginTop: 10,
              display: "inline-flex",
              alignItems: "center",
              gap: 8,
              padding: "6px 12px",
              borderRadius: 999,
              border: "1px solid rgba(94,228,255,0.28)",
              background: "rgba(94,228,255,0.06)",
              color: "#5ee4ff",
              fontSize: "0.74rem",
              fontWeight: 600,
              letterSpacing: "0.02em",
            }}>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                <circle cx="12" cy="12" r="10" />
                <path d="M12 16v-4M12 8h.01" />
              </svg>
              You're viewing a demo — upload your own CV to get a live analysis.
            </div>
          )}
        </div>

        <div className="results-hero">
          <ScoreRing score={Math.round(matchScore)} />

          <div className="results-hero-summary">
            {(() => {
              const s = Math.round(matchScore);
              const sc = scoreColor(s);
              const scBg = `${sc.replace("hsl(", "hsla(").replace(")", ", 0.12)")}`;
              const scBorder = `${sc.replace("hsl(", "hsla(").replace(")", ", 0.3)")}`;
              const verdict = s >= 80 ? { label: "Strong match" }
                : s >= 60   ? { label: "Competitive" }
                : s >= 40   ? { label: "Developing" }
                :              { label: "Needs work" };
              const tagline = s >= 80 ? "Your CV is a strong fit — sharpen the detail and apply with confidence."
                : s >= 60   ? "A solid foundation. Targeted improvements could make this competitive."
                : s >= 40   ? "Relevant experience exists but gaps need addressing before applying."
                :              "Significant gaps between your CV and this role's requirements.";

              const respMatched = responsibilityDetail.matched_count || 0;
              const respTotal   = responsibilityDetail.total_responsibilities || 0;
              const respPct     = respTotal ? Math.round((respMatched / respTotal) * 100) : 0;

              return (
                <>
                  <div className="hero-verdict-row">
                    <span className="hero-verdict-badge" style={{ color: sc, background: scBg, border: `1px solid ${scBorder}` }}>
                      {verdict.label}
                    </span>
                  </div>
                  <h1 className="results-title">
                    Your CV scored <span style={{ color: sc, textShadow: `0 0 28px ${sc}55` }}>{s}</span> for this role.
                  </h1>
                  <p className="results-desc">{tagline}</p>

                  <div className="hero-stats">
                    <div className="hero-stat">
                      <span className="hero-stat-val" style={{ color: scoreColor(respPct) }}>{respMatched}<span className="hero-stat-of">/{respTotal}</span></span>
                      <span className="hero-stat-label">Responsibilities matched</span>
                    </div>
                  </div>

                  <div className="rewrite-cta-row" style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      className="cta-button primary-button results-cta"
                      onClick={isSample ? () => navigate("/analyze") : handleGenerateRewrite}
                      disabled={rewriteLoading || coverLetterLoading}
                    >
                      {isSample
                        ? "Upload your CV to try this"
                        : rewriteLoading
                          ? "Generating rewrite..."
                          : "Generate tailored CV rewrite"}
                    </button>
                    {!isSample && (
                      <button
                        type="button"
                        className="cta-button ghost-button results-cta"
                        onClick={handleGenerateCoverLetter}
                        disabled={coverLetterLoading || rewriteLoading}
                        style={{ minHeight: 48 }}
                      >
                        {coverLetterLoading ? "Writing cover letter..." : "Generate cover letter"}
                      </button>
                    )}
                  </div>
                  {rewriteError ? <p className="rewrite-error">{rewriteError}</p> : null}
                  {coverLetterError ? <p className="rewrite-error">{coverLetterError}</p> : null}
                  {fitError ? <p className="rewrite-error">{fitError}</p> : null}
                </>
              );
            })()}
          </div>
        </div>

        {scoreBreakdown && (scoreBreakdown.factors_pulling_down?.length || scoreBreakdown.factors_pulling_up?.length) ? (
          <section className="results-block score-explainer-block" style={{
            margin: "20px 0 24px",
            padding: "26px 28px",
            border: "1px solid rgba(255,255,255,0.07)",
            borderRadius: 16,
            background: "linear-gradient(180deg, rgba(94,228,255,0.04), rgba(167,139,250,0.025))",
          }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap", marginBottom: 14 }}>
              <h2 style={{ margin: 0, fontSize: "1.15rem", fontWeight: 700, color: "#f4f6fb" }}>
                Why your score is <span style={{ color: "#5ee4ff" }}>{scoreBreakdown.current_score}</span>
              </h2>
              {scoreBreakdown.potential_score > scoreBreakdown.current_score && (
                <span style={{
                  fontSize: "0.82rem", color: "rgba(184,192,212,0.7)",
                }}>
                  → potential: <strong style={{ color: "#4ade80" }}>{scoreBreakdown.potential_score}</strong>
                </span>
              )}
            </div>
            <p style={{ margin: "0 0 18px", fontSize: "0.92rem", color: "rgba(232,237,245,0.85)", lineHeight: 1.55 }}>
              {scoreBreakdown.verdict_line}
            </p>

            {scoreBreakdown.factors_pulling_down?.length > 0 && (
              <div style={{ marginBottom: 16 }}>
                <p style={{ margin: "0 0 10px", fontSize: "0.74rem", fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: "#f87171" }}>
                  Pulling your score down
                </p>
                <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 10 }}>
                  {scoreBreakdown.factors_pulling_down.map((f, i) => (
                    <li key={i} style={{
                      display: "flex", gap: 12, alignItems: "flex-start",
                      padding: "10px 14px",
                      borderRadius: 10,
                      background: "rgba(248,113,113,0.04)",
                      border: "1px solid rgba(248,113,113,0.15)",
                    }}>
                      <span style={{
                        flexShrink: 0,
                        fontSize: "0.78rem", fontWeight: 800,
                        color: "#f87171",
                        padding: "3px 9px", borderRadius: 999,
                        background: "rgba(248,113,113,0.12)", border: "1px solid rgba(248,113,113,0.3)",
                        minWidth: 50, textAlign: "center",
                      }}>−{f.points_lost}</span>
                      <div>
                        <div style={{ fontSize: "0.92rem", fontWeight: 600, color: "#f4f6fb", marginBottom: 2 }}>{f.label}</div>
                        {f.fix && <div style={{ fontSize: "0.84rem", color: "rgba(184,192,212,0.75)", lineHeight: 1.5 }}>{f.fix}</div>}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {scoreBreakdown.factors_pulling_up?.length > 0 && (
              <div>
                <p style={{ margin: "0 0 10px", fontSize: "0.74rem", fontWeight: 700, letterSpacing: "0.12em", textTransform: "uppercase", color: "#4ade80" }}>
                  Working in your favour
                </p>
                <ul style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 6 }}>
                  {scoreBreakdown.factors_pulling_up.map((f, i) => (
                    <li key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", fontSize: "0.88rem", color: "rgba(232,237,245,0.85)" }}>
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 3 }}>
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                      <span>{f}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </section>
        ) : null}

        <CandidateProfileBar profile={candidateProfile} />

        <div className="results-tab-bar">
          <button
            className={`results-tab${activeTab === "overview" ? " active" : ""}`}
            onClick={() => setActiveTab("overview")}
          >
            Overview
          </button>
          <button
            className={`results-tab${activeTab === "details" ? " active" : ""}`}
            onClick={() => setActiveTab("details")}
          >
            Detailed Analysis
          </button>
          <button
            className={`results-tab${activeTab === "interview" ? " active" : ""}`}
            onClick={() => setActiveTab("interview")}
          >
            Interview Prep
          </button>
        </div>

        {activeTab === "overview" && (
          <>
            {(businessFit || recruiterView) ? (
              <CombinedFitPanel fit={businessFit} recruiterData={recruiterView} />
            ) : fitError ? (
              <section className="results-block bf-error-block">
                <p className="rewrite-error" style={{ margin: 0 }}>{fitError}</p>
              </section>
            ) : (
              <PanelSkeleton label="Analysing business fit and recruiter view…" />
            )}

            {companyInsightsLocked ? (
              <section className="results-block" style={{
                display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 14,
                padding: "28px 26px",
                border: "1px solid rgba(94,228,255,0.18)",
                background: "linear-gradient(180deg, rgba(94,228,255,0.05), rgba(167,139,250,0.03))",
              }}>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  <div style={{
                    width: 36, height: 36, borderRadius: 10, display: "flex", alignItems: "center", justifyContent: "center",
                    background: "rgba(94,228,255,0.12)", border: "1px solid rgba(94,228,255,0.28)", color: "#5ee4ff",
                  }}>
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                      <rect x="3" y="11" width="18" height="11" rx="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
                    </svg>
                  </div>
                  <div>
                    <h2 style={{ margin: 0, fontSize: "1.1rem", fontWeight: 700, color: "#f4f6fb" }}>Company intelligence</h2>
                    <p style={{ margin: "2px 0 0", fontSize: "0.82rem", color: "rgba(184,192,212,0.6)" }}>Available on the full plan</p>
                  </div>
                </div>
                <p style={{ margin: 0, fontSize: "0.92rem", color: "rgba(232,237,245,0.85)", lineHeight: 1.6 }}>
                  {companyInsightsLockMessage} Unlock recruiter-grade company research — why they're hiring, what's changing, where to focus — by upgrading.
                </p>
                <a
                  href="mailto:gptc2903@gmail.com?subject=Shortlistly upgrade request"
                  className="cta-button primary-button"
                  style={{ textDecoration: "none", minHeight: 42, padding: "0 22px", fontSize: "0.88rem" }}
                >
                  Email to upgrade →
                </a>
              </section>
            ) : companyInsights ? (
              <CompanyInsightsPanel insights={companyInsights} />
            ) : companyInsightsError ? (
              <section className="results-block bf-error-block">
                <p className="rewrite-error" style={{ margin: 0 }}>{companyInsightsError}</p>
              </section>
            ) : (
              <PanelSkeleton label="Researching company intelligence…" />
            )}

            <section className="results-block rfd-block">
              <div className="results-block-header">
                <div className="results-block-icon keyword-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <circle cx="12" cy="12" r="10" /><path d="m9 12 2 2 4-4" />
                  </svg>
                </div>
                <div>
                  <h2 className="results-block-title">How your score was calculated</h2>
                  <p className="results-block-sub">Each component is weighted and contributes a specific number of points to your total.</p>
                </div>
              </div>
          {(() => {
            const respScore  = breakdown.responsibility_match_score || 0;
            const semScore   = breakdown.semantic_score || 0;
            const expScore   = breakdown.experience_match_score || 0;
            const skillScore = breakdown.skills_match_score || 0;
            const weights    = breakdown.weights || { responsibility: 0.65, semantic: 0.20, experience: 0.10, skills: 0.05 };

            const components = [
              {
                label: "Responsibility match",
                desc: "How well your CV proves the essential and desirable requirements from the JD — the biggest driver of your score.",
                raw: respScore,
                weight: weights.responsibility || 0.65,
                color: scoreColor(respScore),
              },
              {
                label: "Language & context fit",
                desc: "How closely your CV's overall language, terminology, and framing mirrors the job description — catches relevant experience even when exact words differ.",
                raw: semScore,
                weight: weights.semantic || 0.20,
                color: scoreColor(semScore),
              },
              {
                label: "Experience level",
                desc: "Whether your seniority, years of experience, and job title history align with what the role requires.",
                raw: expScore,
                weight: weights.experience || 0.10,
                color: scoreColor(expScore),
              },
              {
                label: "Skills coverage",
                desc: "Technical and soft skill overlap between your CV and the JD — a small contributing signal.",
                raw: skillScore,
                weight: weights.skills || 0.05,
                color: scoreColor(skillScore),
              },
            ];

            return (
              <div className="sbd-list">
                {components.map((c, i) => {
                  const maxPoints = Math.round(c.weight * 100);
                  const earned    = Math.round((c.raw / 100) * maxPoints);
                  const fillPct   = c.raw;
                  return (
                    <div key={i} className="sbd-row">
                      <div className="sbd-row-top">
                        <div className="sbd-row-meta">
                          <span className="sbd-label">{c.label}</span>
                          <span className="sbd-weight">{Math.round(c.weight * 100)}% of score</span>
                        </div>
                        <div className="sbd-points">
                          <span className="sbd-earned" style={{ color: c.color }}>{earned}</span>
                          <span className="sbd-max">/{maxPoints} pts</span>
                        </div>
                      </div>
                      <div className="sbd-bar">
                        <div className="sbd-bar-fill" style={{ width: `${fillPct}%`, background: c.color }} />
                      </div>
                      <p className="sbd-desc">{c.desc}</p>
                    </div>
                  );
                })}
                <div className="sbd-total-row">
                  <span className="sbd-total-label">Total</span>
                  <span className="sbd-total-score" style={{ color: scoreColor(Math.round(matchScore)) }}>
                    {Math.round(matchScore)}<span className="sbd-total-denom">/100</span>
                  </span>
                </div>
              </div>
            );
          })()}
        </section>
          </>
        )}

        {activeTab === "details" && (
          <>
            <CombinedMatchPanel
              matched={breakdown.matched_responsibilities || []}
              missing={breakdown.missing_responsibilities || []}
              atsKeywords={atsKeywords}
              responsibilityDetail={responsibilityDetail}
            />

            <section className="results-block">
              <div className="results-block-header">
                <div className="results-block-icon section-icon">
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <polyline points="14 2 14 8 20 8" />
                  </svg>
                </div>
                <div>
                  <h2 className="results-block-title">Section Feedback</h2>
                  <p className="results-block-sub">Section-level quality feedback across each part of your CV.</p>
                </div>
              </div>
              <div className="section-cards">
                {sortedSectionKeys.map((key) => (
                  <SectionCard key={key} name={key} feedback={sectionFeedback[key]} />
                ))}
              </div>
            </section>

            <CvHighlightPanel highlights={cvHighlights} />
          </>
        )}

        {activeTab === "interview" && (
          <>
            {interviewPrep ? (
              <InterviewPrepPanel data={interviewPrep} />
            ) : interviewPrepError ? (
              <section className="results-block bf-error-block">
                <p className="rewrite-error" style={{ margin: 0 }}>{interviewPrepError}</p>
              </section>
            ) : (
              <PanelSkeleton label="Generating interview questions…" />
            )}
          </>
        )}

        {/* ── Feedback widget ── */}
        <section className="results-block fb-block">
          <div className="fb-inner">
            <div className="fb-question">Was this analysis accurate?</div>
            {feedbackSubmitted ? (
              <div className="fb-thankyou">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#4ade80" strokeWidth="2.5" strokeLinecap="round"><polyline points="20 6 9 17 4 12"/></svg>
                Thanks — your feedback helps us improve the analysis.
              </div>
            ) : (
              <>
                <div className="fb-thumbs">
                  <button
                    type="button"
                    className={`fb-thumb fb-thumb--up${feedbackRating === "accurate" ? " active" : ""}`}
                    onClick={() => setFeedbackRating(feedbackRating === "accurate" ? null : "accurate")}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M14 9V5a3 3 0 0 0-3-3l-4 9v11h11.28a2 2 0 0 0 2-1.7l1.38-9a2 2 0 0 0-2-2.3H14z"/><path d="M7 22H4a2 2 0 0 1-2-2v-7a2 2 0 0 1 2-2h3"/></svg>
                    Accurate
                  </button>
                  <button
                    type="button"
                    className={`fb-thumb fb-thumb--down${feedbackRating === "inaccurate" ? " active" : ""}`}
                    onClick={() => setFeedbackRating(feedbackRating === "inaccurate" ? null : "inaccurate")}
                  >
                    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M10 15v4a3 3 0 0 0 3 3l4-9V2H5.72a2 2 0 0 0-2 1.7l-1.38 9a2 2 0 0 0 2 2.3H10z"/><path d="M17 2h2.67A2.31 2.31 0 0 1 22 4v7a2.31 2.31 0 0 1-2.33 2H17"/></svg>
                    Not accurate
                  </button>
                </div>

                {feedbackRating === "inaccurate" && (
                  <div className="fb-detail">
                    <div className="fb-issues">
                      {["Score too high","Score too low","Wrong responsibilities matched","Skills wrong","CV parsed badly","Other"].map(issue => (
                        <button
                          key={issue}
                          type="button"
                          className={`fb-issue-chip${feedbackIssues.includes(issue) ? " active" : ""}`}
                          onClick={() => setFeedbackIssues(prev =>
                            prev.includes(issue) ? prev.filter(i => i !== issue) : [...prev, issue]
                          )}
                        >
                          {issue}
                        </button>
                      ))}
                    </div>
                    <textarea
                      className="fb-textarea"
                      placeholder="Anything specific that was wrong? (optional)"
                      value={feedbackNote}
                      onChange={e => setFeedbackNote(e.target.value)}
                      rows={3}
                    />
                  </div>
                )}

                {feedbackRating && (
                  <button
                    type="button"
                    className="cta-button primary-button fb-submit"
                    disabled={feedbackSubmitting}
                    onClick={async () => {
                      setFeedbackSubmitting(true);
                      try {
                        await fetch(`${API_BASE_URL}/feedback`, {
                          method: "POST",
                          headers: { "Content-Type": "application/json" },
                          body: JSON.stringify({
                            rating: feedbackRating,
                            issues: feedbackIssues,
                            note: feedbackNote.trim(),
                            match_score: Math.round(matchScore),
                            email: result?._user_email || "",
                          }),
                        });
                      } catch { /* silent — don't block the user */ }
                      setFeedbackSubmitted(true);
                      setFeedbackSubmitting(false);
                    }}
                  >
                    {feedbackSubmitting ? "Sending…" : "Submit feedback"}
                  </button>
                )}
              </>
            )}
          </div>
        </section>

        <div className="results-cta-row">
          <button type="button" className="cta-button primary-button results-cta" onClick={() => navigate("/analyze")}>
            Analyze another CV
          </button>
          <Link to="/" className="cta-button ghost-button results-cta">Back to home</Link>
        </div>
      </div>
    </PageLayout>
  );
}
