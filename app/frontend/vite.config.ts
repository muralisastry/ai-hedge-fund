import react from "@vitejs/plugin-react";
import path from "path";
import { defineConfig } from "vite";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5177,
    strictPort: true,
    host: "127.0.0.1",
    // Reached from other tailnet devices through `qt serve up` (tailscale
    // serve → 127.0.0.1), which forwards the *.ts.net Host header.
    allowedHosts: [".ts.net"],
    // Same-origin API: the backend's routes sit at its root (/hedge-fund,
    // /ollama, …), so /api is a proxy-only prefix and is stripped.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8006",
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
