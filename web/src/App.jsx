import { useEffect, useMemo, useRef, useState } from "react";
import "./App.css";
import skillsTech from "./data/skills.json";
import skillsFinance from "./data/skills_finance.json";
import skillsLaw from "./data/skills_law.json";

const API_URL = "http://127.0.0.1:8000/analyze";
const EXTRACT_URL = "http://127.0.0.1:8000/extract-resume";

const STOPWORDS = new Set([
  "and",
  "or",
  "the",
  "a",
  "an",
  "with",
  "for",
  "to",
  "of",
  "in",
  "on",
  "is",
  "are",
  "as",
  "we",
  "our",
  "their",
  "your",
  "you",
  "will",
  "role",
  "team",
  "job",
  "position",
  "responsibilities",
  "responsibility",
  "hiring",
  "candidate",
  "candidates",
  "ideal",
  "looking",
  "seeking",
  "required",
  "requirements",
  "must",
  "have",
  "nice",
  "plus",
  "bonus",
]);

const SECTION_HEADERS = [
  "summary",
  "profile",
  "objective",
  "skills",
  "experience",
  "work experience",
  "projects",
  "education",
  "certifications",
];

const SYNONYMS = {
  "powerbi": "power bi",
  "power bi": "power bi",
  "pbi": "power bi",
  "etl": "etl",
  "kpi": "kpi",
  "sql": "sql",
  "postgres": "postgresql",
  "postgresql": "postgresql",
  "ml": "machine learning",
  "ai": "artificial intelligence",
};

const ATS_SECTION_DEFS_BY_PROFILE = {
  tech: [
    {
      key: "title",
      label: "Title match",
      terms: ["software engineer", "software developer", "full stack", "backend", "frontend"],
      mode: "title",
    },
    {
      key: "core",
      label: "Core SWE skills",
      terms: [
        "system design",
        "software architecture",
        "distributed systems",
        "scalability",
        "performance",
        "code review",
        "testing",
        "api",
        "microservices",
        "architecture",
        "web applications",
      ],
    },
    {
      key: "leadership",
      label: "Leadership & ownership",
      terms: ["lead", "led", "ownership", "mentor", "mentorship", "onboard", "initiative"],
    },
    {
      key: "scale",
      label: "Scale & performance",
      terms: ["scalable", "latency", "throughput", "reliability", "availability", "performance"],
    },
    {
      key: "tools",
      label: "Tools & platforms",
      terms: [
        "react",
        "kotlin",
        "swift",
        "docker",
        "kubernetes",
        "aws",
        "gcp",
        "azure",
        "ci/cd",
        "git",
        "linux",
      ],
    },
  ],
  finance: [
    {
      key: "title",
      label: "Title match",
      terms: ["analyst", "risk", "performance", "investment", "portfolio"],
      mode: "title",
    },
    {
      key: "core",
      label: "Core finance skills",
      terms: [
        "portfolio",
        "risk",
        "performance",
        "valuation",
        "financial modeling",
        "reconciliation",
        "reporting",
        "kpi",
        "compliance",
        "regulatory",
      ],
    },
    {
      key: "analysis",
      label: "Analysis & data",
      terms: ["sql", "excel", "python", "statistics", "data analysis", "power bi"],
    },
    {
      key: "tools",
      label: "Tools & platforms",
      terms: ["power bi", "excel", "vba", "tableau", "sql", "python"],
    },
  ],
  law: [
    {
      key: "title",
      label: "Title match",
      terms: ["lawyer", "solicitor", "paralegal", "legal", "counsel"],
      mode: "title",
    },
    {
      key: "core",
      label: "Core legal skills",
      terms: [
        "contract drafting",
        "legal research",
        "case law",
        "litigation",
        "compliance",
        "due diligence",
        "negotiation",
        "client advisory",
      ],
    },
    {
      key: "practice",
      label: "Practice areas",
      terms: [
        "employment law",
        "commercial law",
        "corporate law",
        "intellectual property",
        "data privacy",
        "gdpr",
      ],
    },
  ],
};
const PROFILE_OPTIONS = [
  { id: "tech", label: "Tech / SWE" },
  { id: "finance", label: "Finance" },
  { id: "law", label: "Law" },
];

