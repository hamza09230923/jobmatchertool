import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import { isAuthenticated, signUp } from "./auth";
import "./AuthForms.css";

export default function SignupPage() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (isAuthenticated()) navigate("/analyze", { replace: true });
  }, [navigate]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError("");
    if (password.length < 8) { setError("Password must be at least 8 characters."); return; }
    if (password !== confirm) { setError("Passwords don't match."); return; }
    setLoading(true);
    const result = await signUp(email, password);
    setLoading(false);
    if (!result.ok) { setError(result.error); return; }
    navigate("/analyze", { replace: true });
  };

  return (
    <main className="auth-shell">
      <nav className="auth-nav">
        <Link to="/"><img src={shortlistlyLogo} alt="Shortlistly" /></Link>
        <Link to="/login" className="auth-nav-link">Have an account? Login →</Link>
      </nav>

      <section className="auth-card">
        <span className="auth-kicker">Create account</span>
        <h1 className="auth-title">Get 2 free CV scans</h1>
        <p className="auth-subtitle">
          Sign up to use Shortlistly. You'll get 2 free scans against any job description —
          no card required.
        </p>

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

          <div className="auth-field">
            <label>Password</label>
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

          <button type="submit" className="auth-submit" disabled={loading}>
            {loading ? "Creating your account..." : "Create account"}
          </button>
        </form>

        <p className="auth-footer">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </section>
    </main>
  );
}
