import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `npm run dev` proxies API calls to the local API (docker compose or uvicorn on :8000).
const api = { target: "http://localhost:8000", changeOrigin: false };

export default defineConfig({
  plugins: [react()],
  // Never inline assets as data: URIs: the CSP (default-src 'self') blocks them, so every
  // font and image must ship as a same-origin file.
  build: { assetsInlineLimit: 0 },
  server: { proxy: { "/v1": api, "/checkout-api": api, "/dashboard-api": api } },
});
