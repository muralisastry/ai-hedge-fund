// Single source of truth for the backend base URL.
// Overridable via VITE_API_URL (see app/frontend/.env); default port is the
// one registered for `hedgefund` in the quantai orchestrator.
export const API_BASE_URL =
  import.meta.env.VITE_API_URL || "http://localhost:8006";
