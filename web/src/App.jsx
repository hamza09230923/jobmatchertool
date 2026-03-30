import { useEffect, useRef, useState } from "react";
import { Routes, Route } from "react-router-dom";
import "./App.css";
import LoginPage from "./LoginPage";
import AnalyzePage from "./AnalyzePage";
import ResultsPage from "./ResultsPage";
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

    const tick = (now) => {
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;

      const ctaEl = document.querySelector('.primary-button');
      const cta   = midOf(ctaEl);

      if (state === 'traveling') {
        const tA = { x: cta.x + 62, y: cta.y };
        const tB = { x: cta.x - 54, y: cta.y };
        posA.x = lerp(posA.x, tA.x, 0.055);
        posA.y = lerp(posA.y, tA.y, 0.055);
        posB.x = lerp(posB.x, tB.x, 0.044);
        posB.y = lerp(posB.y, tB.y, 0.044);
        if (Math.hypot(posA.x - tA.x, posA.y - tA.y) < 6) {
          state = 'orbiting';
          orbitAng = 0;
        }

      } else if (state === 'orbiting') {
        // Ease-in over 2 laps: 0.5 → 9.0 rad/s, then holds; total ~4s
        const rampProgress = Math.min(orbitAng / (Math.PI * 4), 1);
        const speed = 0.5 + (9.0 - 0.5) * rampProgress * rampProgress;
        orbitAng += dt * speed;

        const rx = 90, ry = 28;
        const tA = { x: cta.x + Math.cos(orbitAng) * rx,           y: cta.y + Math.sin(orbitAng) * ry };
        const tB = { x: cta.x + Math.cos(orbitAng + Math.PI) * rx, y: cta.y + Math.sin(orbitAng + Math.PI) * ry };
        const lerpT = 0.18 + 0.52 * rampProgress;
        posA.x = lerp(posA.x, tA.x, lerpT);
        posA.y = lerp(posA.y, tA.y, lerpT);
        posB.x = lerp(posB.x, tB.x, lerpT);
        posB.y = lerp(posB.y, tB.y, lerpT);
        if (orbitAng >= Math.PI * 8) state = 'returning'; // 4 full laps

      } else if (state === 'returning') {
        // Head back to where ghost orbs are frozen (they haven't moved)
        const gA = midOf(ghostARef.current);
        const gB = midOf(ghostBRef.current);
        posA.x = lerp(posA.x, gA.x, 0.06);
        posA.y = lerp(posA.y, gA.y, 0.06);
        posB.x = lerp(posB.x, gB.x, 0.05);
        posB.y = lerp(posB.y, gB.y, 0.05);

        if (Math.hypot(posA.x - gA.x, posA.y - gA.y) < 5) {
          // Remove real orbs, resume ghosts
          realA.remove();
          realB.remove();
          if (ghostARef.current) ghostARef.current.style.animationPlayState = 'running';
          if (ghostBRef.current) ghostBRef.current.style.animationPlayState = 'running';
          busy.current = false;
          return;
        }
      }

      realA.style.left = `${posA.x}px`;
      realA.style.top  = `${posA.y}px`;
      realB.style.left = `${posB.x}px`;
      realB.style.top  = `${posB.y}px`;
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
      } else {
        setPaused(true);
      }
    } else {
      if (displayed.length > 0) {
        const t = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 48);
        return () => clearTimeout(t);
      } else {
        setDeleting(false);
        setPhraseIdx((i) => (i + 1) % TYPING_PHRASES.length);
      }
    }
  }, [displayed, deleting, paused, phraseIdx]);

  return (
    <h1 className="hero-title">
      <span className="accent-copy">Your CV</span> should feel<br />
      <span className="typing-phrase">{displayed}</span><span className="typing-cursor" />
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
  const cursorRef = useRef(null);
  const [heroRef, heroVisible] = useScrollReveal(0.05);
  const [carouselRef, carouselVisible] = useScrollReveal(0.1);
  const [howRef, howVisible] = useScrollReveal(0.08);
  const [stepsRef, stepsVisible] = useScrollReveal(0.08);
  const [ctaRef, ctaVisible] = useScrollReveal(0.1);

  useEffect(() => {
    const cursor = cursorRef.current;
    if (!cursor) return undefined;

    const move = (event) => {
      cursor.style.transform = `translate(${event.clientX}px, ${event.clientY}px)`;
    };

    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, []);

  return (
    <main className="landing-page">
      <div className="cursor" ref={cursorRef}></div>
      <div className="space-stars"></div>
      <div className="space-stars space-stars-secondary"></div>
      <div className="space-grid"></div>
      <div className="space-orb space-orb-a"></div>
      <div className="space-orb space-orb-b"></div>
      <div className="page-glow page-glow-left"></div>
      <div className="page-glow page-glow-right"></div>

      <div className="landing-shell">
        <nav className="landing-nav">
          <ShortlistlyLogo />
          <a className="cta-button ghost-button" href="#cta">
            Early access
          </a>
        </nav>

        <section className={`hero-section${heroVisible ? " is-visible" : ""}`} ref={heroRef}>
          <TypingHero />
          <p className="hero-subtitle lp-anim-s3">
            Strong candidates get missed when the fit is not obvious fast enough.
          </p>
          <div className="hero-actions lp-anim-s4">
            <a className="cta-button primary-button" href="#cta">
              Join the waitlist
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
              <span className="section-kicker cta-sr-1">Early Access</span>
              <h2 className="cta-sr-2">Make every application read like the obvious choice.</h2>
              <p>
                Join the waitlist for first access when SHORTLISTLY opens.
              </p>
            </div>
            <div className="closing-actions cta-sr-3">
              <a className="cta-button primary-button closing-button" href="mailto:hello@matchcv.io">
                Join the waitlist
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

function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<LoginPage />} />
      <Route path="/analyze" element={<AnalyzePage />} />
      <Route path="/results" element={<ResultsPage />} />
    </Routes>
  );
}

export default App;
