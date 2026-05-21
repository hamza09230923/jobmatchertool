const DEFAULT_API_BASE_URL = import.meta.env.PROD
  ? "https://jobmatchertool.onrender.com"
  : "http://localhost:8000";

export const API_BASE_URL = (
  import.meta.env.VITE_API_BASE_URL || DEFAULT_API_BASE_URL
).trim().replace(/\/$/, "");
