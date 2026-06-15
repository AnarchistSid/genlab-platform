import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";

export default defineConfig({
  plugins: [
    react({
      babel: {
        plugins: [["babel-plugin-react-compiler", {}]],
      },
    }),
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
