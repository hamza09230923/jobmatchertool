import { useEffect, useRef, useState } from "react";
import { useLocation, useNavigate, Link } from "react-router-dom";
import PageLayout from "./PageLayout";
import { signOut } from "./auth";
import "./CoverLetterPage.css";

export default function CoverLetterPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const { letter, fileName } = location.state || {};
  const textareaRef = useRef(null);
  const [edited, setEdited] = useState(letter || "");
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (!letter) {
      navigate("/analyze", { replace: true });
    }
  }, [letter, navigate]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(edited);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Fallback for older browsers
      textareaRef.current?.select();
      document.execCommand("copy");
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    }
  };

  const handleDownload = () => {
    const blob = new Blob([edited], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safeName = (fileName || "cover-letter").replace(/\.[^.]+$/, "");
    a.download = `${safeName}-cover-letter.txt`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  };

  const wordCount = edited.trim() ? edited.trim().split(/\s+/).length : 0;

  if (!letter) return null;

  return (
    <PageLayout
      navRight={(
        <button
          className="cta-button ghost-button"
          style={{ fontSize: "0.88rem", minHeight: "40px", padding: "0 18px", cursor: "pointer" }}
          onClick={() => { signOut(); navigate("/login", { replace: true }); }}
        >
          Sign out
        </button>
      )}
    >
      <div className="cl-page">
        <div className="cl-intro">
          <span className="section-kicker">Cover letter</span>
          <h1 className="cl-title">Your tailored cover letter</h1>
          <p className="cl-subtitle">
            Edit anything below before you send it. Click <strong>Copy</strong> to paste straight into a job
            portal, or <strong>Download</strong> to save it as a text file.
          </p>
        </div>

        <div className="cl-toolbar">
          <Link to="/results" className="cl-back-link">← Back to results</Link>
          <div className="cl-toolbar-actions">
            <button type="button" className="cl-action" onClick={handleCopy}>
              {copied ? (
                <>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                  Copied
                </>
              ) : (
                <>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                  Copy
                </>
              )}
            </button>
            <button type="button" className="cl-action cl-action--primary" onClick={handleDownload}>
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" />
              </svg>
              Download .txt
            </button>
          </div>
        </div>

        <div className="cl-letter-shell">
          <textarea
            ref={textareaRef}
            className="cl-letter-text"
            value={edited}
            onChange={(e) => setEdited(e.target.value)}
            spellCheck
          />
          <div className="cl-letter-footer">
            <span>{wordCount} words</span>
            <span className="cl-letter-footer-hint">Aim for 250–400 words for the cleanest read</span>
          </div>
        </div>
      </div>
    </PageLayout>
  );
}
