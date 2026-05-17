import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import { verifyEmail } from "./auth";
import "./AuthForms.css";

export default function VerifyEmailPage() {
  const [params] = useSearchParams();
  const token = params.get("token") || "";
  const [state, setState] = useState("pending"); // "pending" | "ok" | "error"
  const [message, setMessage] = useState("");

  useEffect(() => {
    if (!token) {
      setState("error");
      setMessage("This link is missing its verification token. Try clicking the link from your email again.");
      return;
    }
    (async () => {
      const result = await verifyEmail(token);
      if (result.ok) {
        setState("ok");
        setMessage("Your email is verified. You can keep using Shortlistly normally.");
      } else {
        setState("error");
        setMessage(result.error || "Could not verify your email.");
      }
    })();
  }, [token]);

  return (
    <main className="auth-shell">
      <nav className="auth-nav">
        <Link to="/"><img src={shortlistlyLogo} alt="Shortlistly" /></Link>
        <Link to="/analyze" className="auth-nav-link">Go to dashboard →</Link>
      </nav>

      <section className="auth-card">
        <span className="auth-kicker">Email verification</span>
        <h1 className="auth-title">
          {state === "pending" ? "Verifying…" : state === "ok" ? "All set" : "Couldn't verify"}
        </h1>

        {state === "pending" && (
          <p className="auth-subtitle">Checking your link, one moment…</p>
        )}

        {state === "ok" && (
          <div className="auth-success" style={{ marginTop: 12 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 2 }}>
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>{message}</span>
          </div>
        )}

        {state === "error" && (
          <div className="auth-error" style={{ marginTop: 12 }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 1 }}>
              <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
            <span>{message}</span>
          </div>
        )}

        <p className="auth-footer">
          <Link to="/analyze">Continue to Shortlistly →</Link>
        </p>
      </section>
    </main>
  );
}
