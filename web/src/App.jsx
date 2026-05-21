import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Routes, Route, Link, Navigate, useLocation } from "react-router-dom";
import ParticleField from "./ParticleField";
import "./App.css";
import { isAuthenticated } from "./auth";
import { SAMPLE_STATE } from "./sampleScan";

const LoginPage = lazy(() => import("./LoginPage"));
const SignupPage = lazy(() => import("./SignupPage"));
const ForgotPasswordPage = lazy(() => import("./ForgotPasswordPage"));
const ResetPasswordPage = lazy(() => import("./ResetPasswordPage"));
const VerifyEmailPage = lazy(() => import("./VerifyEmailPage"));
const AnalyzePage = lazy(() => import("./AnalyzePage"));
const ResultsPage = lazy(() => import("./ResultsPage"));
const CVRewritePage = lazy(() => import("./CVRewritePage"));
const CoverLetterPage = lazy(() => import("./CoverLetterPage"));
const PrivacyPage = lazy(() => import("./PrivacyPage"));
import doordashLogo from "./assets/logos/doordash-wordmark.svg";
import githubLogo from "./assets/logos/github-wordmark.svg";
import linearLogo from "./assets/logos/linear-wordmark.svg";
import notionLogo from "./assets/logos/notion-wordmark.png";
import shortlistlyLogo from "./assets/logos/short.png";
import stripeLogo from "./assets/logos/stripe-wordmark.svg";
import vercelLogo from "./assets/logos/vercel-wordmark.svg";

const LOGO_MARQUEE = [
  { name: "DoorDash", src: doordashLogo, width: 154 },
  { name: "Notion", src: notionLogo, width: 162, className: "logo-mark-invert" },
  { name: "GitHub", src: githubLogo, width: 150, className: "logo-mark-invert" },
  { name: "Stripe", src: stripeLogo, width: 138 },
  { name: "Vercel", src: vercelLogo, width: 164, className: "logo-mark-invert" },
  { name: "Linear", src: linearLogo, width: 164 },
];

const STEPS = [
  {
    index: "01",
    label: "Recruiter Analysis",
    color: "cyan",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
        <circle cx="12" cy="12" r="3" />
      </svg>
    ),
    title: "See yourself through their eyes",
    sub: "Know exactly what a hiring manager thinks — before you ever hit apply.",
  },
  {
    index: "02",
    label: "ATS Coverage",
    color: "green",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
        <polyline points="22 4 12 14.01 9 11.01" />
      </svg>
    ),
    title: "Never get filtered out again",
    sub: "Every missing keyword, mapped and fixed before the system decides your fate.",
  },
  {
    index: "03",
    label: "CV Intelligence",
    color: "amber",
    icon: (
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
      </svg>
    ),
    title: "Your CV, brutally honest",
    sub: "Quality score, red flags, ATS rating. Everything your CV has been hiding.",
  },
];

function ShortlistlyLogo() {
  return <img className="shortlistly-logo-image" src={shortlistlyLogo} alt="SHORTLISTLY." />;
}

