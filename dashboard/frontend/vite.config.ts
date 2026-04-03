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
