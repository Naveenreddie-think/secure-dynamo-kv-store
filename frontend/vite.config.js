import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base MUST match the sub-path main.py serves this bundle from
// (StaticFiles mounted at /dashboard) -- Vite's default root-relative
// asset URLs (/assets/...) would 404 once served from a sub-path instead
// of the origin root.
export default defineConfig({
  plugins: [react()],
  base: "/dashboard/",
  server: {
    port: 5173,
  },
});