function HeroShowcase() {
  const ghostARef = useRef(null);
  const ghostBRef = useRef(null);
  const busy      = useRef(false);
  const rafId     = useRef(null);

  const lerp = (a, b, t) => a + (b - a) * t;
  const midOf = (el) => {
    if (!el) return { x: 0, y: 0 };
    const r = el.getBoundingClientRect();
    return { x: r.left + r.width / 2, y: r.top + r.height / 2 };
  };

  const styleOrb = (orb, current, previous, intensity = 1) => {
    if (!orb) return;

    const dx = current.x - previous.x;
    const dy = current.y - previous.y;
    const speed = Math.hypot(dx, dy);
    const angle = Math.atan2(dy, dx) * (180 / Math.PI);
    const tailScale = Math.min(1.7, 0.88 + speed * 0.08 + intensity * 0.18);
    const trailOpacity = Math.min(0.9, 0.24 + speed * 0.04 + intensity * 0.16);
    const scale = 0.96 + intensity * 0.08;

    orb.style.left = `${current.x}px`;
    orb.style.top = `${current.y}px`;
    orb.style.setProperty("--orb-angle", `${angle}deg`);
    orb.style.setProperty("--orb-tail-scale", tailScale.toFixed(3));
    orb.style.setProperty("--orb-trail-opacity", trailOpacity.toFixed(3));
    orb.style.setProperty("--orb-scale", scale.toFixed(3));
  };

  useEffect(() => {
    return () => {
      if (rafId.current) cancelAnimationFrame(rafId.current);
    };
  }, []);

  const handleMouseEnter = () => {
    if (busy.current) return;
    busy.current = true;

    // Snapshot ghost orb viewport positions
    const rA = ghostARef.current ? ghostARef.current.getBoundingClientRect() : null;
    const rB = ghostBRef.current ? ghostBRef.current.getBoundingClientRect() : null;
    const startA = { x: (rA ? rA.left + rA.width / 2 : 0), y: (rA ? rA.top + rA.height / 2 : 0) };
    const startB = { x: (rB ? rB.left + rB.width / 2 : 0), y: (rB ? rB.top + rB.height / 2 : 0) };

    // Pause ghost orbs (they freeze visually in place)
    if (ghostARef.current) ghostARef.current.style.animationPlayState = 'paused';
    if (ghostBRef.current) ghostBRef.current.style.animationPlayState = 'paused';

    // Spawn real fixed orbs at exact same position
    const realA = document.createElement('div');
    const realB = document.createElement('div');
    realA.className = 'orb-real orb-real--a';
    realB.className = 'orb-real orb-real--b';
    realA.style.cssText = `left:${startA.x}px;top:${startA.y}px;`;
    realB.style.cssText = `left:${startB.x}px;top:${startB.y}px;`;
    document.body.appendChild(realA);
    document.body.appendChild(realB);

    const posA = { ...startA };
    const posB = { ...startB };
    let state = 'traveling';
    let orbitAng = 0;
    let last = performance.now();

    styleOrb(realA, posA, startA, 1);
    styleOrb(realB, posB, startB, 1);

    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      const ctaEl = document.querySelector('.hero-actions .primary-button');
      const ctaRect = ctaEl?.getBoundingClientRect();
      const cta = midOf(ctaEl);
      const orbitRx = Math.max((ctaRect?.width ?? 0) * 0.5 + 30, 84);
      const orbitRy = Math.max((ctaRect?.height ?? 0) * 0.5 + 16, 30);
      const prevA = { ...posA };
      const prevB = { ...posB };

      if (state === 'traveling') {
        const tA = { x: cta.x + orbitRx * 0.82, y: cta.y - orbitRy * 0.2 };
        const tB = { x: cta.x - orbitRx * 0.74, y: cta.y + orbitRy * 0.18 };
        posA.x = lerp(posA.x, tA.x, 0.1);
        posA.y = lerp(posA.y, tA.y, 0.1);
        posB.x = lerp(posB.x, tB.x, 0.09);
        posB.y = lerp(posB.y, tB.y, 0.09);
        if (
          Math.hypot(posA.x - tA.x, posA.y - tA.y) < 6 &&
          Math.hypot(posB.x - tB.x, posB.y - tB.y) < 6
        ) {
          state = 'orbiting';
          orbitAng = -Math.PI * 0.2;
        }

      } else if (state === 'orbiting') {
        // Ease-in over 2 laps: 0.5 → 9.0 rad/s, then holds; total ~4s
        const rampProgress = Math.min((orbitAng + Math.PI * 0.2) / (Math.PI * 3), 1);
        const speed = 2.8 + (8.6 - 2.8) * rampProgress * rampProgress;
        orbitAng += dt * speed;

        const tA = {
          x: cta.x + Math.cos(orbitAng) * orbitRx,
          y: cta.y + Math.sin(orbitAng) * orbitRy,
        };
        const tB = {
          x: cta.x + Math.cos(orbitAng + Math.PI) * (orbitRx - 10),
          y: cta.y + Math.sin(orbitAng + Math.PI) * (orbitRy + 3),
        };
        const lerpT = 0.3 + 0.24 * rampProgress;
        posA.x = lerp(posA.x, tA.x, lerpT);
        posA.y = lerp(posA.y, tA.y, lerpT);
        posB.x = lerp(posB.x, tB.x, lerpT);
        posB.y = lerp(posB.y, tB.y, lerpT);
        if (orbitAng >= Math.PI * 7.5) state = 'returning';

      } else if (state === 'returning') {
        const gA = midOf(ghostARef.current);
        const gB = midOf(ghostBRef.current);
        posA.x = lerp(posA.x, gA.x, 0.084);
        posA.y = lerp(posA.y, gA.y, 0.084);
        posB.x = lerp(posB.x, gB.x, 0.076);
        posB.y = lerp(posB.y, gB.y, 0.076);

        if (Math.hypot(posA.x - gA.x, posA.y - gA.y) < 5) {
          realA.remove();
          realB.remove();
          if (ghostARef.current) ghostARef.current.style.animationPlayState = 'running';
          if (ghostBRef.current) ghostBRef.current.style.animationPlayState = 'running';
          busy.current = false;
          return;
        }
      }

      const intensity = state === "orbiting" ? 0.9 : state === "traveling" ? 1 : 0.72;
      styleOrb(realA, posA, prevA, intensity);
      styleOrb(realB, posB, prevB, intensity * 0.94);
      rafId.current = requestAnimationFrame(tick);
    };

    rafId.current = requestAnimationFrame(tick);
  };

  return (
    <div className="hero-showcase" aria-hidden="true" onMouseEnter={handleMouseEnter}>
      <div className="hero-showcase__glow hero-showcase__glow--a" />
      <div className="hero-showcase__glow hero-showcase__glow--b" />
      <div className="hero-showcase__frame">
        <div className="hero-showcase__brand-shell">
          <img className="hero-showcase__brand-logo" src={shortlistlyLogo} alt="" />
        </div>
        <div className="hero-showcase__veil" />
        <div className="hero-showcase__orb hero-showcase__orb--a" ref={ghostARef} />
        <div className="hero-showcase__orb hero-showcase__orb--b" ref={ghostBRef} />
      </div>
      <div className="hero-showcase__caption">
        <span>Powered by SHORTLISTLY.AI</span>
        <strong>Sharper CVs, clearer fit.</strong>
      </div>
    </div>
  );
}

