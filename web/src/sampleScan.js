// Hardcoded sample analysis — no backend calls, doesn't decrement scan limit.
// Replace the "result" payload with a real captured scan output later for higher fidelity.

export const SAMPLE_JOB_DESCRIPTION = `Senior Product Manager — Growth

We're hiring a Senior Product Manager to lead our self-serve growth team. You'll own the activation funnel end-to-end, define and ship experiments that move conversion, and partner closely with engineering, design, and data.

Responsibilities:
- Own the activation funnel from sign-up through first value moment
- Run weekly experiments using our in-house A/B testing platform
- Define product KPIs and report progress to the leadership team
- Partner with engineering to scope and ship work in 2-week cycles
- Translate qualitative user research into prioritised product bets
- Build pricing & packaging experiments alongside the commercial team

Requirements:
- 5+ years product management experience, ideally at a SaaS or growth-focused company
- Strong analytical skills — comfortable in SQL, dashboards, A/B test interpretation
- Track record of shipping experiments that materially moved a metric
- Excellent written communication; you'll write PRDs, post-mortems, and stakeholder updates
- Experience working with engineering and design teams in an agile environment

Nice to have:
- Background in product-led growth or freemium SaaS
- Experience with pricing and packaging
- Familiarity with tools like Amplitude, Mixpanel, or Heap`;

