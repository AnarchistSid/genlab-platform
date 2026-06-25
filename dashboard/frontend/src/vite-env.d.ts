/// <reference types="vite/client" />

// Build-time globals defined by vite.config.ts `define` block.
// Values come from VITE_APP_VERSION / VITE_BUILD_TIME env vars set
// by scripts/deploy.sh (or CI on auto-deploy). Resolves the
// 'Dashboard 0.0.0' symptom surfaced by the 2026-06-25 prod audit.
declare const __APP_VERSION__: string;
declare const __BUILD_TIME__: string;