const TYPING_PHRASES = [
  "written for one role.",
  "sharp, clear, precise.",
  "your strongest case.",
  "impossible to ignore.",
];

const LONGEST_TYPING_PHRASE = TYPING_PHRASES.reduce(
  (longest, phrase) => (phrase.length > longest.length ? phrase : longest),
  "",
);

function TypingHero() {
  const [phraseIdx, setPhraseIdx] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    const target = TYPING_PHRASES[phraseIdx];
    if (paused) {
      // After the final (strongest) phrase, hold it on screen instead of deleting.
      if (phraseIdx === TYPING_PHRASES.length - 1) return;
      const t = setTimeout(() => { setPaused(false); setDeleting(true); }, 3200);
      return () => clearTimeout(t);
    }
    if (!deleting) {
      if (displayed.length < target.length) {
        const t = setTimeout(() => setDisplayed(target.slice(0, displayed.length + 1)), 95);
        return () => clearTimeout(t);
      }
      const t = setTimeout(() => setPaused(true), 0);
      return () => clearTimeout(t);
    }
    if (displayed.length > 0) {
      const t = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 48);
      return () => clearTimeout(t);
    }
    const t = setTimeout(() => {
        setDeleting(false);
        setPhraseIdx((i) => (i + 1) % TYPING_PHRASES.length);
    }, 0);
    return () => clearTimeout(t);
  }, [displayed, deleting, paused, phraseIdx]);

  return (
    <h1 className="hero-title">
      <span className="accent-copy">Your CV</span> should feel<br />
      <span className="typing-line">
        <span className="typing-sizer" aria-hidden="true">
          <span className="typing-phrase">{LONGEST_TYPING_PHRASE}</span>
          <span className="typing-cursor" />
        </span>
        <span className="typing-live">
          <span className="typing-phrase">{displayed}</span>
          <span className="typing-cursor" />
        </span>
      </span>
    </h1>
  );
}

