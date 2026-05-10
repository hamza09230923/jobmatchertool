import { useEffect, useRef, useState } from "react";
import { Routes, Route, Link, Navigate } from "react-router-dom";
import ParticleField from "./ParticleField";
import "./App.css";
import LoginPage from "./LoginPage";
import AnalyzePage from "./AnalyzePage";
import ResultsPage from "./ResultsPage";
import CVRewritePage from "./CVRewritePage";
import { isAuthenticated } from "./auth";
import doordashLogo from "./assets/logos/doordash-wordmark.svg";
import githubLogo from "./assets/logos/github-wordmark.svg";
import linearLogo from "./assets/logos/linear-wordmark.svg";
import notionLogo from "./assets/logos/notion-wordmark.png";
import postmanLogo from "./assets/logos/postman-wordmark.svg";
import shortlistlyLogo from "./assets/logos/short.png";
import stripeLogo from "./assets/logos/stripe-wordmark.svg";
import vercelLogo from "./assets/logos/vercel-wordmark.svg";

const LOGO_MARQUEE = [
  { name: "DoorDash", src: doordashLogo, width: 154 },
  { name: "Postman", src: postmanLogo, width: 176 },
  { name: "Notion", src: notionLogo, width: 162, className: "logo-mark-invert" },
  { name: "GitHub", src: githubLogo, width: 150, className: "logo-mark-invert" },
  { name: "Stripe", src: stripeLogo, width: 138 },
  { name: "Vercel", src: vercelLogo, width: 164, className: "logo-mark-invert" },
  { name: "Linear", src: linearLogo, width: 164 },
];

const STEPS = [
  {
    index: "01",
    label: "Diagnose",
    cue: "See the blur",
    title: "Find the mismatch",
    body: "Cut what muddies the target.",
    points: ["Trim the vague.", "Keep the role clear."],
    result: "A clear target.",
    metricLabel: "Signal noise",
    metricValue: "68%",
  },
  {
    index: "02",
    label: "Refine",
    cue: "Raise the signal",
    title: "Sharpen the signal",
    body: "Let the strongest proof carry.",
    points: ["Lead with evidence.", "Shape it to the role."],
    result: "Sharper relevance.",
    metricLabel: "Role alignment",
    metricValue: "91%",
  },
  {
    index: "03",
    label: "Convert",
    cue: "Make the case land",
    title: "Apply with conviction",
    body: "Make the fit read instantly.",
    points: ["Front-load the match.", "Make it feel intentional."],
    result: "Faster trust.",
    metricLabel: "Reader clarity",
    metricValue: "2.4x",
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
  "impossible to ignore.",
  "sharp, clear, precise.",
  "your strongest case.",
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
            <a className="cta-button ghost-button" href="#cta">
              Early access
            </a>
          </div>
        </nav>

        <section className={`hero-section${heroVisible ? " is-visible" : ""}`} ref={heroRef}>
          <TypingHero />
          <p className="hero-subtitle lp-anim-s3">
            Strong candidates get missed when the fit is not obvious fast enough.
          </p>
          <div className="hero-actions lp-anim-s4">
            <a className="cta-button primary-button" href="#cta">
              Join us
            </a>
          </div>
          <div className="tag-row lp-anim-s5">
            <span className="tag-pill">Role-specific</span>
            <span className="tag-pill">Sharper positioning</span>
            <span className="tag-pill">Cleaner signal</span>
            <span className="tag-pill">No generic CVs</span>
          </div>
        </section>

        <section className={`logo-carousel-section${carouselVisible ? " is-visible" : ""}`} aria-label="Company logos" ref={carouselRef}>
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
                <span>Role fit</span>
                <span>Proof up front</span>
                <span>Faster trust</span>
              </div>
            </div>
            <HeroShowcase />
          </div>
          <div className={`process-grid${stepsVisible ? " is-visible" : ""}`} ref={stepsRef}>
            {STEPS.map((item, i) => (
              <article className={`process-card step-card-anim step-card-anim-${i}`} key={item.index}>
                <div className="process-card-live" aria-hidden="true">
                  <span className="process-live-dot"></span>
                  <span className="process-live-line"></span>
                </div>
                <div className="process-card-top">
                  <span className="process-card-label">{item.label}</span>
                  <span className="process-card-index">{item.index}</span>
                </div>
                <div className="process-card-headline">
                  <div className="process-card-cue">{item.cue}</div>
                  <h3>{item.title}</h3>
                  <p>{item.body}</p>
                </div>
                <ul className="process-points">
                  {item.points.map((point) => (
                    <li key={point}>{point}</li>
                  ))}
                </ul>
                <div className="process-card-footer">
                  <div className="process-card-outcome">
                    <span>Outcome</span>
                    <strong>{item.result}</strong>
                  </div>
                  <div className="process-card-metric">
                    <span>{item.metricLabel}</span>
                    <strong>{item.metricValue}</strong>
                  </div>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className={`closing-section${ctaVisible ? " is-visible" : ""}`} id="cta" ref={ctaRef}>
          <div className="closing-shell">
            <div className="closing-copy">
              <span className="section-kicker cta-sr-1">Get Started</span>
              <h2 className="cta-sr-2">Make every application read like the obvious choice.</h2>
              <p>
                Join us and start matching your CV to roles with precision.
              </p>
            </div>
            <div className="closing-actions cta-sr-3">
              <a className="cta-button primary-button closing-button" href="/login">
                Join us
              </a>
            </div>
          </div>
          <div className="closing-rail">
            <span>Role-specific positioning</span>
            <span>Sharper signal</span>
            <span>Built for better conversion</span>
          </div>
        </section>
      </div>
    </main>
  );
}

function RequireAuth({ children }) {
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
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginGuard onLogin={() => forceUpdate(n => n + 1)} />} />
      <Route path="/analyze" element={<RequireAuth><AnalyzePage /></RequireAuth>} />
      <Route path="/results" element={<RequireAuth><ResultsPage /></RequireAuth>} />
      <Route path="/cv-rewrite" element={<RequireAuth><CVRewritePage /></RequireAuth>} />
    </Routes>
  );
}

export default App;
