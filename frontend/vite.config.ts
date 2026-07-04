import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Dev proxy forwards API calls to the FastAPI backend so the SPA and API share
// an origin in development (avoids CORS). In production the frontend is served
// behind the same ingress as the API (see deploy/helm).
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ""),
      },
    },
  },
});