function useScrollReveal(threshold = 0.15) {
  const ref = useRef(null);
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([entry]) => { if (entry.isIntersecting) { setVisible(true); obs.disconnect(); } },
      { threshold }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [threshold]);
  return [ref, visible];
}

function LandingPage() {
  const [heroRef, heroVisible] = useScrollReveal(0.05);
  const [carouselRef, carouselVisible] = useScrollReveal(0.1);
  const [howRef, howVisible] = useScrollReveal(0.08);
  const [stepsRef, stepsVisible] = useScrollReveal(0.08);
  const [ctaRef, ctaVisible] = useScrollReveal(0.1);

  return (
    <main className="landing-page">
      <ParticleField count={72} />
      <div className="space-grid"></div>
      <div className="space-orb space-orb-a"></div>
      <div className="space-orb space-orb-b"></div>
      <div className="page-glow page-glow-left"></div>
      <div className="page-glow page-glow-right"></div>

      <div className="landing-shell">
        <nav className="landing-nav">
          <ShortlistlyLogo />
          <div className="landing-nav-actions">
            <Link className="cta-button ghost-button nav-login-button" to="/login">
              Login
            </Link>
          </div>
        </nav>

        <section className={`hero-section${heroVisible ? " is-visible" : ""}`} ref={heroRef}>
          <TypingHero />
          <p className="hero-subtitle lp-anim-s3">
            Your CV, scored against the actual job — in about 30 seconds.
          </p>
          <div className="hero-actions lp-anim-s4">
            <Link className="cta-button primary-button" to="/signup">
              Get started — it's free
            </Link>
          </div>
          <p className="hero-sample-hint lp-anim-s4">Free during early access</p>
          <div className="tag-row lp-anim-s5">
            <span className="tag-pill">Reads PDFs in 30 sec</span>
            <span className="tag-pill">Scores against the JD's keywords</span>
            <span className="tag-pill">Maps responsibility gaps</span>
            <span className="tag-pill">Powered by Gemini</span>
          </div>
        </section>

        <section className={`logo-carousel-section${carouselVisible ? " is-visible" : ""}`} aria-label="Target companies" ref={carouselRef}>
          <p className="logo-carousel-label">Built for CVs targeting roles at companies like</p>
          <div className="logo-carousel-mask">
            <div className="logo-carousel-track">
              {[...LOGO_MARQUEE, ...LOGO_MARQUEE].map((item, index) => (
                <div className="logo-slide" key={`${item.name}-${index}`}>
                  <img
                    className={`logo-mark ${item.className ?? ""}`.trim()}
                    src={item.src}
                    alt={item.name}
                    style={{ width: `${item.width}px` }}
                  />
                </div>
              ))}
            </div>
          </div>
        </section>

        <section className={`content-section how-it-works-section${howVisible ? " is-visible" : ""}`} ref={howRef}>
          <div className="section-kicker sr-item sr-delay-0">How it works</div>
          <div className="workflow-topline">
            <div className="workflow-intro">
              <h2 className="sr-item sr-delay-1">Turn a broad CV into a precise signal.</h2>
              <div className="workflow-accent sr-item sr-delay-2">Role fit, made obvious</div>
              <p className="sr-item sr-delay-3">SHORTLISTLY turns a generic resume into a case for one role.</p>
              <div className="workflow-summary sr-item sr-delay-4">
                <span className="ws-chip ws-chip--cyan">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <circle cx="12" cy="12" r="10" />
                    <circle cx="12" cy="12" r="6" />
                    <circle cx="12" cy="12" r="2" />
                  </svg>
                  Role fit
                </span>
                <span className="ws-chip ws-chip--green">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14" />
                    <polyline points="22 4 12 14.01 9 11.01" />
                  </svg>
                  Proof up front
                </span>
                <span className="ws-chip ws-chip--amber">
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                  </svg>
                  Faster trust
                </span>
              </div>
            </div>
            <HeroShowcase />
          </div>
          <div className={`process-grid${stepsVisible ? " is-visible" : ""}`} ref={stepsRef}>
            {STEPS.map((item, i) => (
              <article className={`process-card process-card--${item.color} step-card-anim step-card-anim-${i}`} key={item.index}>
                <span className="process-card-watermark" aria-hidden="true">{item.index}</span>
                <div className="process-card-icon">{item.icon}</div>
                <span className="process-card-label">{item.label}</span>
                <h3 className="process-card-title">{item.title}</h3>
                <p className="process-card-sub">{item.sub}</p>
              </article>
            ))}
          </div>
        </section>

        <section className={`closing-section${ctaVisible ? " is-visible" : ""}`} id="cta" ref={ctaRef}>
          <div className="closing-shell">
            <div className="closing-copy">
              <span className="section-kicker cta-sr-1">Ready when you are</span>
              <h2 className="cta-sr-2">Make every application read like the obvious choice.</h2>
              <p>
                Upload your CV, paste a job description, get a score in about 30 seconds.
                That's it — no questionnaires, no fluff.
              </p>
            </div>
            <div className="closing-actions cta-sr-3">
              <Link className="cta-button primary-button closing-button" to="/signup">
                Run your first scan
              </Link>
            </div>
          </div>
          <div className="closing-rail">
            <span>~30-second analysis</span>
            <span>Scored against the JD's own keywords</span>
            <span>Free during early access</span>
          </div>
        </section>

        <footer className="landing-footer">
          <div className="landing-footer-row">
            <span className="landing-footer-brand">© {new Date().getFullYear()} Shortlistly</span>
            <div className="landing-footer-links">
              <Link to="/privacy">Privacy</Link>
              <span className="landing-footer-dot">·</span>
              <a href="mailto:gptc2903@gmail.com">Contact</a>
              <span className="landing-footer-dot">·</span>
              <Link to="/login">Login</Link>
              <span className="landing-footer-dot">·</span>
              <Link to="/signup">Sign up</Link>
            </div>
          </div>
        </footer>
      </div>
    </main>
  );
}

