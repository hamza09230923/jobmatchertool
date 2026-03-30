import { useState, useEffect, useRef } from "react";
import { useNavigate, Link } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import "./LoginPage.css";

const PARTICLES = Array.from({ length: 22 }, (_, i) => ({
  id: i,
  x: Math.random() * 100,
  y: Math.random() * 100,
  size: Math.random() * 3 + 1,
  delay: Math.random() * 6,
  dur: Math.random() * 8 + 6,
  opacity: Math.random() * 0.5 + 0.15,
}));

export default function LoginPage() {
  const navigate = useNavigate();
  const cursorRef = useRef(null);
  const cardRef = useRef(null);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [focusedField, setFocusedField] = useState(null);
  const [showPass, setShowPass] = useState(false);
  const [mousePos, setMousePos] = useState({ x: 0, y: 0 });

  useEffect(() => {
    const cursor = cursorRef.current;
    if (!cursor) return;
    const move = (e) => {
      cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
      setMousePos({ x: e.clientX / window.innerWidth, y: e.clientY / window.innerHeight });
    };
    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, []);

  const handleCardMouseMove = (e) => {
    const card = cardRef.current;
    if (!card) return;
    const rect = card.getBoundingClientRect();
    const x = (e.clientX - rect.left) / rect.width - 0.5;
    const y = (e.clientY - rect.top) / rect.height - 0.5;
    card.style.transform = `perspective(900px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg) translateZ(4px)`;
  };

  const handleCardMouseLeave = () => {
    const card = cardRef.current;
    if (!card) return;
    card.style.transform = `perspective(900px) rotateY(0deg) rotateX(0deg) translateZ(0px)`;
  };

  const handleSubmit = (e) => {
    e.preventDefault();
    if (!email || !password) { setError("Please fill in all fields."); return; }
    setError("");
    setLoading(true);
    setTimeout(() => { setLoading(false); navigate("/analyze"); }, 1400);
  };

  return (
    <div className="login-page">
      <div className="cursor" ref={cursorRef} />

      {/* Animated background */}
      <div className="login-bg">
        <div className="login-bg-orb login-bg-orb-a" style={{
          transform: `translate(${mousePos.x * 24}px, ${mousePos.y * 24}px)`
        }} />
        <div className="login-bg-orb login-bg-orb-b" style={{
          transform: `translate(${-mousePos.x * 18}px, ${-mousePos.y * 18}px)`
        }} />
        <div className="login-bg-orb login-bg-orb-c" style={{
          transform: `translate(${mousePos.x * 12}px, ${-mousePos.y * 12}px)`
        }} />
        <div className="login-grid" />
        {PARTICLES.map((p) => (
          <div
            key={p.id}
            className="login-particle"
            style={{
              left: `${p.x}%`,
              top: `${p.y}%`,
              width: `${p.size}px`,
              height: `${p.size}px`,
              opacity: p.opacity,
              animationDelay: `${p.delay}s`,
              animationDuration: `${p.dur}s`,
            }}
          />
        ))}
      </div>

      {/* Nav */}
      <nav className="login-nav login-anim-nav">
        <Link to="/" className="login-logo-link">
          <img src={shortlistlyLogo} alt="SHORTLISTLY." className="login-logo-img" />
        </Link>
        <Link to="/analyze" className="login-try-btn">
          Try free <span className="login-try-arrow">→</span>
        </Link>
      </nav>

      {/* Main content split */}
      <div className="login-split">
        {/* Left panel */}
        <div className="login-left">
          <div className="login-left-inner">
            <div className="login-tagline-chip login-anim-s1">AI-Powered CV Matching</div>
            <h2 className="login-left-title login-anim-s2">
              Turn your CV into<br />
              <span className="login-left-accent">the obvious choice.</span>
            </h2>
            <p className="login-left-body login-anim-s3">
              SHORTLISTLY scores your CV against any job description and tells you exactly what to fix — so you get seen, shortlisted, and hired.
            </p>
            <div className="login-stats login-anim-s4">
              <div className="login-stat">
                <strong>91%</strong>
                <span>Role alignment</span>
              </div>
              <div className="login-stat-div" />
              <div className="login-stat">
                <strong>2.4×</strong>
                <span>Reader clarity</span>
              </div>
              <div className="login-stat-div" />
              <div className="login-stat">
                <strong>5s</strong>
                <span>Analysis time</span>
              </div>
            </div>
            <div className="login-review-row login-anim-s5">
              <div className="login-avatars">
                {["#5ee4ff","#ffd166","#a78bfa","#4ade80"].map((c, i) => (
                  <div key={i} className="login-avatar" style={{ background: `radial-gradient(circle at 35% 35%, ${c}55, #111)`, borderColor: c + "44", marginLeft: i > 0 ? "-10px" : 0 }} />
                ))}
              </div>
              <span className="login-review-text">Loved by 2,400+ job seekers</span>
            </div>
          </div>
        </div>

        {/* Right panel – form card */}
        <div className="login-right login-anim-card">
          <div
            className="login-card"
            ref={cardRef}
            onMouseMove={handleCardMouseMove}
            onMouseLeave={handleCardMouseLeave}
          >
            <div className="login-card-shine" />

            <div className="login-card-header">
              <h1 className="login-title">Welcome back</h1>
              <p className="login-subtitle">Sign in to your account</p>
            </div>

            {error && (
              <div className="login-error">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
                {error}
              </div>
            )}

            <form className="login-form" onSubmit={handleSubmit}>
              <div className={`lf${focusedField === "email" ? " lf--focused" : ""}${email ? " lf--filled" : ""}`}>
                <label className="lf__label">Email address</label>
                <div className="lf__wrap">
                  <svg className="lf__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/>
                    <polyline points="22,6 12,13 2,6"/>
                  </svg>
                  <input
                    type="email"
                    placeholder="you@company.com"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    onFocus={() => setFocusedField("email")}
                    onBlur={() => setFocusedField(null)}
                    autoComplete="email"
                  />
                </div>
              </div>

              <div className={`lf${focusedField === "pass" ? " lf--focused" : ""}${password ? " lf--filled" : ""}`}>
                <div className="lf__label-row">
                  <label className="lf__label">Password</label>
                  <a href="#" className="lf__forgot">Forgot?</a>
                </div>
                <div className="lf__wrap">
                  <svg className="lf__icon" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
                    <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
                  </svg>
                  <input
                    type={showPass ? "text" : "password"}
                    placeholder="••••••••••"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    onFocus={() => setFocusedField("pass")}
                    onBlur={() => setFocusedField(null)}
                    autoComplete="current-password"
                  />
                  <button type="button" className="lf__eye" onClick={() => setShowPass(!showPass)} tabIndex={-1}>
                    {showPass ? (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                    ) : (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                    )}
                  </button>
                </div>
              </div>

              <button type="submit" className={`login-submit${loading ? " login-submit--loading" : ""}`} disabled={loading}>
                {loading ? (
                  <span className="login-submit-inner">
                    <span className="login-ring" />
                    Signing in…
                  </span>
                ) : (
                  <span className="login-submit-inner">
                    Sign in
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><path d="M5 12h14"/><path d="m12 5 7 7-7 7"/></svg>
                  </span>
                )}
                <span className="login-submit-glow" />
              </button>
            </form>

            <p className="login-signup-hint">
              No account yet? <Link to="/analyze" className="login-signup-link">Analyze free →</Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
