import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import "./App.css";

export function ShortlistlyLogo() {
  return <img className="shortlistly-logo-image" src={shortlistlyLogo} alt="SHORTLISTLY." />;
}

export default function PageLayout({ children, navRight }) {
  const cursorRef = useRef(null);

  useEffect(() => {
    const cursor = cursorRef.current;
    if (!cursor) return undefined;
    const move = (e) => {
      cursor.style.transform = `translate(${e.clientX}px, ${e.clientY}px)`;
    };
    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, []);

  return (
    <main className="landing-page">
      <div className="cursor" ref={cursorRef} />
      <div className="space-stars" />
      <div className="space-stars space-stars-secondary" />
      <div className="space-grid" />
      <div className="space-orb space-orb-a" />
      <div className="space-orb space-orb-b" />
      <div className="page-glow page-glow-left" />
      <div className="page-glow page-glow-right" />

      <div className="layout-shell">
        <nav className="landing-nav">
          <Link to="/" style={{ textDecoration: "none" }}>
            <ShortlistlyLogo />
          </Link>
          {navRight}
        </nav>
        {children}
      </div>
    </main>
  );
}
