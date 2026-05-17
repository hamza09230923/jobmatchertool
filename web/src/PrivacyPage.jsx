import { Link } from "react-router-dom";
import shortlistlyLogo from "./assets/logos/short.png";
import "./PrivacyPage.css";

const LAST_UPDATED = "17 May 2026";
const CONTACT_EMAIL = "gptc2903@gmail.com";

export default function PrivacyPage() {
  return (
    <main className="privacy-page">
      <nav className="privacy-nav">
        <Link to="/" className="privacy-brand" aria-label="Shortlistly home">
          <img src={shortlistlyLogo} alt="Shortlistly" />
        </Link>
        <Link to="/" className="privacy-back-link">← Back to home</Link>
      </nav>

      <article className="privacy-content">
        <header className="privacy-header">
          <span className="privacy-kicker">Legal</span>
          <h1>Privacy Policy</h1>
          <p className="privacy-updated">Last updated: {LAST_UPDATED}</p>
        </header>

        <section>
          <h2>The short version</h2>
          <p>
            Shortlistly analyses your CV against a job description and gives you a score. To do
            that, we send the text of your CV and the job description to Google's Gemini API.
            We don't permanently store your CV file on our servers. We don't sell or share your
            data. You can request deletion of anything we hold about you at any time.
          </p>
        </section>

        <section>
          <h2>What we collect</h2>
          <ul>
            <li>
              <strong>Your CV (PDF).</strong> Uploaded for analysis, processed in memory,
              and discarded once the response is returned. We do not keep your PDF file.
            </li>
            <li>
              <strong>The job description.</strong> Pasted or fetched from a URL you provide.
              Used for the analysis. Not stored after the request completes.
            </li>
            <li>
              <strong>Your email address.</strong> Only if you sign up — used to identify you
              and apply daily scan limits.
            </li>
            <li>
              <strong>Daily scan counts.</strong> A number per user per day, so we can enforce
              fair-use limits. Reset daily.
            </li>
            <li>
              <strong>Optional feedback.</strong> If you click "this was inaccurate" or leave a
              note, we store that to improve the analysis quality.
            </li>
          </ul>
        </section>

        <section>
          <h2>How we process it</h2>
          <p>
            The text of your CV and the job description is sent to <strong>Google's Gemini API</strong>{" "}
            (model: Gemini 2.0 Flash). Google's handling of this data is governed by their{" "}
            <a href="https://ai.google.dev/gemini-api/terms" target="_blank" rel="noopener noreferrer">
              Gemini API Additional Terms
            </a>. We do not train any model on your data.
          </p>
          <p>
            We don't sell, rent, or share your data with advertisers or analytics services.
            We don't use behavioural tracking pixels or cookies for advertising.
          </p>
        </section>

        <section>
          <h2>What's stored locally on your device</h2>
          <p>
            We use your browser's <strong>localStorage</strong> (not cookies) to remember:
          </p>
          <ul>
            <li>A session token, so you don't have to log in on every visit</li>
            <li>Your scan history — the score, file name, and result of recent scans</li>
            <li>Your cached daily scan limit</li>
          </ul>
          <p>
            All of this lives in your browser only. We never read it server-side. You can
            clear it any time via your browser settings, or use the X button next to each scan
            in your history.
          </p>
        </section>

        <section>
          <h2>Third-party services</h2>
          <ul>
            <li><strong>Google Gemini API</strong> — analyses your CV and job description</li>
            <li><strong>Render</strong> — hosts the backend server</li>
            <li><strong>GitHub Pages</strong> — serves the frontend</li>
            <li><strong>Google Fonts</strong> — loads the website typography</li>
          </ul>
          <p>
            We do not use Google Analytics, Meta Pixel, Hotjar, Mixpanel, or any other
            behavioural analytics platform.
          </p>
        </section>

        <section>
          <h2>Your rights</h2>
          <p>Under UK GDPR you have the right to:</p>
          <ul>
            <li>Request a copy of any data we hold about you</li>
            <li>Request correction of inaccurate data</li>
            <li>Request deletion of your data ("right to be forgotten")</li>
            <li>Withdraw consent at any time</li>
          </ul>
          <p>
            To exercise any of these rights, email us at{" "}
            <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>. We'll respond within
            30 days.
          </p>
        </section>

        <section>
          <h2>Data retention</h2>
          <p>
            We hold your email address and daily scan count for as long as you have an active
            account. Optional feedback you submit is retained indefinitely for product
            improvement. CV files and job description text are never persisted.
          </p>
        </section>

        <section>
          <h2>Children</h2>
          <p>
            Shortlistly is not directed at children under 16. If you believe a child has
            given us personal data, contact us and we'll delete it.
          </p>
        </section>

        <section>
          <h2>Changes to this policy</h2>
          <p>
            We'll update this page if our practices change. The "Last updated" date at the
            top reflects the most recent revision. Material changes will be highlighted on
            the home page.
          </p>
        </section>

        <section>
          <h2>Contact</h2>
          <p>
            Questions about your data or this policy?
            Email <a href={`mailto:${CONTACT_EMAIL}`}>{CONTACT_EMAIL}</a>.
          </p>
        </section>
      </article>

      <footer className="privacy-footer">
        <Link to="/">Home</Link>
        <span>·</span>
        <a href={`mailto:${CONTACT_EMAIL}`}>Contact</a>
        <span>·</span>
        <span>© {new Date().getFullYear()} Shortlistly</span>
      </footer>
    </main>
  );
}