function App() {
  const [selectedFile, setSelectedFile] = useState(null);
  const [jobDesc, setJobDesc] = useState("");
  const [status, setStatus] = useState("Ready.");
  const [score, setScore] = useState(null);
  const [missing, setMissing] = useState([]);
  const [resumeText, setResumeText] = useState("");
  const [resumeView, setResumeView] = useState("");
  const [editedText, setEditedText] = useState("");
  const [keywords, setKeywords] = useState([]);
  const [mustHave, setMustHave] = useState([]);
  const [niceToHave, setNiceToHave] = useState([]);
  const [matched, setMatched] = useState([]);
  const [coverage, setCoverage] = useState({ must: 0, nice: 0 });
const [debugMode, setDebugMode] = useState(false);
const [debugData, setDebugData] = useState(null);
const [openSections, setOpenSections] = useState({});
const [showLogin, setShowLogin] = useState(false);
const [profile, setProfile] = useState(null);
const [customProfiles, setCustomProfiles] = useState([]);
const [newProfileName, setNewProfileName] = useState("");
const [newProfileSkills, setNewProfileSkills] = useState("");
const [jdUrl, setJdUrl] = useState("");
const [history, setHistory] = useState([]);
  const cursorRef = useRef(null);
  const featureRef = useRef(null);

  const scrollTo = (id) => {
    const el = document.getElementById(id);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  };

  const fileName = useMemo(() => selectedFile?.name ?? "", [selectedFile]);
  const skillsSet = useMemo(() => {
    const custom = customProfiles.find((p) => p.id === profile);
    if (custom) {
      return new Set(custom.skills.map((s) => s.toLowerCase()));
    }
    const source =
      profile === "finance"
        ? skillsFinance
        : profile === "law"
        ? skillsLaw
        : skillsTech;
    return new Set(source.map((item) => item.toLowerCase()));
  }, [profile]);

  useEffect(() => {
    const cursor = cursorRef.current;
    if (!cursor) return undefined;
    const move = (event) => {
      cursor.style.transform = `translate(${event.clientX}px, ${event.clientY}px)`;
    };
    window.addEventListener("mousemove", move);
    return () => window.removeEventListener("mousemove", move);
  }, []);

  useEffect(() => {
    const container = featureRef.current;
    if (!container) return;
    const cards = Array.from(container.querySelectorAll(".feature-card"));
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) {
            entry.target.classList.add("show");
          }
        });
      },
      { threshold: 0.2 }
    );
    cards.forEach((c) => observer.observe(c));
    return () => observer.disconnect();
  }, []);

  const onFileSelected = (file) => {
    if (!file) return;
    setSelectedFile(file);
    setStatus("Resume loaded.");
  };

  const addCustomProfile = () => {
    const name = newProfileName.trim();
    if (!name) return;
    const skills = newProfileSkills
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean);
    if (!skills.length) return;
    const id = `custom-${Date.now()}`;
    const profileObj = { id, label: name, skills };
    setCustomProfiles((prev) => [...prev, profileObj]);
    setProfile(id);
    setNewProfileName("");
    setNewProfileSkills("");
  };

  const loadJobFromFile = async (file) => {
    if (!file) return;
    if (file.type === "text/plain") {
      const text = await file.text();
      setJobDesc(text);
      setStatus("Job description loaded from file.");
      return;
    }
    setStatus("Only .txt job description files are supported for upload.");
  };

  const handleDrop = (event) => {
    event.preventDefault();
    const file = event.dataTransfer.files?.[0];
    if (file) onFileSelected(file);
    event.currentTarget.classList.remove("dragover");
  };

  const handleDragOver = (event) => {
    event.preventDefault();
    event.currentTarget.classList.add("dragover");
  };

  const handleDragLeave = (event) => {
    event.currentTarget.classList.remove("dragover");
  };

  const handleAnalyze = async () => {
    if (!selectedFile) {
      setStatus("Please upload a resume PDF first.");
      return;
    }
    if (!jobDesc.trim()) {
      setStatus("Please paste a job description.");
      return;
    }

    try {
      setStatus("Analyzing...");
      const ensureResumeText = async () => {
        if (editedText || resumeText) return editedText || resumeText;
        const formData = new FormData();
        formData.append("resume", selectedFile);
        const response = await fetch(EXTRACT_URL, {
          method: "POST",
          body: formData,
        });
        if (!response.ok) return "";
        const data = await response.json();
        const text = data.resume_text || "";
        if (text) {
          setResumeText(text);
          setEditedText((prev) => (prev ? prev : text));
        }
        return text;
      };

      const formData = new FormData();
      formData.append("resume", selectedFile);
      formData.append("job_description", jobDesc);

      const response = await fetch(`${API_URL}${debugMode ? "?debug=1" : ""}`, {
        method: "POST",
        body: formData,
      });

      if (!response.ok) {
        const errText = await response.text();
        setStatus(`Error ${response.status}: ${errText}`);
        return;
      }

      const data = await response.json();
      setScore(data.match_score);
      setMissing(data.missing_keywords || []);
      if (data.resume_text) {
        setResumeText(data.resume_text);
        setResumeView(data.resume_text);
        setEditedText((prev) => (prev ? prev : data.resume_text));
      }
      setDebugData(data.debug || null);
      const parsed = parseJobDescription(jobDesc);
      setMustHave(parsed.mustHave);
      setNiceToHave(parsed.niceToHave);
      const derived = buildKeywords(jobDesc);
      setKeywords(derived);
      const resumeForMatch = (editedText || resumeText) || (await ensureResumeText());
      const matchedTerms = getMatchedKeywords(resumeForMatch, derived);
      setMatched(matchedTerms);
      setCoverage({
        must: getCoverage(parsed.mustHave, resumeForMatch),
        nice: getCoverage(parsed.niceToHave, resumeForMatch),
      });
      if (resumeText) {
        setEditedText((prev) => (prev ? prev : resumeText));
      }
        if (debugMode && data.debug) {
          setStatus(`Debug: ${JSON.stringify(data.debug)}`);
        } else if (!data.missing_keywords?.length) {
          setStatus("Done. No missing keywords detected.");
        } else {
          setStatus("Done.");
        }
    } catch (error) {
      setStatus("Request failed. Is the API running?");
    }
  };

  const handleExtract = async () => {
    if (!selectedFile) {
      setStatus("Please upload a resume PDF first.");
      return;
    }

    try {
      setStatus("Extracting resume text...");
      const formData = new FormData();
      formData.append("resume", selectedFile);
      const response = await fetch(EXTRACT_URL, {
        method: "POST",
        body: formData,
      });
      if (!response.ok) {
        const errText = await response.text();
        setStatus(`Error ${response.status}: ${errText}`);
        return;
      }
      const data = await response.json();
      const text = data.resume_text || "";
      setResumeText(text);
      setResumeView(text);
      setEditedText(text);
      const parsed = parseJobDescription(jobDesc);
      setMustHave(parsed.mustHave);
      setNiceToHave(parsed.niceToHave);
      const derived = buildKeywords(jobDesc);
      setKeywords(derived);
      setMatched(getMatchedKeywords(text, derived));
      setCoverage({
        must: getCoverage(parsed.mustHave, text),
        nice: getCoverage(parsed.niceToHave, text),
      });
      setDebugData(null);
      setStatus("Resume text loaded.");
    } catch (error) {
      setStatus("Failed to extract resume text.");
    }
  };

  const handleReset = () => {
    setSelectedFile(null);
    setJobDesc("");
    setScore(null);
    setMissing([]);
    setResumeText("");
    setEditedText("");
    setKeywords([]);
    setMustHave([]);
    setNiceToHave([]);
    setMatched([]);
    setCoverage({ must: 0, nice: 0 });
    setDebugData(null);
    setOpenSections({});
    setStatus("Ready.");
  };

  const normalizePhrase = (text) =>
    text
      .toLowerCase()
      .replace(/[^a-z0-9+# ]+/g, " ")
      .replace(/\s+/g, " ")
      .trim();

  const applySynonyms = (term) => SYNONYMS[term] || term;

  const tokenize = (text) =>
    normalizePhrase(text)
      .split(" ")
      .filter(Boolean)
      .filter((token) => token.length > 1 && !STOPWORDS.has(token))
      .map(applySynonyms);

  const buildKeywords = (text) => {
    const tokens = tokenize(text);
    const phrases = new Set();
    tokens.forEach((token) => phrases.add(token));
    for (let i = 0; i < tokens.length; i += 1) {
      const two = `${tokens[i]} ${tokens[i + 1] || ""}`.trim();
      const three = `${tokens[i]} ${tokens[i + 1] || ""} ${tokens[i + 2] || ""}`.trim();
      if (two.split(" ").length === 2) phrases.add(two);
      if (three.split(" ").length === 3) phrases.add(three);
    }
    const filtered = Array.from(phrases).filter((term) => skillsSet.has(term));
    return filtered.slice(0, 160);
  };

  const parseJobDescription = (text) => {
    const lines = text.split(/\r?\n/);
    let mode = "general";
    const must = [];
    const nice = [];
    lines.forEach((line) => {
      const lower = line.toLowerCase();
      if (/(must have|required|requirements|minimum)/.test(lower)) {
        mode = "must";
      } else if (/(preferred|nice to have|bonus|plus)/.test(lower)) {
        mode = "nice";
      } else if (line.trim() === "") {
        mode = mode === "must" || mode === "nice" ? mode : "general";
      }
      const terms = buildKeywords(line);
      if (mode === "must") {
        must.push(...terms);
      } else if (mode === "nice") {
        nice.push(...terms);
      }
    });
    return {
      mustHave: Array.from(new Set(must)).slice(0, 60),
      niceToHave: Array.from(new Set(nice)).slice(0, 60),
    };
  };

  const getMatchedKeywords = (text, terms) => {
    const normalized = normalizePhrase(text);
    return terms.filter((term) => normalized.includes(term));
  };

  const getCoverage = (terms, text) => {
    if (!terms.length) return 0;
    const normalized = normalizePhrase(text);
    const hits = terms.filter((term) => normalized.includes(term)).length;
    return Math.round((hits / terms.length) * 100);
  };

  const escapeHtml = (value) =>
    value
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#39;");

  const highlightText = (text, terms) => {
    if (!text) return "";
    const safe = escapeHtml(text);
    if (!terms.length) return safe;
    const escaped = terms
      .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .sort((a, b) => b.length - a.length);
    if (!escaped.length) return safe;
    const regex = new RegExp(`\\b(${escaped.join("|")})\\b`, "gi");
    return safe.replace(regex, "<mark class=\"match\">$1</mark>");
  };

  const highlightWithStatus = (text, matchedTerms, missingTerms) => {
    if (!text) return "";
    const safe = escapeHtml(text);
    const combined = Array.from(
      new Set([...(matchedTerms || []), ...(missingTerms || [])])
    );
    if (!combined.length) return safe;
    const escaped = combined
      .map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
      .sort((a, b) => b.length - a.length);
    if (!escaped.length) return safe;
    const missingSet = new Set(missingTerms.map((term) => normalizePhrase(term)));
    const regex = new RegExp(`\\b(${escaped.join("|")})\\b`, "gi");
    return safe.replace(regex, (match) => {
      const normalized = normalizePhrase(match);
      const isMissing = missingSet.has(normalized);
      const cls = isMissing ? "miss" : "match";
      return `<mark class=\"${cls}\">${match}</mark>`;
    });
  };

  const buildSuggestions = () => {
    const missingMust = mustHave.filter(
      (term) => !getMatchedKeywords(editedText || resumeText, [term]).length
    );
    const missingNice = niceToHave.filter(
      (term) => !getMatchedKeywords(editedText || resumeText, [term]).length
    );
    return {
      missingMust: missingMust.slice(0, 8),
      missingNice: missingNice.slice(0, 8),
    };
  };

  useEffect(() => {
    if (!profile || !jobDesc.trim()) {
      setKeywords([]);
      setMustHave([]);
      setNiceToHave([]);
      setMatched([]);
      setCoverage({ must: 0, nice: 0 });
      return;
    }
    const parsed = parseJobDescription(jobDesc);
    const derived = buildKeywords(jobDesc);
    setMustHave(parsed.mustHave);
    setNiceToHave(parsed.niceToHave);
    setKeywords(derived);
    const resumeForMatch = editedText || resumeText;
    setMatched(getMatchedKeywords(resumeForMatch, derived));
    setCoverage({
      must: getCoverage(parsed.mustHave, resumeForMatch),
      nice: getCoverage(parsed.niceToHave, resumeForMatch),
    });
  }, [profile, jobDesc, editedText, resumeText]);

  const missingTerms = useMemo(() => {
    const matchedSet = new Set(matched.map((term) => normalizePhrase(term)));
    return keywords.filter(
      (term) => !matchedSet.has(normalizePhrase(term))
    );
  }, [keywords, matched]);

  const buildActionSuggestions = () => {
    const resumeForMatch = editedText || resumeText;
    const missingMust = mustHave.filter(
      (term) => !getMatchedKeywords(resumeForMatch, [term]).length
    );
    const missingNice = niceToHave.filter(
      (term) => !getMatchedKeywords(resumeForMatch, [term]).length
    );
    const combined = Array.from(
      new Set([...(missing || []), ...missingMust, ...missingNice])
    ).slice(0, 10);

    const suggestions = [];
    combined.forEach((term) => {
      const norm = normalizePhrase(term);
      const target =
        norm.split(" ").length > 1 || !skillsSet.has(norm) ? "Experience" : "Skills";
      const text =
        target === "Experience"
          ? `Add a bullet in Experience: delivered ${term} in a project to drive impact.`
          : `Add to Skills: ${term}`;
      suggestions.push({ term, target, text });
    });
    return suggestions;
  };

  const atsSections = useMemo(() => {
    const sectionDefs =
      ATS_SECTION_DEFS_BY_PROFILE[profile] || ATS_SECTION_DEFS_BY_PROFILE.tech;
    const resumeForMatch = editedText || resumeText;
    const resumeNorm = normalizePhrase(resumeForMatch);
    const jdNorm = normalizePhrase(jobDesc);
    const hasData = resumeNorm.length > 0 && jdNorm.length > 0;

    return sectionDefs.map((section) => {
      if (!hasData) {
        return {
          ...section,
          score: 0,
          matchedTerms: [],
          missingTerms: [],
          relevantTerms: [],
        };
      }

      const relevant = section.terms.filter((term) => jdNorm.includes(term));
      let active = relevant.length ? relevant : section.terms;
      let matchedTerms = active.filter((term) => resumeNorm.includes(term));
      let score = active.length
        ? Math.round((matchedTerms.length / active.length) * 100)
        : 0;

      if (section.mode === "title") {
        const titleHit = section.terms.find((term) => resumeNorm.includes(term));
        matchedTerms = titleHit ? [titleHit] : [];
        score = titleHit ? 100 : 0;
      }

      const missingTerms = active.filter((term) => !matchedTerms.includes(term));
      return {
        ...section,
        score,
        matchedTerms,
        missingTerms,
        relevantTerms: active,
      };
    });
  }, [jobDesc, editedText, resumeText, profile]);

  if (!profile) {
    return (
      <main className="shell landing">
        <div className="cursor" ref={cursorRef}></div>
        <div className="stars"></div>
        <div className="orb orb-a"></div>
        <div className="orb orb-b"></div>
        <div className="gridlines"></div>

        <nav className="topbar">
          <div className="brand">
            <div className="brand-badge">CV</div>
            <div>
              <div className="brand-title">MatchCV.io</div>
              <div className="brand-subtitle">AI resume+JD alignment</div>
            </div>
          </div>
          <div className="nav-links">
            <a href="#about" onClick={(e) => { e.preventDefault(); scrollTo("about"); }}>About</a>
            <a href="#features" onClick={(e) => { e.preventDefault(); scrollTo("features"); }}>Features</a>
            <a href="#steps" onClick={(e) => { e.preventDefault(); scrollTo("steps"); }}>How it works</a>
            <a href="#profiles" onClick={(e) => { e.preventDefault(); scrollTo("profiles"); }}>Profiles</a>
          </div>
          <div className="nav-actions">
            <button className="nav-cta" onClick={() => setProfile("tech")}>
              Launch App
            </button>
          </div>
        </nav>

        <header className="hero">
          <div className="hero-text">
            <h1 className="logo-stack">
              <span className="logo-glow">Precision</span>
              <span className="logo-glow">Match</span>
              <span className="logo-tag">for every role</span>
            </h1>
            <p className="hero-copy">
              Parse PDFs, extract must-haves, highlight gaps, and tune your resume
              to any job description with ATS-style scoring.
            </p>
            <div className="hero-cta">
              <button className="primary" onClick={() => setProfile("tech")}>
                Start Matching
              </button>
              <button className="ghost" onClick={(e) => { e.preventDefault(); scrollTo("features"); }}>
                See How It Works
              </button>
            </div>
          </div>
          <div className="hero-card">
            <div className="status">Live Score Preview</div>
            <div className="score">82<small>/100</small></div>
            <div className="score-bars">
              <div className="bar-row">
                <span>Must-have</span>
                <div className="score-bar"><div className="score-fill good" style={{ width: "88%" }}></div></div>
                <span className="mini">88%</span>
              </div>
              <div className="bar-row">
                <span>Nice-to-have</span>
                <div className="score-bar"><div className="score-fill" style={{ width: "72%" }}></div></div>
                <span className="mini">72%</span>
              </div>
              <div className="bar-row">
                <span>Penalty</span>
                <div className="score-bar"><div className="score-fill warn" style={{ width: "8%" }}></div></div>
                <span className="mini">-8</span>
              </div>
            </div>
            <div className="hero-sample">
              <div className="sample-columns">
                <div>
                  <div className="status">Sample CV</div>
                  <pre className="sample-block">
Senior Software Engineer — 5 yrs
• Built scalable services (Go, Postgres, Redis) handling 5M req/day
• Led code reviews, observability, and perf tuning (-30% p95)
• React/TypeScript web apps, A/B tests, accessibility
                  </pre>
                </div>
                <div>
                  <div className="status">Sample JD</div>
                  <pre className="sample-block">
Design scalable systems, improve performance, lead code reviews,
build React/TypeScript web apps. DB + caching experience preferred.
                  </pre>
                </div>
              </div>
              <div className="chip-list">
                <span className="chip chip-soft">Matched: scalable systems</span>
                <span className="chip chip-soft">Matched: code reviews</span>
                <span className="chip chip-soft">Matched: React/TypeScript</span>
                <span className="chip chip-warn">Missing: accessibility</span>
                <span className="chip chip-warn">Missing: A/B testing</span>
              </div>
            </div>
          </div>
        </header>

        <section className="panel landing-section" id="about">
          <div className="section-header">
            <h2>Built for real ATS checks</h2>
            <p>Section-weighted matching, must-have detection, and transparent scoring — without sending your data away.</p>
          </div>
          <div className="grid two-col">
            <div className="card">
              <div className="icon">🧠</div>
              <h3>Parsing that respects layout</h3>
              <p>PDF text extraction with section detection (Experience, Projects, Skills) so coverage reflects where it matters.</p>
            </div>
            <div className="card">
              <div className="icon">⚡</div>
              <h3>Dual keyword engines</h3>
              <p>TextRazor + TF‑IDF fallback to catch critical phrases; synonyms map to your skill taxonomy.</p>
            </div>
          </div>
        </section>

        <section className="panel landing-section" id="features">
          <div className="section-header">
            <h2>What you get</h2>
          </div>
          <div className="feature-grid" ref={featureRef}>
            {[
              { icon: "🎯", title: "ATS-grade scoring", desc: "Must-have weighting, section-aware coverage, semantic + keyword blend." },
              { icon: "🖍️", title: "Annotated resume", desc: "Live highlights of matched vs missing terms on your parsed PDF." },
              { icon: "💡", title: "One-click bullets", desc: "Tailored insertable bullets for Experience or Skills, ready to paste." },
              { icon: "🔗", title: "Flexible JD intake", desc: "Paste, fetch via URL, or upload .txt — auto-extract must-haves." },
              { icon: "🧭", title: "Role templates", desc: "Tech / Finance / Law profiles plus custom skill taxonomies." },
              { icon: "🛡️", title: "Local-first privacy", desc: "Keep data local; cache hashes; no unnecessary retention." },
            ].map((item, idx) => (
              <div
                className="feature-card"
                key={item.title}
                style={{ transitionDelay: `${idx * 80}ms` }}
              >
                <div className="feature-icon">{item.icon}</div>
                <div>
                  <div className="feature-title">{item.title}</div>
                  <div className="feature-desc">{item.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="panel landing-section" id="steps">
          <div className="section-header">
            <h2>How it works</h2>
            <p>Three quick steps to an ATS-aware match.</p>
          </div>
          <div className="steps">
            <div className="step">
              <div className="step-icon">1</div>
              <div>
                <div className="step-title">Upload your PDF</div>
                <div className="step-desc">We parse text and detect sections (Experience, Projects, Skills).</div>
              </div>
            </div>
            <div className="step">
              <div className="step-icon">2</div>
              <div>
                <div className="step-title">Paste or fetch the JD</div>
                <div className="step-desc">Auto-extract must-haves via dual keyword engines + synonyms.</div>
              </div>
            </div>
            <div className="step">
              <div className="step-icon">3</div>
              <div>
                <div className="step-title">Get score & actions</div>
                <div className="step-desc">ATS-style score, section-weighted coverage, and ready-made bullets to add.</div>
              </div>
            </div>
          </div>
        </section>

        <section className="panel landing-section" id="profiles">
          <div className="section-header">
            <h2>Choose your profile</h2>
            <p>Each profile swaps the skill taxonomy and ATS section weights.</p>
          </div>
          <div className="landing-grid">
            {PROFILE_OPTIONS.map((option) => (
              <button
                key={option.id}
                type="button"
                className="landing-card"
                onClick={() => setProfile(option.id)}
              >
                <div className="landing-tag">{option.label}</div>
                <div className="landing-title">Enter {option.label}</div>
                <p>
                  {option.id === "tech"
                    ? "System design, scalability, code reviews, and product engineering signals."
                    : option.id === "finance"
                    ? "Risk, performance, portfolio analysis, and reporting-focused signals."
                    : "Legal research, contract work, compliance, and practice area signals."}
                </p>
                <div className="landing-cta">Launch workspace →</div>
              </button>
            ))}
            <div className="landing-card custom-card">
              <div className="landing-tag">Custom</div>
              <div className="landing-title">Create your profile</div>
              <p>Give it a name and a comma-separated skill list.</p>
              <input
                type="text"
                placeholder="Profile name (e.g., Data Science)"
                value={newProfileName}
                onChange={(e) => setNewProfileName(e.target.value)}
              />
              <textarea
                placeholder="Skills, separated by commas"
                value={newProfileSkills}
                onChange={(e) => setNewProfileSkills(e.target.value)}
              />
              <button className="primary" type="button" onClick={addCustomProfile}>
                Save & Launch
              </button>
            </div>
          </div>
        </section>
      </main>
    );
  }

  return (
    <main className="shell">
      <div className="cursor" ref={cursorRef}></div>
      <div className="stars"></div>
      <div className="orb orb-a"></div>
      <div className="orb orb-b"></div>
      <div className="gridlines"></div>

      <nav className="topbar">
        <div className="brand">
          <div className="brand-badge">CV</div>
          <div>
            <div className="brand-title">MatchCV.io</div>
            <div className="brand-subtitle">AI-first resume calibration</div>
          </div>
        </div>
        <div className="nav-links">
          <a href="#analyze">Analyzer</a>
          <a href="#ats">ATS</a>
        </div>
        <div className="nav-actions">
          <button
            className="ghost nav-switch"
            onClick={() => setProfile(null)}
            type="button"
          >
            Switch Profile
          </button>
          <button
            className="icon-btn"
            onClick={() => setShowLogin(true)}
            aria-label="Open login"
            title="Login"
          >
            <svg viewBox="0 0 24 24" aria-hidden="true">
              <path
                d="M12 12a4 4 0 1 0-4-4 4 4 0 0 0 4 4Zm0 2c-4.2 0-7.6 2.4-8 5.5a1 1 0 0 0 1 1.1h14a1 1 0 0 0 1-1.1C19.6 16.4 16.2 14 12 14Z"
                fill="currentColor"
              />
            </svg>
          </button>
          <button
            className="nav-cta"
            onClick={() => document.getElementById("jobDesc")?.focus()}
          >
            Start Matching
          </button>
        </div>
      </nav>

      <header>
        <h1>MatchCV.io</h1>
        <p>
          Drop a resume, paste a job description, and get a match score in
          seconds.
        </p>
      </header>

      <section className="grid" id="analyze">
        <div className="panel">
                    <div
            className="dropzone"
            onDrop={handleDrop}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onClick={() => document.getElementById("fileInput")?.click()}
          >
            <strong>Drag & drop your resume PDF</strong>
            <span>or click to browse</span>
            <input
              id="fileInput"
              type="file"
              accept=".pdf"
              hidden
              onChange={(event) => onFileSelected(event.target.files?.[0])}
            />
            {fileName ? (
              <div className="file-pill">
                <span>{fileName}</span>
              </div>
            ) : null}
          </div>

          <div className="spacer" />
          <label htmlFor="jobDesc" className="status">
            Job Description
          </label>
          <div className="jd-actions">
            <input
              type="text"
              placeholder="Paste JD URL to fetch..."
              value={jdUrl}
              onChange={(e) => setJdUrl(e.target.value)}
            />
            <button
              type="button"
              className="ghost"
              onClick={async () => {
                if (!jdUrl.trim()) return;
                try {
                  const resp = await fetch("http://127.0.0.1:8000/scrape-job", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ url: jdUrl.trim() }),
                  });
                  if (!resp.ok) {
                    setStatus(`JD fetch failed: ${resp.status}`);
                    return;
                  }
                  const data = await resp.json();
                  setJobDesc(data.job_text || "");
                  setStatus("Job description fetched from URL.");
                } catch (err) {
                  setStatus("JD fetch failed.");
                }
              }}
            >
              Fetch JD
            </button>
            <label className="ghost jd-upload">
              Upload JD (.txt)
              <input
                type="file"
                accept=".txt"
                hidden
                onChange={(e) => loadJobFromFile(e.target.files?.[0])}
              />
            </label>
          </div>
          <textarea
            id="jobDesc"
            placeholder="Paste the job description here..."
            value={jobDesc}
            onChange={(event) => setJobDesc(event.target.value)}
          />

          <div className="spacer" />
          <div className="actions">
            <button className="primary" onClick={handleAnalyze}>
              Analyze Match
            </button>
            <label className="toggle">
              <input
                type="checkbox"
                checked={debugMode}
                onChange={(event) => setDebugMode(event.target.checked)}
              />
              <span>Debug</span>
            </label>
            <button className="ghost" onClick={handleExtract}>
              Load Resume Text
            </button>
            <button className="ghost" onClick={handleReset}>
              Reset
            </button>
          </div>
          <div className="mini-spacer" />
          <div className="status">{status}</div>
        </div>

        <div className="panel result">
          <div>
            <div className="status">Match Score</div>
            <div className="score">
              {score ?? "--"}
              <small>/100</small>
            </div>
          </div>
          <div>
            <div className="status">Missing Keywords</div>
            <div className="chip-list">
              {missing.length ? (
                missing.map((item) => (
                  <span className="chip" key={item}>
                    {item}
                  </span>
                ))
              ) : (
                <span className="chip">No data yet</span>
              )}
            </div>
          </div>
          <div>
            <div className="status">Coverage</div>
            <div className="chip-list">
              <span className="chip">Must-have: {coverage.must}%</span>
              <span className="chip">Nice-to-have: {coverage.nice}%</span>
            </div>
          </div>
          <div>
            <div className="status">Note</div>
            <p>
              For best results, make sure the resume is a text-based PDF (not
              scanned). The API must be running at{" "}
              <strong>http://127.0.0.1:8000</strong>.
            </p>
          </div>
        </div>
      </section>

      <section className="panel annotated" id="annotated">
        <div className="editor-header">
          <div>
            <div className="status">Annotated Resume</div>
            <p>
              Your parsed CV with highlights. Cyan = matched terms. Amber = suggested adds.
            </p>
          </div>
          <div className="chip-list">
            <span className="chip">Matched: {matched.length}</span>
            <span className="chip">Missing: {missing.length}</span>
          </div>
        </div>
        <div
          className="highlight-pane resume-pane"
          dangerouslySetInnerHTML={{
            __html: highlightWithStatus(
              resumeView || resumeText || "Analyze a resume to see highlights.",
              matched,
              missing
            ),
          }}
        />
        <div className="suggestions compact">
          <div>
            <div className="status">Actionable Suggestions</div>
            <div className="chip-list vertical">
              {buildActionSuggestions().length ? (
                buildActionSuggestions().map((item) => (
                  <div className="action-chip" key={item.term}>
                    <div className="chip-label">
                      <strong>{item.target}</strong> · {item.term}
                    </div>
                    <div className="chip-note">{item.text}</div>
                    <button
                      type="button"
                      className="ghost tiny"
                      onClick={() => navigator.clipboard.writeText(item.text)}
                    >
                      Copy
                    </button>
                  </div>
                ))
              ) : (
                <span className="chip">No suggestions yet</span>
              )}
            </div>
          </div>
        </div>
      </section>

      <section className="panel ats" id="ats">
        <div className="editor-header">
          <div>
            <div className="status">ATS Sections Panel</div>
            <p>Breakdown of key ATS signals for this role.</p>
          </div>
          <div className="chip-list">
            <span className="chip">
              Profile: {debugData?.ats_profile || "General"}
            </span>
            <span className="chip">Score: {score ?? "--"}</span>
          </div>
        </div>
        {atsSections.length ? (
          <div className="ats-grid">
            {atsSections.map((item) => {
              const pct = Math.min(100, item.score || 0);
              const isOpen = openSections[item.key];
              return (
                <div
                  className={`ats-card ${isOpen ? "open" : ""}`}
                  key={item.key}
                  onClick={() =>
                    setOpenSections((prev) => ({
                      ...prev,
                      [item.key]: !prev[item.key],
                    }))
                  }
                  onKeyDown={(event) => {
                    if (event.key === "Enter" || event.key === " ") {
                      event.preventDefault();
                      setOpenSections((prev) => ({
                        ...prev,
                        [item.key]: !prev[item.key],
                      }));
                    }
                  }}
                  role="button"
                  tabIndex={0}
                >
                  <div className="ats-header">
                    <span>{item.label}</span>
                    <span className="ats-value">
                      {item.mode === "title"
                        ? item.score > 0
                          ? "Yes"
                          : "No"
                        : `${pct}%`}
                    </span>
                  </div>
                  <div className="ats-bar">
                    <div className="ats-bar-fill" style={{ width: `${pct}%` }}></div>
                  </div>
                  {isOpen ? (
                    <div className="ats-detail">
                      <div>
                        <div className="status">Matched</div>
                        <div className="chip-list">
                          {item.matchedTerms.length ? (
                            item.matchedTerms.map((term) => (
                              <span className="chip chip-soft" key={term}>
                                {term}
                              </span>
                            ))
                          ) : (
                            <span className="chip">None</span>
                          )}
                        </div>
                      </div>
                      <div>
                        <div className="status">Missing</div>
                        <div className="chip-list">
                          {item.missingTerms.length ? (
                            item.missingTerms.map((term) => (
                              <span className="chip chip-warn" key={term}>
                                {term}
                              </span>
                            ))
                          ) : (
                            <span className="chip">None</span>
                          )}
                        </div>
                      </div>
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        ) : (
          <p className="muted-note">
            Paste a job description and load a resume to see ATS sections.
          </p>
        )}
      </section>

      {showLogin ? (
        <div className="login-overlay" role="dialog" aria-modal="true">
          <div className="login-card">
            <button className="login-close" onClick={() => setShowLogin(false)}>
              X
            </button>
            <div className="login-header">
              <div className="brand-badge">CV</div>
              <div>
                <h2>Welcome back</h2>
                <p>Sign in to save sessions and track match history.</p>
              </div>
            </div>
            <div className="login-form">
              <label>
                Email
                <input type="email" placeholder="you@domain.com" />
              </label>
              <label>
                Password
                <input type="password" placeholder="********" />
              </label>
              <div className="login-actions">
                <button className="primary" type="button">
                  Sign in
                </button>
                <button className="ghost" type="button">
                  Create account
                </button>
              </div>
              <div className="status">
                This is a UI-only screen for now. No data is stored.
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </main>
  );
}

export default App;
