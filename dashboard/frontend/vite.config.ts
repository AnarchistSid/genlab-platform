import { defineConfig } from "vite";
import react, { reactCompilerPreset } from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import babel from "@rolldown/plugin-babel";
import path from "path";

// Vite 8 / @vitejs/plugin-react@6 removed the inline `babel` option;
// React Compiler now ships via the separate @rolldown/plugin-babel
// chain so the official preset can be composed cleanly. Same runtime
// behaviour (RC enabled for the project) — just expressed through the
// new API.

// Build-time version injection — resolves the 'Dashboard 0.0.0'
// symptom surfaced by the 2026-06-25 prod audit. The frontend reads
// `__APP_VERSION__` / `__BUILD_TIME__` as compile-time globals; the
// values come from env vars set by scripts/deploy.sh (or CI on
// auto-deploy). Falls back to "0.0.0-dev" / empty string for local
// dev / pre-injection deployments so existing UX is preserved when
// the env isn't set.
//
// Reads from process.env (not exec) per repo workflow-security
// guidance. The deploy script computes `git rev-parse --short HEAD`
// and writes the result to VITE_APP_VERSION before `npm run build`.
const APP_VERSION = process.env.VITE_APP_VERSION || "0.0.0-dev";
const BUILD_TIME = process.env.VITE_BUILD_TIME || "";

export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(APP_VERSION),
    __BUILD_TIME__: JSON.stringify(BUILD_TIME),
  },
  plugins: [
    react(),
    babel({ presets: [reactCompilerPreset()] }),
    tailwindcss(),
  ],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://localhost:5151",
        changeOrigin: true,
      },
      "/socket.io": {
        target: "http://localhost:5151",
        ws: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: "hidden",
    // Preserve old hash-named chunks across builds so in-flight tabs
    // loaded with the previous index.html can still fetch their
    // lazy-loaded routes after a deploy. Vite normally wipes outDir
    // before each build, which deletes the prior build's chunks and
    // causes "Failed to fetch dynamically imported module" errors on
    // route navigation in tabs that haven't been hard-refreshed.
    // index.html is still overwritten (it's a fixed filename) so fresh
    // tabs get the latest chunk references. Hash-named chunks (the
    // *only* assets that need preservation) accumulate harmlessly —
    // typical drift is ~1MB/month; cleanup via `find dist/assets/
    // -mtime +30 -delete` whenever disk pressure warrants.
    emptyOutDir: false,
    rollupOptions: {
      output: {
        // Vite 8 / Rollup 5 narrowed manualChunks's TS type to the
        // function form only — object-map is still supported at
        // runtime but tsc no longer overloads it. Translate the
        // chunk map into a function so types stay strict.
        manualChunks: (() => {
          const groups: Record<string, string[]> = {
            "react-vendor": ["react", "react-dom", "react-router-dom"],
            "query-vendor": ["@tanstack/react-query"],
            "chart-vendor": ["recharts"],
            "ui-vendor": [
              "radix-ui",
              "clsx",
              "class-variance-authority",
              "tailwind-merge",
              "cmdk",
              "sonner",
            ],
            "motion-vendor": ["framer-motion"],
            "dnd-vendor": [
              "@dnd-kit/core",
              "@dnd-kit/sortable",
              "@dnd-kit/utilities",
              "@tanstack/react-virtual",
            ],
          };
          // Build a reverse lookup at config-load time — O(modules)
          // per-build cost instead of O(groups × modules) on every
          // chunk match.
          const lookup = new Map<string, string>();
          for (const [chunk, deps] of Object.entries(groups)) {
            for (const dep of deps) {
              lookup.set(dep, chunk);
            }
          }
          return (id: string) => {
            // Match `/node_modules/<pkg>/...` — pkg may be scoped (@org/name).
            const m = id.match(/node_modules\/(?:(@[^/]+)\/)?([^/]+)/);
            if (!m) return null;
            const pkg = m[1] ? `${m[1]}/${m[2]}` : m[2];
            return lookup.get(pkg) ?? null;
          };
        })(),
      },
    },
  },
});
