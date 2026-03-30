import { useEffect, useRef } from "react";
import "./App.css";
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
  return (
    <div className="hero-showcase" aria-hidden="true">
      <div className="hero-showcase__glow hero-showcase__glow--a"></div>
      <div className="hero-showcase__glow hero-showcase__glow--b"></div>
      <div className="hero-showcase__frame">
        <div className="hero-showcase__brand-shell">
          <img className="hero-showcase__brand-logo" src={shortlistlyLogo} alt="" />
        </div>
        <div className="hero-showcase__veil"></div>
        <div className="hero-showcase__orb hero-showcase__orb--a"></div>
        <div className="hero-showcase__orb hero-showcase__orb--b"></div>
      </div>
      <div className="hero-showcase__caption">
        <span>Powered by SHORTLISTLY.AI</span>
        <strong>Sharper CVs, clearer fit.</strong>
      </div>
    </div>
  );
}

function App() {
  const cursorRef = useRef(null);

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

        <section className="hero-section">
          <h1 className="hero-title">
            <span className="accent-copy">Your CV</span> should feel written for one role.
          </h1>
          <p className="hero-subtitle">
            Strong candidates get missed when the fit is not obvious fast enough.
          </p>
          <div className="hero-actions">
            <a className="cta-button primary-button" href="#cta">
              Join the waitlist
            </a>
          </div>
          <div className="tag-row">
            <span className="tag-pill">Role-specific</span>
            <span className="tag-pill">Sharper positioning</span>
            <span className="tag-pill">Cleaner signal</span>
            <span className="tag-pill">No generic CVs</span>
          </div>
        </section>

        <section className="logo-carousel-section" aria-label="Company logos">
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

        <section className="content-section how-it-works-section">
          <div className="section-kicker">How it works</div>
          <div className="workflow-topline">
            <div className="workflow-intro">
              <h2>Turn a broad CV into a precise signal.</h2>
              <div className="workflow-accent">Role fit, made obvious</div>
              <p>SHORTLISTLY turns a generic resume into a case for one role.</p>
              <div className="workflow-summary">
                <span>Role fit</span>
                <span>Proof up front</span>
                <span>Faster trust</span>
              </div>
            </div>
            <HeroShowcase />
          </div>
          <div className="process-grid">
            {STEPS.map((item) => (
              <article className="process-card" key={item.index}>
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

        <section className="closing-section" id="cta">
          <div className="closing-shell">
            <div className="closing-copy">
              <span className="section-kicker">Early Access</span>
              <h2>Make every application read like the obvious choice.</h2>
              <p>
                Join the waitlist for first access when SHORTLISTLY opens.
              </p>
            </div>
            <div className="closing-actions">
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

export default App;
