// Single source of truth for the backend base URL.
// Same-origin by default: the dev server proxies /api to the backend on :8006
// (vite.config.ts), so the app works from whichever host it is opened on —
// including another tailnet device via `qt serve up`, where a hardcoded
// localhost:8006 would point at the viewer's own machine. Overridable via
// VITE_API_URL.
export const API_BASE_URL = import.meta.env.VITE_API_URL || "/api";
