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
        manualChunks: {
          "react-vendor": [
            "react",
            "react-dom",
            "react-router-dom",
          ],
          "query-vendor": [
            "@tanstack/react-query",
          ],
          "chart-vendor": [
            "recharts",
          ],
          "ui-vendor": [
            "radix-ui",
            "clsx",
            "class-variance-authority",
            "tailwind-merge",
            "cmdk",
            "sonner",
          ],
          "motion-vendor": [
            "framer-motion",
          ],
          "dnd-vendor": [
            "@dnd-kit/core",
            "@dnd-kit/sortable",
            "@dnd-kit/utilities",
            "@tanstack/react-virtual",
          ],
        },
      },
    },
  },
});
