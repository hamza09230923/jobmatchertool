import { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import { resetPassword } from "./auth";
import "./AuthForms.css";

export default function ResetPasswordPage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const token = params.get("token") || "";
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);

  useEffect(() => {
    if (!token) setError("This link is missing its reset token. Request a new one from the forgot-password page.");
  }, [token]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== confirm) { setError("Passwords don't match."); return; }
    setLoading(true);
    const result = await resetPassword(token, password);
    setLoading(false);
    if (!result.ok) { setError(result.error); return; }
    setDone(true);
    setTimeout(() => navigate("/login", { replace: true }), 2200);
  };

  return (
    <main className="auth-shell">
      <nav className="auth-nav">
        <Link to="/"><img src={shortlistlyLogo} alt="Shortlistly" /></Link>
        <Link to="/login" className="auth-nav-link">← Back to login</Link>
      </nav>

      <section className="auth-card">
        <span className="auth-kicker">Reset password</span>
        <h1 className="auth-title">Choose a new password</h1>
        <p className="auth-subtitle">
          Pick something at least 8 characters long. You'll be signed in automatically once
          you've reset it.
        </p>

        {done ? (
          <div className="auth-success">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.8" strokeLinecap="round" style={{ flexShrink: 0, marginTop: 2 }}>
              <polyline points="20 6 9 17 4 12" />
            </svg>
            <span>Password updated. Redirecting you to login…</span>
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
                <label>New password</label>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="At least 8 characters"
                  autoComplete="new-password"
                  minLength={8}
                  required
                />
              </div>

              <div className="auth-field">
                <label>Confirm password</label>
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  placeholder="Re-enter your password"
                  autoComplete="new-password"
                  required
                />
              </div>

              <button type="submit" className="auth-submit" disabled={loading || !token}>
                {loading ? "Updating..." : "Reset password"}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}