export const SAMPLE_RESULT = {
  match_score: 76,

  role_fit_breakdown: {
    job_description: {
      role_title: "Senior Product Manager — Growth",
      seniority: "Senior",
    },

    responsibility_detail: {
      matched_count: 5,
      total_responsibilities: 7,
    },

    matched_responsibilities: [
      {
        responsibility: "Own the activation funnel from sign-up through first value moment",
        confidence: "strong",
        category: "essential",
        evidence: "[Product Manager @ Lumen] Redesigned the activation funnel, lifting day-7 retention from 22% to 34% over two quarters.",
      },
      {
        responsibility: "Run weekly experiments using an A/B testing platform",
        confidence: "strong",
        category: "essential",
        evidence: "[Product Manager @ Lumen] Shipped 60+ experiments through our Optimizely instance; 18 winners drove a cumulative +14% on signup-to-paid conversion.",
      },
      {
        responsibility: "Define product KPIs and report progress to the leadership team",
        confidence: "strong",
        category: "essential",
        evidence: "[Product Manager @ Lumen] Defined and owned the activation north-star metric, presenting weekly to the C-suite.",
      },
      {
        responsibility: "Partner with engineering to scope and ship work in 2-week cycles",
        confidence: "strong",
        category: "essential",
        evidence: "[Associate PM @ Northwind] Ran a 4-engineer pod on 2-week sprints, shipping 80% of committed scope across 18 consecutive cycles.",
      },
      {
        responsibility: "Translate qualitative user research into prioritised product bets",
        confidence: "partial",
        category: "essential",
        evidence: "[Product Manager @ Lumen] Synthesised 30+ customer interviews into the FY24 roadmap themes.",
      },
    ],

    missing_responsibilities: [
      {
        responsibility: "Build pricing & packaging experiments alongside the commercial team",
        category: "essential",
      },
      {
        responsibility: "Familiarity with Amplitude, Mixpanel, or Heap",
        category: "nice_to_have",
      },
    ],

    experience_detail: {
      required_years: 5,
      candidate_years: 6,
      meets_requirement: true,
    },

    skills_detail: {
      must_have: [
        { skill: "Product management", status: "present" },
        { skill: "A/B testing", status: "present" },
        { skill: "SQL", status: "present" },
        { skill: "Stakeholder communication", status: "present" },
        { skill: "Pricing & packaging", status: "missing" },
      ],
      nice_to_have: [
        { skill: "Amplitude / Mixpanel", status: "missing" },
        { skill: "Product-led growth", status: "partial" },
      ],
    },
  },

  ats_keywords: {
    hard_skills: [
      { keyword: "A/B testing", status: "present", jd_count: 3, cv_count: 4 },
      { keyword: "SQL", status: "present", jd_count: 2, cv_count: 2 },
      { keyword: "activation funnel", status: "present", jd_count: 2, cv_count: 3 },
      { keyword: "experimentation", status: "present", jd_count: 3, cv_count: 3 },
      { keyword: "PRD", status: "low", jd_count: 1, cv_count: 0 },
      { keyword: "pricing", status: "missing", jd_count: 2, cv_count: 0 },
      { keyword: "packaging", status: "missing", jd_count: 2, cv_count: 0 },
      { keyword: "Amplitude", status: "missing", jd_count: 1, cv_count: 0 },
    ],
    soft_skills: [
      { keyword: "stakeholder communication", status: "present", jd_count: 2, cv_count: 2 },
      { keyword: "cross-functional", status: "present", jd_count: 2, cv_count: 3 },
      { keyword: "written communication", status: "low", jd_count: 1, cv_count: 0 },
    ],
  },

  section_feedback: {
    summary: {
      verdict: "needs_work",
      summary_line: "Names the target function (growth PM) but stops short of mirroring Lumen's specific positioning around self-serve activation.",
      strengths: [
        "Names target function directly: 'Growth-focused product manager with 6 years experience...'",
      ],
      improvements: [
        {
          issue: "Generic PM framing — doesn't mirror Lumen's 'self-serve' or 'customer activation' language.",
          fix: "Rewrite opening line as: 'Growth PM with 6 years building self-serve activation funnels across two SaaS companies.' Mirrors the JD verbatim.",
        },
      ],
    },
    experience: {
      verdict: "good",
      summary_line: "Each role leads with a quantified outcome — strong recruiter-scan signal. One bullet on pricing/packaging would close the largest visible gap.",
      strengths: [
        "Lumen bullets all quantify impact: retention 22%→34%, 60+ experiments, 18 winners, +14% on conversion.",
        "Bullets connect action to metric without burying the result.",
      ],
      improvements: [
        {
          issue: "No pricing or packaging work represented — JD names this as an essential responsibility.",
          fix: "Either add a bullet under Lumen referencing any pricing test you ran, or add a one-line 'Adjacent work' note acknowledging the gap honestly.",
        },
      ],
    },
    skills: {
      verdict: "good",
      summary_line: "Covers the JD's hard requirements (SQL, A/B testing) but is missing the product-analytics tooling the JD calls out by name.",
      strengths: [
        "Hard skills cover SQL, experimentation, and stakeholder communication — all JD must-haves.",
      ],
      improvements: [
        {
          issue: "JD names Amplitude/Mixpanel/Heap as nice-to-have; none appear in your skills section.",
          fix: "Add a 'Product analytics' subsection listing whichever of Amplitude/Mixpanel/Heap you've actually touched. Even basic familiarity counts here.",
        },
      ],
    },
    projects: {
      verdict: "strong",
      summary_line: "No standalone projects section, but your role bullets read like projects with measurable outcomes — appropriate for a PM CV.",
      strengths: [
        "Your work-experience bullets effectively function as project mini-cases (e.g. 'activation funnel redesign').",
      ],
      improvements: [],
    },
    education: {
      verdict: "good",
      summary_line: "Education is concise and relevant; doesn't dominate the CV which is right for a 6-year-experienced PM.",
      strengths: [
        "BSc Computer Science from Manchester — institution and degree both clearly listed.",
      ],
      improvements: [],
    },
  },

  resume_text: "JORDAN SMITH\nSenior Product Manager\n\nSUMMARY\nGrowth-focused product manager with 6 years experience leading activation, experimentation, and retention work across two SaaS companies.\n\nWORK EXPERIENCE\n\nLumen (Series B SaaS) — Product Manager, Growth\n2022 – Present\n• Redesigned the activation funnel, lifting day-7 retention from 22% to 34% over two quarters.\n• Shipped 60+ experiments through Optimizely; 18 winners drove +14% on signup-to-paid conversion.\n• Defined and owned the activation north-star metric, presenting weekly to the C-suite.\n• Synthesised 30+ customer interviews into the FY24 roadmap themes.\n\nNorthwind — Associate PM\n2019 – 2022\n• Ran a 4-engineer pod on 2-week sprints, shipping 80% of committed scope across 18 cycles.\n• Owned onboarding for the SMB segment; reduced time-to-first-value from 11 to 4 days.\n\nSKILLS\nProduct management, A/B testing, SQL, experimentation, stakeholder communication, cross-functional leadership.\n\nEDUCATION\nB.Sc. Computer Science, University of Manchester, 2019.",

  cv_highlights: [
    "Lifted day-7 retention from 22% → 34% in two quarters at Lumen.",
    "Ran 60+ experiments; 18 winners drove +14% on signup-to-paid conversion.",
    "Reduced time-to-first-value from 11 days to 4 days at Northwind.",
  ],

  candidate_profile: {
    name: "Jordan Smith",
    current_title: "Product Manager, Growth",
    years_experience: 6,
    seniority: "Senior",
  },

  cv_sections_analysis: {
    sections_found: ["summary", "work_experience", "skills", "education"],
    sections_missing: ["projects", "certifications"],
    quality_score: 82,
  },

  score_breakdown: {
    current_score: 76,
    potential_score: 89,
    verdict_line: "You're in competitive range. Closing the gaps below could lift you to 89.",
    factors_pulling_down: [
      {
        label: "1 essential responsibility not evidenced",
        points_lost: 7,
        fix: "Add bullets that explicitly evidence: Build pricing & packaging experiments alongside the commercial team",
      },
      {
        label: "1 responsibility only partially evidenced",
        points_lost: 4,
        fix: "Strengthen evidence with metrics or exact-keyword bullets for: Translate qualitative user research into prioritised product bets",
      },
      {
        label: "1 ATS keyword from the JD not present",
        points_lost: 2,
        fix: "Mirror these JD keywords in your CV (only if you genuinely have the experience): Amplitude, Mixpanel, Heap",
      },
    ],
    factors_pulling_up: [
      "4 responsibilities clearly evidenced in your experience",
      "4 of 5 must-have skills already in your CV",
      "Years of experience meets or exceeds the requirement",
    ],
  },
};

export const SAMPLE_STATE = {
  result: SAMPLE_RESULT,
  fileName: "sample-jordan-smith-growth-pm.pdf",
  jobSource: "sample",
  jobDescription: SAMPLE_JOB_DESCRIPTION,
  isSample: true,
};
