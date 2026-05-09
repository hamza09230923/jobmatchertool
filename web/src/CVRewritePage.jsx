import { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import "./CVRewritePage.css";

function escHtml(str) {
  return (str || "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;");
}

function parseBullet(raw) {
  if (!raw) return { text: "", metric: null };
  const m = raw.match(/^([\s\S]*?)\s*\[METRIC:\s*([\s\S]*?)\]\s*$/);
  return m ? { text: m[1].trim(), metric: m[2].trim() } : { text: raw.trim(), metric: null };
}

function bulletToHtml(b) {
  const { text, metric } = parseBullet(b);
  if (metric) {
    return `<div class="cvr-bullet">${escHtml(text)}<mark class="cvr-metric-prompt" contenteditable="false" title="${escHtml(metric)}">[+ add: ${escHtml(metric)}]</mark></div>`;
  }
  return `<div class="cvr-bullet">${escHtml(text)}</div>`;
}

// Sets innerHTML once on mount — never touched by React reconciler again.
function EditBlock({ tag: Tag = "div", className, html }) {
  const ref = useRef(null);
  useEffect(() => {
    if (ref.current) ref.current.innerHTML = html || "";
  }, []); // eslint-disable-line react-hooks/exhaustive-deps
  return <Tag ref={ref} className={className} contentEditable suppressContentEditableWarning />;
}

const TYPE_META = {
  repositioned: { color: "#818cf8", label: "Repositioned" },
  optimised:    { color: "#34d399", label: "Optimised"    },
  restructured: { color: "#fbbf24", label: "Restructured" },
  added:        { color: "#a78bfa", label: "Added"        },
  improved:     { color: "#60a5fa", label: "Improved"     },
};

function ChangeCard({ change }) {
  const meta = TYPE_META[change.type] || TYPE_META.improved;
  return (
    <div className="cvr-change-card" style={{ "--cc": meta.color }}>
      <div className="cvr-change-top">
        <span className="cvr-change-label">{change.label || change.section}</span>
        <span className="cvr-change-type">{meta.label}</span>
      </div>
      <p className="cvr-change-text">{change.change}</p>
    </div>
  );
}

function Sidebar({ diagnosis = {}, sectionChanges = [], metricItems = [] }) {
  const hasContent =
    diagnosis.current_positioning ||
    diagnosis.target_positioning ||
    sectionChanges.length > 0 ||
    metricItems.length > 0;
  if (!hasContent) return null;

  return (
    <aside className="cvr-sidebar no-print">
      {/* Before / After diagnosis */}
      {(diagnosis.current_positioning || diagnosis.target_positioning) && (
        <div className="cvr-sb-block">
          <div className="cvr-sb-heading">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4Z" />
            </svg>
            What we changed
          </div>
          {diagnosis.current_positioning && (
            <div className="cvr-diag-row">
              <span className="cvr-diag-pill cvr-diag-pill--before">Before</span>
              <p className="cvr-diag-text">{diagnosis.current_positioning}</p>
            </div>
          )}
          {diagnosis.target_positioning && (
            <div className="cvr-diag-row">
              <span className="cvr-diag-pill cvr-diag-pill--after">After</span>
              <p className="cvr-diag-text">{diagnosis.target_positioning}</p>
            </div>
          )}
        </div>
      )}

      {/* Per-section changes */}
      {sectionChanges.length > 0 && (
        <div className="cvr-sb-block">
          <div className="cvr-sb-heading">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <path d="m9 11 3 3L22 4" /><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
            </svg>
            Section improvements
          </div>
          <div className="cvr-changes-list">
            {sectionChanges.map((c, i) => <ChangeCard key={i} change={c} />)}
          </div>
        </div>
      )}

      {/* Metric prompts summary */}
      {metricItems.length > 0 && (
        <div className="cvr-sb-block cvr-metrics-sb">
          <div className="cvr-sb-heading">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <line x1="12" y1="20" x2="12" y2="10" /><line x1="18" y1="20" x2="18" y2="4" /><line x1="6" y1="20" x2="6" y2="16" />
            </svg>
            Add numbers to strengthen
          </div>
          <p className="cvr-metrics-intro">
            Highlighted text in each bullet = a missing metric. Click it and type your figure.
          </p>
          {metricItems.map((m, i) => (
            <div key={i} className="cvr-metric-row">
              <span className="cvr-metric-role-lbl">{m.role}</span>
              <p className="cvr-metric-q">{m.prompt}</p>
            </div>
          ))}
        </div>
      )}
    </aside>
  );
}

export default function CVRewritePage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { rewrite, fileName } = location.state || {};

  useEffect(() => {
    if (!rewrite) navigate("/analyze", { replace: true });
  }, [rewrite, navigate]);

  if (!rewrite) return null;

  const {
    name,
    contact = {},
    role_target,
    diagnosis = {},
    rewritten_summary,
    skills_section = [],
    education_section = [],
    experience_section = [],
    projects_section = [],
    missing_information = [],
    section_changes = [],
  } = rewrite;

  const contactParts = [contact.email, contact.phone, contact.linkedin, contact.location].filter(Boolean);
  const contactHtml = contactParts.map(escHtml).join(" &nbsp;&middot;&nbsp; ");

  // Collect all [METRIC] prompts from bullets for the sidebar list
  const metricItems = [];
  [...experience_section, ...projects_section].forEach((role) => {
    (role.bullets || []).forEach((b) => {
      const { metric } = parseBullet(b);
      if (metric) metricItems.push({ role: role.heading || "", prompt: metric });
    });
  });

  return (
    <div className="cvr-root">
      {/* ── Toolbar ── */}
      <div className="cvr-topbar no-print">
        <button className="cvr-back-btn" onClick={() => navigate(-1)}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="15 18 9 12 15 6" />
          </svg>
          Back to results
        </button>
        <div className="cvr-topbar-center">
          {role_target && (
            <span className="cvr-tailored-for">Tailored for: <strong>{role_target}</strong></span>
          )}
          <span className="cvr-edit-hint">Click any text to edit &nbsp;·&nbsp; <span className="cvr-hint-metric">amber highlight</span> = add your metric</span>
        </div>
        <button className="cvr-save-btn" onClick={() => window.print()}>
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
            <polyline points="8 17 12 21 16 17" />
            <line x1="12" y1="12" x2="12" y2="21" />
            <path d="M20.88 18.09A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.29" />
          </svg>
          Save as PDF
        </button>
      </div>
      <p className="cvr-print-tip no-print">
        Opens your browser&apos;s print dialog — select <strong>Save as PDF</strong> as the destination.
      </p>

      {/* ── Two-column layout: paper + sidebar ── */}
      <div className="cvr-layout">

        {/* CV Paper */}
        <div className="cvr-paper">
          <div className="cvr-header-block">
            <EditBlock tag="h1" className="cvr-name" html={escHtml(name || fileName || "Your Name")} />
            {contactHtml && <EditBlock className="cvr-contact-line" html={contactHtml} />}
          </div>

          {rewritten_summary && (
            <div className="cvr-section">
              <div className="cvr-section-rule"><span className="cvr-section-title">Professional Summary</span></div>
              <EditBlock className="cvr-section-text" html={escHtml(rewritten_summary)} />
            </div>
          )}

          {skills_section.length > 0 && (
            <div className="cvr-section">
              <div className="cvr-section-rule"><span className="cvr-section-title">Skills</span></div>
              <div className="cvr-skills-grid">
                {skills_section.map((group, i) => {
                  const itemsHtml = group.items ? escHtml(group.items.join(", ")) : "";
                  const rowHtml = group.category
                    ? `<strong>${escHtml(group.category)}:</strong> ${itemsHtml}`
                    : itemsHtml;
                  return <EditBlock key={i} className="cvr-skills-row" html={rowHtml} />;
                })}
              </div>
            </div>
          )}

          {experience_section.length > 0 && (
            <div className="cvr-section">
              <div className="cvr-section-rule"><span className="cvr-section-title">Experience</span></div>
              {experience_section.map((role, i) => (
                <div key={i} className="cvr-role-block">
                  <EditBlock className="cvr-role-heading" html={escHtml(role.heading || "")} />
                  <EditBlock className="cvr-bullets-block" html={(role.bullets || []).map(bulletToHtml).join("")} />
                </div>
              ))}
            </div>
          )}

          {projects_section.length > 0 && (
            <div className="cvr-section">
              <div className="cvr-section-rule"><span className="cvr-section-title">Projects</span></div>
              {projects_section.map((proj, i) => (
                <div key={i} className="cvr-role-block">
                  <EditBlock className="cvr-role-heading" html={escHtml(proj.heading || "")} />
                  <EditBlock className="cvr-bullets-block" html={(proj.bullets || []).map(bulletToHtml).join("")} />
                </div>
              ))}
            </div>
          )}

          {education_section.length > 0 && (
            <div className="cvr-section">
              <div className="cvr-section-rule"><span className="cvr-section-title">Education</span></div>
              {education_section.map((edu, i) => (
                <div key={i} className="cvr-edu-block">
                  <EditBlock className="cvr-role-heading" html={escHtml(edu.heading || "")} />
                  {edu.details && <EditBlock className="cvr-edu-details" html={escHtml(edu.details)} />}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Right-side annotation sidebar */}
        <Sidebar
          diagnosis={diagnosis}
          sectionChanges={section_changes}
          metricItems={metricItems}
        />
      </div>

      {/* Missing info notice — below both columns */}
      {missing_information.length > 0 && (
        <div className="cvr-missing-block no-print">
          <div className="cvr-missing-title">
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            Add these details to strengthen the CV
          </div>
          <ul className="cvr-missing-list">
            {missing_information.map((item, i) => <li key={i}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
