import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import "./styles/globals.css";

// Apply saved theme on load (before React renders to prevent flash)
const savedTheme = localStorage.getItem("genlab-theme");
if (savedTheme === "light") {
  document.documentElement.setAttribute("data-theme", "light");
  document.documentElement.classList.add("light");
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);

// Register service worker for PWA support
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => {
      // SW registration failed — non-critical, ignore
    });
  });
}
