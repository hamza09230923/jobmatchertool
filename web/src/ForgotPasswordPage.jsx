import { useState } from "react";
import { Link } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import { requestPasswordReset } from "./auth";
import "./AuthForms.css";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [sent, setSent] = useState(false);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    const result = await requestPasswordReset(email);
    setLoading(false);
    if (!result.ok) { setError(result.error); return; }
    setSent(true);
  };

  return (
    <main className="auth-shell">
      <nav className="auth-nav">
        <Link to="/"><img src={shortlistlyLogo} alt="Shortlistly" /></Link>
        <Link to="/login" className="auth-nav-link">← Back to login</Link>
      </nav>

      <section className="auth-card">
        <span className="auth-kicker">Forgot password</span>
        <h1 className="auth-title">Reset your password</h1>
        <p className="auth-subtitle">
          Enter the email you signed up with. We'll send you a link to choose a new password.
          The link expires in 1 hour.
        </p>

        {sent ? (
          <div className="auth-success">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 2 }}>
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>
              If an account exists for <strong>{email}</strong>, you'll receive a password
              reset email in the next minute. Check your spam folder if you don't see it.
            </span>
          </div>
        ) : (
          <>
            {error && (
              <div className="auth-error">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 1 }}>
                  <circle cx="12" cy="12" r="10" /><line x1="12" y1="8" x2="12" y2="12" /><line x1="12" y1="16" x2="12.01" y2="16" />
                </svg>
                <span>{error}</span>
              </div>
            )}

            <form className="auth-form" onSubmit={handleSubmit}>
              <div className="auth-field">
                <label>Email</label>
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="you@company.com"
                  autoComplete="email"
                  required
                />
              </div>

              <button type="submit" className="auth-submit" disabled={loading}>
                {loading ? "Sending..." : "Send reset link"}
              </button>
            </form>
          </>
        )}

        <p className="auth-footer">
          Remembered it? <Link to="/login">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