function RequireAuth({ children }) {
  const location = useLocation();
  // Sample previews are public — anyone can see what the analysis looks like.
  if (location.state?.isSample) return children;
  return isAuthenticated() ? children : <Navigate to="/login" replace />;
}

function LoginGuard({ onLogin }) {
  return isAuthenticated() ? <Navigate to="/analyze" replace /> : <LoginPage onLogin={onLogin} />;
}

function App() {
  const [, forceUpdate] = useState(0);

  useEffect(() => {
    const onStorage = () => forceUpdate(n => n + 1);
    const onSignOut = () => forceUpdate(n => n + 1);
    window.addEventListener("storage", onStorage);
    window.addEventListener("shortlistly:signout", onSignOut);
    return () => {
      window.removeEventListener("storage", onStorage);
      window.removeEventListener("shortlistly:signout", onSignOut);
    };
  }, []);

  return (
    <Suspense fallback={<div style={{ minHeight: "100vh", background: "#0f1115" }} />}>
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/login" element={<LoginGuard onLogin={() => forceUpdate(n => n + 1)} />} />
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/forgot-password" element={<ForgotPasswordPage />} />
        <Route path="/reset-password" element={<ResetPasswordPage />} />
        <Route path="/verify" element={<VerifyEmailPage />} />
        <Route path="/analyze" element={<RequireAuth><AnalyzePage /></RequireAuth>} />
        <Route path="/results" element={<RequireAuth><ResultsPage /></RequireAuth>} />
        <Route path="/cv-rewrite" element={<RequireAuth><CVRewritePage /></RequireAuth>} />
        <Route path="/cover-letter" element={<RequireAuth><CoverLetterPage /></RequireAuth>} />
        <Route path="/privacy" element={<PrivacyPage />} />
      </Routes>
    </Suspense>
  );
}

export default App;
