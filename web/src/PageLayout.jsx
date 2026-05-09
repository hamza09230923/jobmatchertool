import { Link } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import ParticleField from "./ParticleField";
import "./App.css";

export function ShortlistlyLogo() {
  return <img className="shortlistly-logo-image" src={shortlistlyLogo} alt="SHORTLISTLY." />;
}

export default function PageLayout({ children, navRight, shellClass }) {
  return (
    <main className="landing-page">
      <ParticleField count={62} />
      <div className="space-grid" />
      <div className="space-orb space-orb-a" />
      <div className="space-orb space-orb-b" />
      <div className="page-glow page-glow-left" />
      <div className="page-glow page-glow-right" />

      <div className={`layout-shell${shellClass ? ` ${shellClass}` : ""}`}>
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
