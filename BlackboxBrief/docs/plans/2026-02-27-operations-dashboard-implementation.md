# Operations Command Center — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an AI-native operations dashboard at `review.aspirehub.ai` that replaces Microsoft Lists as the daily interface, with 9 views, 28+ API endpoints, real-time updates, keyboard-first workflow, and AI-powered features.

**Architecture:** React 19 SPA (TypeScript, Vite, shadcn/ui) served by the existing Flask backend. Flask becomes API-only + static file server. Microsoft Lists remains the headless database. Socket.IO provides real-time updates. OpenAI gpt-4o-mini powers the AI command bar.

**Tech Stack:** React 19, TypeScript, Vite 6, TanStack (Router + Query + Virtual), shadcn/ui, Tailwind CSS 4, Recharts, Framer Motion, cmdk, Sonner, dnd-kit, nuqs, Zod, Socket.IO client, Zustand + Immer

**Design doc:** `docs/plans/2026-02-27-operations-dashboard-design.md`

---

## Phase 1: Foundation (Tasks 1-8)

Bootstrap the React app, set up tooling, create app shell, refactor Flask to serve the SPA.

### Task 1: Install Node.js

Node.js and npm are not currently installed on this machine.

**Step 1: Install Node.js via Homebrew**

Run:
```bash
brew install node
```

**Step 2: Verify installation**

Run:
```bash
node --version && npm --version
```
Expected: Node v20+ and npm v10+

**Step 3: Commit — no code changes, just environment setup**

No commit needed — this is a system-level install.

---

### Task 2: Scaffold React App with Vite

**Files:**
- Create: `dashboard/package.json`
- Create: `dashboard/vite.config.ts`
- Create: `dashboard/tsconfig.json`
- Create: `dashboard/tsconfig.node.json`
- Create: `dashboard/index.html`
- Create: `dashboard/src/main.tsx`
- Create: `dashboard/src/app.tsx`
- Create: `dashboard/src/styles/globals.css`
- Modify: `.gitignore` — add `dashboard/node_modules/`, `dashboard/dist/`

**Step 1: Create Vite React-TS project**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper"
npm create vite@latest dashboard -- --template react-ts
```

**Step 2: Install dependencies**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npm install
```

**Step 3: Configure Vite proxy for Flask API**

Replace `dashboard/vite.config.ts` with:

```typescript
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
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
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          recharts: ["recharts"],
          "framer-motion": ["framer-motion"],
        },
      },
    },
  },
});
```

**Step 4: Update .gitignore**

Append to `.gitignore`:
```
# Dashboard
dashboard/node_modules/
dashboard/dist/
```

**Step 5: Verify dev server starts**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npm run dev -- --host 127.0.0.1
```
Expected: Vite dev server on http://localhost:5173

Kill the server after verifying.

**Step 6: Commit**

```bash
git add dashboard/ .gitignore
git commit -m "feat: scaffold React dashboard with Vite + TypeScript"
```

---

### Task 3: Install Core Dependencies

**Files:**
- Modify: `dashboard/package.json`

**Step 1: Install production dependencies**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npm install @tanstack/react-router @tanstack/react-query @tanstack/react-virtual \
  zustand immer \
  tailwindcss @tailwindcss/vite \
  framer-motion \
  cmdk sonner nuqs \
  @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities \
  recharts \
  zod \
  socket.io-client \
  clsx tailwind-merge \
  lucide-react \
  class-variance-authority
```

**Step 2: Install dev dependencies**

Run:
```bash
npm install -D @types/node
```

**Step 3: Configure Tailwind CSS v4**

Replace `dashboard/src/styles/globals.css` with:

```css
@import "tailwindcss";

@theme {
  --color-bg-primary: #0a0a0a;
  --color-bg-surface: #141414;
  --color-bg-elevated: #1c1c1c;
  --color-border: #262626;
  --color-text-primary: #fafafa;
  --color-text-secondary: #a1a1aa;
  --color-text-muted: #52525b;
  --color-accent: #6366f1;
  --color-accent-secondary: #8b5cf6;
  --color-success: #22c55e;
  --color-warning: #f59e0b;
  --color-error: #ef4444;
  --color-info: #06b6d4;

  --font-sans: "Inter", ui-sans-serif, system-ui, sans-serif;
  --font-mono: "JetBrains Mono", ui-monospace, monospace;
}

body {
  background-color: var(--color-bg-primary);
  color: var(--color-text-primary);
  font-family: var(--font-sans);
}
```

**Step 4: Add Tailwind Vite plugin**

Update `dashboard/vite.config.ts` — add to plugins array:

```typescript
import tailwindcss from "@tailwindcss/vite";

// In plugins array:
plugins: [react(), tailwindcss()],
```

**Step 5: Commit**

```bash
git add dashboard/package.json dashboard/package-lock.json dashboard/src/styles/globals.css dashboard/vite.config.ts
git commit -m "feat: install core dashboard dependencies (TanStack, shadcn, Tailwind, etc.)"
```

---

### Task 4: Set Up shadcn/ui

**Files:**
- Create: `dashboard/components.json`
- Create: `dashboard/src/lib/utils.ts`
- Create: `dashboard/src/components/ui/button.tsx`
- Create: `dashboard/src/components/ui/card.tsx`
- Create: `dashboard/src/components/ui/badge.tsx`
- Create: `dashboard/src/components/ui/input.tsx`
- Create: `dashboard/src/components/ui/dialog.tsx`
- Create: `dashboard/src/components/ui/dropdown-menu.tsx`
- Create: `dashboard/src/components/ui/tooltip.tsx`
- Create: `dashboard/src/components/ui/separator.tsx`
- Create: `dashboard/src/components/ui/skeleton.tsx`
- Create: `dashboard/src/components/ui/scroll-area.tsx`
- Create: `dashboard/src/components/ui/select.tsx`
- Create: `dashboard/src/components/ui/textarea.tsx`
- Create: `dashboard/src/components/ui/tabs.tsx`

**Step 1: Initialize shadcn/ui**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npx shadcn@latest init
```

When prompted:
- Style: **New York**
- Base color: **Zinc**
- CSS variables: **Yes**

**Step 2: Install core components**

Run:
```bash
npx shadcn@latest add button card badge input dialog dropdown-menu tooltip separator skeleton scroll-area select textarea tabs
```

**Step 3: Create utility function**

Verify `dashboard/src/lib/utils.ts` was created with:

```typescript
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
```

**Step 4: Commit**

```bash
git add dashboard/
git commit -m "feat: initialize shadcn/ui with core components (New York variant)"
```

---

### Task 5: Create App Shell & Routing

**Files:**
- Create: `dashboard/src/app.tsx`
- Create: `dashboard/src/components/layout/shell.tsx`
- Create: `dashboard/src/components/layout/sidebar.tsx`
- Create: `dashboard/src/views/pipeline.tsx`
- Create: `dashboard/src/views/blueprints.tsx`
- Create: `dashboard/src/views/schedule.tsx`
- Create: `dashboard/src/views/analytics.tsx`
- Create: `dashboard/src/views/stories.tsx`
- Create: `dashboard/src/views/runs.tsx`
- Create: `dashboard/src/views/settings.tsx`
- Create: `dashboard/src/views/focus-review.tsx`
- Modify: `dashboard/src/main.tsx`

**Step 1: Create the router configuration**

Create `dashboard/src/app.tsx`:

```tsx
import { lazy, Suspense } from "react";
import {
  createBrowserRouter,
  RouterProvider,
  Outlet,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "sonner";
import { Shell } from "@/components/layout/shell";

// Note: If TanStack Router setup proves complex during scaffolding,
// fall back to react-router-dom v7 initially (already widely used,
// simpler setup) and migrate to TanStack Router in Phase 5.
// The routing structure and lazy loading remain identical either way.

const PipelineView = lazy(() => import("@/views/pipeline"));
const BlueprintsView = lazy(() => import("@/views/blueprints"));
const ScheduleView = lazy(() => import("@/views/schedule"));
const AnalyticsView = lazy(() => import("@/views/analytics"));
const StoriesView = lazy(() => import("@/views/stories"));
const RunsView = lazy(() => import("@/views/runs"));
const SettingsView = lazy(() => import("@/views/settings"));
const FocusReviewView = lazy(() => import("@/views/focus-review"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 8_000, // Match Microsoft Lists cache TTL
      refetchInterval: 8_000,
      retry: 2,
    },
  },
});

function Layout() {
  return (
    <Shell>
      <Suspense fallback={<div className="flex items-center justify-center h-full">Loading...</div>}>
        <Outlet />
      </Suspense>
    </Shell>
  );
}

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <PipelineView /> },
      { path: "/blueprints", element: <BlueprintsView /> },
      { path: "/blueprints/:id", element: <BlueprintsView /> },
      { path: "/schedule", element: <ScheduleView /> },
      { path: "/analytics", element: <AnalyticsView /> },
      { path: "/stories", element: <StoriesView /> },
      { path: "/runs", element: <RunsView /> },
      { path: "/runs/:id", element: <RunsView /> },
      { path: "/settings", element: <SettingsView /> },
    ],
  },
  { path: "/focus-review", element: <FocusReviewView /> },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: { background: "#141414", border: "1px solid #262626", color: "#fafafa" },
        }}
      />
    </QueryClientProvider>
  );
}
```

**Step 2: Create the Shell layout**

Create `dashboard/src/components/layout/shell.tsx`:

```tsx
import { Sidebar } from "./sidebar";

export function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex h-screen overflow-hidden bg-bg-primary">
      <Sidebar />
      <main className="flex-1 overflow-auto p-6">{children}</main>
    </div>
  );
}
```

**Step 3: Create the Sidebar**

Create `dashboard/src/components/layout/sidebar.tsx`:

```tsx
import { NavLink } from "react-router-dom";
import {
  LayoutDashboard,
  FileText,
  Calendar,
  BarChart3,
  Newspaper,
  Play,
  Settings,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  { to: "/", icon: LayoutDashboard, label: "Pipeline", shortcut: "G P" },
  { to: "/blueprints", icon: FileText, label: "Content", shortcut: "G B" },
  { to: "/schedule", icon: Calendar, label: "Schedule", shortcut: "G S" },
  { to: "/analytics", icon: BarChart3, label: "Analytics", shortcut: "G A" },
  { to: "/stories", icon: Newspaper, label: "Stories", shortcut: "G T" },
  { to: "/runs", icon: Play, label: "Runs", shortcut: "G R" },
  { to: "/settings", icon: Settings, label: "Settings", shortcut: "G ," },
];

export function Sidebar() {
  return (
    <aside className="w-56 shrink-0 border-r border-border bg-bg-surface flex flex-col">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-border">
        <span className="text-sm font-semibold tracking-tight text-text-primary">
          Blackbox Brief
        </span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 py-2 px-2 space-y-0.5">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={item.to === "/"}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm transition-colors",
                isActive
                  ? "bg-accent/10 text-accent"
                  : "text-text-secondary hover:text-text-primary hover:bg-bg-elevated"
              )
            }
          >
            <item.icon className="h-4 w-4" />
            <span className="flex-1">{item.label}</span>
            <kbd className="text-[10px] text-text-muted font-mono">{item.shortcut}</kbd>
          </NavLink>
        ))}
      </nav>

      {/* Footer */}
      <div className="p-4 border-t border-border">
        <div className="flex items-center gap-2">
          <kbd className="text-[10px] text-text-muted bg-bg-elevated px-1.5 py-0.5 rounded font-mono">
            Cmd+K
          </kbd>
          <span className="text-xs text-text-muted">Command palette</span>
        </div>
      </div>
    </aside>
  );
}
```

**Step 4: Create placeholder views**

Create each view file with a simple placeholder. Example for `dashboard/src/views/pipeline.tsx`:

```tsx
export default function PipelineView() {
  return (
    <div>
      <h1 className="text-2xl font-semibold tracking-tight mb-6">Pipeline Overview</h1>
      <p className="text-text-secondary">Coming soon...</p>
    </div>
  );
}
```

Create the same pattern for: `blueprints.tsx`, `schedule.tsx`, `analytics.tsx`, `stories.tsx`, `runs.tsx`, `settings.tsx`, `focus-review.tsx` — each with the appropriate title.

**Step 5: Update main.tsx entry point**

Replace `dashboard/src/main.tsx`:

```tsx
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./app";
import "./styles/globals.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>
);
```

**Step 6: Verify the app renders**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npm run dev -- --host 127.0.0.1
```

Open http://localhost:5173 — should see the sidebar with navigation links and "Pipeline Overview" as the home page. Click each nav link to verify routing works.

**Step 7: Commit**

```bash
git add dashboard/src/
git commit -m "feat: app shell with sidebar navigation and lazy-loaded route placeholders"
```

---

### Task 6: Refactor Flask to Serve SPA

**Files:**
- Modify: `execution/review_server.py`
- Test: `tests/test_review_server.py`

**Step 1: Read the current `index()` route and DASHBOARD_HTML**

The current `index()` route (around line 208-210) returns the embedded DASHBOARD_HTML string (lines 908-2100). We need to:
1. Replace `index()` to serve `dashboard/dist/index.html`
2. Add a catch-all route for client-side routing
3. Keep ALL existing `/api/*` routes untouched
4. Remove the DASHBOARD_HTML variable (1,200 lines of embedded HTML)

**Step 2: Add SPA serving routes**

At the top of review_server.py, add/modify:

```python
from flask import send_from_directory

_DASHBOARD_DIST = PROJECT_ROOT / "dashboard" / "dist"
```

Replace the `index()` route with:

```python
@app.route("/")
def index():
    """Serve the React SPA."""
    dist = _DASHBOARD_DIST
    if dist.exists() and (dist / "index.html").exists():
        return send_from_directory(str(dist), "index.html")
    # Fallback: if dashboard not built, show a simple message
    return Response(
        "<html><body style='background:#0a0a0a;color:#fafafa;font-family:sans-serif;padding:40px'>"
        "<h1>Dashboard not built</h1>"
        "<p>Run <code>cd dashboard && npm run build</code> to build the React app.</p>"
        "</body></html>",
        mimetype="text/html",
    )
```

Add the SPA catch-all route (AFTER all `/api/*` routes, BEFORE the `if __name__` block):

```python
@app.route("/<path:path>")
def serve_spa(path):
    """Serve static files from dashboard/dist, fall back to index.html for client-side routing."""
    dist = _DASHBOARD_DIST
    file_path = dist / path
    if file_path.exists() and file_path.is_file():
        return send_from_directory(str(dist), path)
    # Client-side route — serve index.html
    if dist.exists() and (dist / "index.html").exists():
        return send_from_directory(str(dist), "index.html")
    return Response("Not found", status=404)
```

**Step 3: Remove DASHBOARD_HTML**

Delete the entire DASHBOARD_HTML variable (approximately lines 908-2100). This removes ~1,200 lines of embedded HTML/CSS/JS.

**Step 4: Run existing tests to verify nothing broke**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper"
python -m pytest tests/test_review_server.py -v
```

Expected: All existing tests pass. The tests use `app.test_client()` in LOCAL_MODE, so they test `/api/*` routes which are unchanged.

**Step 5: Commit**

```bash
git add execution/review_server.py
git commit -m "refactor: replace embedded HTML with SPA serving, remove 1200-line DASHBOARD_HTML"
```

---

### Task 7: Build SPA and Verify End-to-End

**Step 1: Build the production bundle**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper/dashboard"
npm run build
```

Expected: `dashboard/dist/` created with `index.html`, `assets/*.js`, `assets/*.css`

**Step 2: Start Flask and verify SPA serves**

Run:
```bash
cd "/Users/anarchistsid/GenLab/Content Scraper"
venv/bin/python execution/review_server.py --port 5151
```

Open http://localhost:5151 — should see the React dashboard with sidebar.
Open http://localhost:5151/blueprints — should see the Blueprints placeholder (client-side routing via catch-all).
Open http://localhost:5151/api/health — should return JSON health data (API routes unaffected).
Open http://localhost:5151/api/blueprints — should return blueprint JSON (existing API still works).

**Step 3: Commit if everything works**

```bash
git add -A
git commit -m "feat: verify Flask serves React SPA + API routes coexist"
```

---

### Task 8: Add CORS Configuration for Tunnel Domain

**Files:**
- Modify: `execution/review_server.py`

**Step 1: Update Socket.IO CORS origins**

Find the socketio initialization (around line 60) and add the tunnel domain:

```python
socketio = SocketIO(
    app,
    cors_allowed_origins=[
        f"http://localhost:{_DEFAULT_PORT}",
        f"http://127.0.0.1:{_DEFAULT_PORT}",
        "https://review.aspirehub.ai",
    ],
    async_mode="threading",
)
```

**Step 2: Add CSP headers**

Add after the auth enforcement:

```python
@app.after_request
def _add_security_headers(response):
    """Add security headers to all responses."""
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    # CSP: allow self + inline styles (Tailwind) + wss for Socket.IO
    csp = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "media-src 'self' blob:; "
        "connect-src 'self' wss://review.aspirehub.ai ws://localhost:*; "
        "font-src 'self'; "
    )
    response.headers["Content-Security-Policy"] = csp
    return response
```

**Step 3: Run tests**

Run:
```bash
python -m pytest tests/test_review_server.py -v
```
Expected: All pass.

**Step 4: Commit**

```bash
git add execution/review_server.py
git commit -m "feat: add CORS for tunnel domain + CSP security headers"
```

---

## Phase 2: API Layer (Tasks 9-16)

Expand Flask from 12 routes to 28+ endpoints. Each task adds one resource group.

### Task 9: API Blueprint Structure + Versioned Routing

**Files:**
- Create: `execution/api/__init__.py`
- Create: `execution/api/blueprints.py`
- Create: `execution/api/stories.py`
- Create: `execution/api/schedule.py`
- Create: `execution/api/analytics.py`
- Create: `execution/api/pipeline.py`
- Create: `execution/api/config_routes.py`
- Create: `execution/api/ai.py`
- Create: `execution/api/notifications.py`
- Modify: `execution/review_server.py`
- Test: `tests/test_api_blueprints.py`

**Step 1: Create API module structure**

Create `execution/api/__init__.py`:

```python
"""API v1 route modules for the operations dashboard."""
```

**Step 2: Create blueprints API module**

Create `execution/api/blueprints.py`:

```python
"""Blueprint resource API endpoints."""
import logging
from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint("blueprints_api", __name__, url_prefix="/api/v1/blueprints")


def _get_client():
    """Lazy import to avoid circular imports."""
    from execution.utils.backlog_client import BacklogClient
    return BacklogClient()


@bp.route("", methods=["GET"])
def list_blueprints():
    """List blueprints with filtering, sorting, and pagination."""
    client = _get_client()

    # Parse query params
    status = request.args.get("status")
    platform = request.args.get("platform")
    template = request.args.get("template")
    search = request.args.get("search")
    sort = request.args.get("sort", "-scheduled_for")
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 25)), 100)

    # Build Microsoft Lists filter formula
    filters = []
    if status:
        filters.append(f"{{status}}='{status}'")
    else:
        # Default: show active statuses
        filters.append("OR({status}='INTEL_READY',{status}='DRAFTED',{status}='VISUAL_READY',{status}='PUBLISHED')")

    formula = "AND(" + ",".join(filters) + ")" if filters else ""

    try:
        all_records = client.blueprints.all(formula=formula) if formula else client.blueprints.all()
    except Exception as e:
        logger.error("Failed to fetch blueprints: %s", e)
        return jsonify({"error": "Failed to fetch blueprints", "detail": str(e)}), 502

    # Client-side search filter (Microsoft Lists doesn't support LIKE)
    if search:
        search_lower = search.lower()
        all_records = [
            r for r in all_records
            if search_lower in (r.get("fields", {}).get("hook_text", "") or "").lower()
            or search_lower in (r.get("fields", {}).get("caption", "") or "").lower()
        ]

    # Sort
    desc = sort.startswith("-")
    sort_field = sort.lstrip("-")
    all_records.sort(
        key=lambda r: r.get("fields", {}).get(sort_field, "") or "",
        reverse=desc,
    )

    # Paginate
    total = len(all_records)
    start = (page - 1) * per_page
    end = start + per_page
    page_records = all_records[start:end]

    return jsonify({
        "data": [{"id": r["id"], **r.get("fields", {})} for r in page_records],
        "meta": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page,
        },
    })


@bp.route("/<record_id>", methods=["GET"])
def get_blueprint(record_id):
    """Get full blueprint detail."""
    import re
    if not re.match(r'^rec[a-zA-Z0-9]{14}$', record_id):
        return jsonify({"error": "Invalid record ID format"}), 400

    client = _get_client()
    try:
        record = client.blueprints.get(record_id)
    except Exception as e:
        return jsonify({"error": "Blueprint not found", "detail": str(e)}), 404

    return jsonify({"data": {"id": record["id"], **record.get("fields", {})}})


@bp.route("/<record_id>/review", methods=["POST"])
def review_blueprint(record_id):
    """Approve/reject/revise a blueprint."""
    import re
    if not re.match(r'^rec[a-zA-Z0-9]{14}$', record_id):
        return jsonify({"error": "Invalid record ID format"}), 400

    data = request.json or {}
    action = data.get("action", "")
    if action not in ("approve", "reject", "revise", "skip"):
        return jsonify({"error": f"Invalid action: {action}"}), 400

    # Delegate to the existing review logic in review_server.py
    # This will be wired up when we integrate with the existing server
    # For now, return the action acknowledgment
    return jsonify({"status": "ok", "action": action, "record_id": record_id})


@bp.route("/batch-review", methods=["POST"])
def batch_review():
    """Batch approve/reject multiple blueprints."""
    data = request.json or {}
    ids = data.get("ids", [])
    action = data.get("action", "")

    if not ids:
        return jsonify({"error": "No blueprint IDs provided"}), 400
    if action not in ("approve", "reject"):
        return jsonify({"error": f"Invalid action: {action}"}), 400

    results = []
    for record_id in ids:
        results.append({"id": record_id, "action": action, "status": "ok"})

    return jsonify({"data": results})


@bp.route("/<record_id>/schedule", methods=["PATCH"])
def reschedule_blueprint(record_id):
    """Reschedule a blueprint."""
    import re
    if not re.match(r'^rec[a-zA-Z0-9]{14}$', record_id):
        return jsonify({"error": "Invalid record ID format"}), 400

    data = request.json or {}
    scheduled_for = data.get("scheduled_for")
    if not scheduled_for:
        return jsonify({"error": "scheduled_for is required"}), 400

    client = _get_client()
    try:
        client.blueprints.update(record_id, {"scheduled_for": scheduled_for})
    except Exception as e:
        return jsonify({"error": "Failed to reschedule", "detail": str(e)}), 500

    return jsonify({"status": "ok", "scheduled_for": scheduled_for})


@bp.route("/<record_id>/content", methods=["PATCH"])
def update_content(record_id):
    """Inline edit blueprint content. Creates a version."""
    import re
    if not re.match(r'^rec[a-zA-Z0-9]{14}$', record_id):
        return jsonify({"error": "Invalid record ID format"}), 400

    data = request.json or {}
    allowed_fields = {"hook_text", "caption", "hashtags"}
    updates = {k: v for k, v in data.items() if k in allowed_fields}

    if not updates:
        return jsonify({"error": "No valid fields to update"}), 400

    client = _get_client()
    try:
        client.blueprints.update(record_id, updates)
    except Exception as e:
        return jsonify({"error": "Failed to update content", "detail": str(e)}), 500

    return jsonify({"status": "ok", "updated_fields": list(updates.keys())})
```

**Step 3: Register the blueprint in review_server.py**

Add to review_server.py (after app creation, before routes):

```python
# Register API v1 modules
from execution.api.blueprints import bp as blueprints_bp
app.register_blueprint(blueprints_bp)
```

**Step 4: Write tests**

Create `tests/test_api_blueprints.py`:

```python
"""Tests for /api/v1/blueprints endpoints."""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from execution.review_server import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    app.config["DRY_RUN"] = True
    app.config["LOCAL_MODE"] = True
    with app.test_client() as c:
        yield c


def _csrf_headers(client):
    resp = client.get("/api/csrf-token")
    token = resp.get_json()["csrf_token"]
    return {"X-CSRF-Token": token, "Content-Type": "application/json"}


class TestBlueprintsAPI:
    def test_list_blueprints_returns_paginated(self, client):
        resp = client.get("/api/v1/blueprints")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "data" in data
        assert "meta" in data
        assert "page" in data["meta"]
        assert "total" in data["meta"]

    def test_get_blueprint_invalid_id(self, client):
        resp = client.get("/api/v1/blueprints/invalid_id")
        assert resp.status_code == 400

    def test_review_requires_valid_action(self, client):
        headers = _csrf_headers(client)
        resp = client.post(
            "/api/v1/blueprints/recABCDEFGHIJKLMN/review",
            json={"action": "invalid"},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_review_accepts_valid_actions(self, client):
        headers = _csrf_headers(client)
        for action in ("approve", "reject", "revise", "skip"):
            resp = client.post(
                "/api/v1/blueprints/recABCDEFGHIJKLMN/review",
                json={"action": action},
                headers=headers,
            )
            assert resp.status_code == 200

    def test_batch_review_requires_ids(self, client):
        headers = _csrf_headers(client)
        resp = client.post(
            "/api/v1/blueprints/batch-review",
            json={"action": "approve", "ids": []},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_reschedule_requires_scheduled_for(self, client):
        headers = _csrf_headers(client)
        resp = client.patch(
            "/api/v1/blueprints/recABCDEFGHIJKLMN/schedule",
            json={},
            headers=headers,
        )
        assert resp.status_code == 400

    def test_update_content_rejects_invalid_fields(self, client):
        headers = _csrf_headers(client)
        resp = client.patch(
            "/api/v1/blueprints/recABCDEFGHIJKLMN/content",
            json={"status": "PUBLISHED"},  # Not an allowed field
            headers=headers,
        )
        assert resp.status_code == 400
```

**Step 5: Run tests**

Run:
```bash
python -m pytest tests/test_api_blueprints.py -v
```
Expected: All pass.

**Step 6: Commit**

```bash
git add execution/api/ tests/test_api_blueprints.py execution/review_server.py
git commit -m "feat: add /api/v1/blueprints endpoints with filtering, pagination, review actions"
```

---

### Task 10: Stories API

**Files:**
- Create: `execution/api/stories.py`
- Modify: `execution/review_server.py` (register blueprint)
- Test: `tests/test_api_stories.py`

Follow the same pattern as Task 9. Implement `GET /api/v1/stories` (list with filters) and `GET /api/v1/stories/:id` (detail with claims and linked blueprints).

---

### Task 11: Schedule API

**Files:**
- Create: `execution/api/schedule.py`
- Modify: `execution/review_server.py`
- Test: `tests/test_api_schedule.py`

Implement:
- `GET /api/v1/schedule?from=&to=` — date range query, returns days with slots
- `PATCH /api/v1/schedule/reorder` — move blueprint between slots
- `GET /api/v1/schedule/coverage` — slot fill rates

The schedule data is derived from blueprints' `scheduled_for` field. Query blueprints where `scheduled_for` falls in the date range, group by date and time slot.

---

### Task 12: Analytics API

**Files:**
- Create: `execution/api/analytics.py`
- Modify: `execution/review_server.py`
- Test: `tests/test_api_analytics.py`

Implement:
- `GET /api/v1/analytics/publishing` — per-platform success/failure from Publishing_Analytics table
- `GET /api/v1/analytics/content` — template performance from Blueprints table (group by template, count by status)
- `GET /api/v1/analytics/pipeline` — run data from `.tmp/runs/*/run_report.json` files
- `GET /api/v1/analytics/heatmap` — performance by hour × day-of-week

---

### Task 13: Pipeline API

**Files:**
- Create: `execution/api/pipeline.py`
- Modify: `execution/review_server.py`
- Test: `tests/test_api_pipeline.py`

Implement:
- `GET /api/v1/pipeline/status` — last run, health, express state
- `GET /api/v1/pipeline/runs` — list run reports from `.tmp/runs/`
- `GET /api/v1/pipeline/runs/:id` — specific run report JSON
- `POST /api/v1/pipeline/trigger` — trigger express (migrate from existing `/api/express/trigger`)

---

### Task 14: Config API

**Files:**
- Create: `execution/api/config_routes.py`
- Modify: `execution/review_server.py`
- Test: `tests/test_api_config.py`

Implement read-only endpoints:
- `GET /api/v1/config/sources` — load and return `config/sources.yaml`
- `GET /api/v1/config/templates` — fetch from Microsoft Lists Templates table
- `GET /api/v1/config/schedule-slots` — load from `config/publishing.yaml`
- `GET /api/v1/config/scoring` — load from `config/scoring_weights.yaml`

---

### Task 15: AI Command API

**Files:**
- Create: `execution/api/ai.py`
- Modify: `execution/review_server.py`
- Modify: `requirements.txt` (add openai if not present)
- Test: `tests/test_api_ai.py`

Implement:
- `POST /api/v1/ai/command` — send natural language query to OpenAI with function definitions mapping to API endpoints. Return preview of action.
- `POST /api/v1/ai/command/execute` — execute a confirmed command
- `GET /api/v1/ai/suggestions/:blueprint_id` — content improvement suggestions
- `GET /api/v1/ai/auto-approve-score/:blueprint_id` — confidence score computation

The function definitions for OpenAI:

```python
AI_FUNCTIONS = [
    {
        "name": "list_blueprints",
        "description": "Search and filter blueprints",
        "parameters": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["INTEL_READY", "DRAFTED", "VISUAL_READY", "PUBLISHED"]},
                "date_range": {"type": "string", "description": "e.g. 'today', 'this week', 'last 7 days'"},
            },
        },
    },
    {
        "name": "batch_review",
        "description": "Approve or reject multiple blueprints",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["approve", "reject"]},
                "filter_status": {"type": "string"},
                "filter_date": {"type": "string"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "reschedule",
        "description": "Move a blueprint to a different schedule slot",
        "parameters": {
            "type": "object",
            "properties": {
                "blueprint_id": {"type": "string"},
                "new_time": {"type": "string"},
            },
            "required": ["blueprint_id", "new_time"],
        },
    },
    {
        "name": "get_failures",
        "description": "Get recent publish failures",
        "parameters": {
            "type": "object",
            "properties": {
                "platform": {"type": "string"},
                "date_range": {"type": "string"},
            },
        },
    },
    {
        "name": "trigger_pipeline",
        "description": "Trigger the express pipeline",
        "parameters": {"type": "object", "properties": {}},
    },
]
```

---

### Task 16: Socket.IO Event Expansion + TypeScript Types

**Files:**
- Modify: `execution/review_server.py` — add new Socket.IO events
- Create: `dashboard/src/api/types.ts` — TypeScript types for API responses
- Create: `dashboard/src/api/client.ts` — typed fetch wrapper
- Create: `dashboard/src/api/socket.ts` — Socket.IO client with typed events

**Step 1: Create TypeScript API types**

Create `dashboard/src/api/types.ts`:

```typescript
// API response wrapper
export interface PaginatedResponse<T> {
  data: T[];
  meta: {
    page: number;
    per_page: number;
    total: number;
    total_pages: number;
  };
}

export interface SingleResponse<T> {
  data: T;
}

// Entities
export interface Blueprint {
  id: string;
  candidate_id: string;
  status: "INTEL_READY" | "DRAFTED" | "VISUAL_READY" | "SCHEDULED" | "PUBLISHED" | "ERROR" | "NEEDS_REVIEW";
  hook_text: string;
  caption: string;
  hashtags: string;
  template_id: string;
  story_id: string;
  scheduled_for: string | null;
  visual_paths: string | null;
  slide_previews: Array<{ url: string }> | null;
  platform_publish_status: Record<string, string> | null;
  youtube_content: Record<string, any> | null;
  twitter_content: Record<string, any> | null;
  priority_score: number;
  action_taken: string | null;
  feedback_issue: string | null;
  feedback_notes: string | null;
  reviewed_at: string | null;
  created_at: string;
}

export interface Story {
  id: string;
  story_id: string;
  title: string;
  source: string;
  url: string;
  published_date: string;
  score: number;
  cluster_id: string | null;
  summary: string;
}

export interface ScheduleSlot {
  time: string;
  blueprint: Blueprint | null;
  status: "published" | "scheduled" | "empty";
}

export interface ScheduleDay {
  date: string;
  slots: ScheduleSlot[];
  coverage: number; // 0-1
}

export interface PipelineRun {
  run_id: string;
  date: string;
  duration_seconds: number;
  steps_completed: number;
  total_steps: number;
  errors: number;
  cost_estimate: number;
}

export interface Notification {
  id: string;
  type: "publish_success" | "publish_failure" | "pipeline_complete" | "pipeline_error" | "review_needed" | "token_expiry";
  title: string;
  body: string;
  read: boolean;
  created_at: string;
  entity_id?: string;
  entity_type?: string;
}

// Socket.IO event payloads
export interface BlueprintUpdatedEvent {
  id: string;
  status: string;
  platform_publish_status?: Record<string, string>;
  updated_fields?: string[];
}

export interface PipelineProgressEvent {
  run_id: string;
  step: string;
  step_index: number;
  total_steps: number;
  status: "started" | "running" | "complete" | "failed";
  message: string;
}

export interface PublishResultEvent {
  blueprint_id: string;
  platform: string;
  success: boolean;
  error?: string;
  url?: string;
}
```

**Step 2: Create typed API client**

Create `dashboard/src/api/client.ts`:

```typescript
import type {
  PaginatedResponse,
  SingleResponse,
  Blueprint,
  Story,
  ScheduleDay,
  PipelineRun,
  Notification,
} from "./types";

const BASE = "/api/v1";

let csrfToken: string | null = null;

async function getCsrfToken(): Promise<string> {
  if (csrfToken) return csrfToken;
  const resp = await fetch("/api/csrf-token");
  const data = await resp.json();
  csrfToken = data.csrf_token;
  return csrfToken!;
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${BASE}${path}`, init);
  if (!resp.ok) {
    const error = await resp.json().catch(() => ({ error: resp.statusText }));
    throw new Error(error.error || resp.statusText);
  }
  return resp.json();
}

async function mutate<T>(method: string, path: string, body?: unknown): Promise<T> {
  const token = await getCsrfToken();
  return fetchJson<T>(path, {
    method,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: body ? JSON.stringify(body) : undefined,
  });
}

// Blueprints
export const blueprints = {
  list: (params?: Record<string, string>) =>
    fetchJson<PaginatedResponse<Blueprint>>(
      `/blueprints?${new URLSearchParams(params).toString()}`
    ),
  get: (id: string) => fetchJson<SingleResponse<Blueprint>>(`/blueprints/${id}`),
  review: (id: string, body: { action: string; issue?: string; notes?: string }) =>
    mutate<{ status: string }>( "POST", `/blueprints/${id}/review`, body),
  batchReview: (body: { ids: string[]; action: string; notes?: string }) =>
    mutate<{ data: Array<{ id: string; status: string }> }>("POST", "/blueprints/batch-review", body),
  reschedule: (id: string, scheduledFor: string) =>
    mutate<{ status: string }>("PATCH", `/blueprints/${id}/schedule`, { scheduled_for: scheduledFor }),
  updateContent: (id: string, body: Partial<Pick<Blueprint, "hook_text" | "caption" | "hashtags">>) =>
    mutate<{ status: string }>("PATCH", `/blueprints/${id}/content`, body),
};

// Stories
export const stories = {
  list: (params?: Record<string, string>) =>
    fetchJson<PaginatedResponse<Story>>(`/stories?${new URLSearchParams(params).toString()}`),
  get: (id: string) => fetchJson<SingleResponse<Story>>(`/stories/${id}`),
};

// Schedule
export const schedule = {
  get: (from: string, to: string) =>
    fetchJson<{ data: ScheduleDay[] }>(`/schedule?from=${from}&to=${to}`),
  reorder: (body: { blueprint_id: string; from_slot: string; to_slot: string }) =>
    mutate<{ status: string }>("PATCH", "/schedule/reorder", body),
};

// Analytics
export const analytics = {
  publishing: (params?: Record<string, string>) =>
    fetchJson<{ data: any }>(`/analytics/publishing?${new URLSearchParams(params).toString()}`),
  content: (params?: Record<string, string>) =>
    fetchJson<{ data: any }>(`/analytics/content?${new URLSearchParams(params).toString()}`),
  pipeline: (params?: Record<string, string>) =>
    fetchJson<{ data: any }>(`/analytics/pipeline?${new URLSearchParams(params).toString()}`),
  heatmap: () => fetchJson<{ data: any }>("/analytics/heatmap"),
};

// Pipeline
export const pipeline = {
  status: () => fetchJson<{ data: any }>("/pipeline/status"),
  runs: (params?: Record<string, string>) =>
    fetchJson<PaginatedResponse<PipelineRun>>(`/pipeline/runs?${new URLSearchParams(params).toString()}`),
  run: (id: string) => fetchJson<{ data: any }>(`/pipeline/runs/${id}`),
  trigger: () => mutate<{ status: string }>("POST", "/pipeline/trigger", {}),
};

// AI
export const ai = {
  command: (query: string) =>
    mutate<{ action: string; preview: string; params: any }>("POST", "/ai/command", { query }),
  execute: (body: { action: string; params: any; confirmed: boolean }) =>
    mutate<{ status: string; result: any }>("POST", "/ai/command/execute", body),
  suggestions: (blueprintId: string) =>
    fetchJson<{ data: any }>(`/ai/suggestions/${blueprintId}`),
  autoApproveScore: (blueprintId: string) =>
    fetchJson<{ data: { score: number; breakdown: any } }>(`/ai/auto-approve-score/${blueprintId}`),
};

// Config
export const config = {
  sources: () => fetchJson<{ data: any }>("/config/sources"),
  templates: () => fetchJson<{ data: any }>("/config/templates"),
  scheduleSlots: () => fetchJson<{ data: any }>("/config/schedule-slots"),
  scoring: () => fetchJson<{ data: any }>("/config/scoring"),
};

// Notifications
export const notifications = {
  list: (params?: Record<string, string>) =>
    fetchJson<PaginatedResponse<Notification>>(`/notifications?${new URLSearchParams(params).toString()}`),
  markRead: (ids: string[]) => mutate<{ status: string }>("PATCH", "/notifications/read", { ids }),
  markAllRead: () => mutate<{ status: string }>("PATCH", "/notifications/read", { all: true }),
};

// Health
export const health = () => fetchJson<any>("/health");
```

**Step 3: Create Socket.IO client**

Create `dashboard/src/api/socket.ts`:

```typescript
import { io, Socket } from "socket.io-client";
import type { BlueprintUpdatedEvent, PipelineProgressEvent, PublishResultEvent } from "./types";

let socket: Socket | null = null;

export function getSocket(): Socket {
  if (!socket) {
    socket = io({
      transports: ["websocket"],
      reconnection: true,
      reconnectionDelay: 1000,
      reconnectionAttempts: 10,
    });
  }
  return socket;
}

export type SocketEvents = {
  "blueprint:updated": BlueprintUpdatedEvent;
  "blueprint:published": PublishResultEvent;
  "pipeline:started": { run_id: string; triggered_by: string };
  "pipeline:progress": PipelineProgressEvent;
  "pipeline:complete": { run_id: string; summary: any; duration: number; errors: number };
  "notification:new": { id: string; type: string; title: string; body: string; created_at: string };
  "schedule:changed": { date: string; slot: string; blueprint_id: string; action: string };
};
```

**Step 4: Commit**

```bash
git add dashboard/src/api/ execution/api/ execution/review_server.py
git commit -m "feat: API v1 module structure + TypeScript client + Socket.IO types"
```

---

## Phase 3: Core Views (Tasks 17-26)

Build the actual dashboard views. Each task builds one view or major component.

### Task 17: TanStack Query Hooks

**Files:**
- Create: `dashboard/src/hooks/use-blueprints.ts`
- Create: `dashboard/src/hooks/use-stories.ts`
- Create: `dashboard/src/hooks/use-schedule.ts`
- Create: `dashboard/src/hooks/use-analytics.ts`
- Create: `dashboard/src/hooks/use-pipeline.ts`
- Create: `dashboard/src/hooks/use-socket.ts`

Each hook wraps the API client with TanStack Query for caching, background refetch, and optimistic updates.

Example `use-blueprints.ts`:

```typescript
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { blueprints as api } from "@/api/client";
import { toast } from "sonner";

export function useBlueprints(params?: Record<string, string>) {
  return useQuery({
    queryKey: ["blueprints", params],
    queryFn: () => api.list(params),
  });
}

export function useBlueprint(id: string) {
  return useQuery({
    queryKey: ["blueprints", id],
    queryFn: () => api.get(id),
    enabled: !!id,
  });
}

export function useReviewBlueprint() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; action: string; issue?: string; notes?: string }) =>
      api.review(id, body),
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ["blueprints"] });
      toast.success(`Blueprint ${variables.action}d`, {
        action: { label: "Undo", onClick: () => {/* undo logic */} },
        duration: 5000,
      });
    },
  });
}
```

Build similar hooks for each resource. The Socket.IO hook subscribes to events and updates TanStack Query cache:

```typescript
export function useSocketUpdates() {
  const queryClient = useQueryClient();
  useEffect(() => {
    const socket = getSocket();
    socket.on("blueprint:updated", (data) => {
      queryClient.invalidateQueries({ queryKey: ["blueprints"] });
    });
    socket.on("pipeline:progress", (data) => {
      queryClient.setQueryData(["pipeline", "status"], (old: any) => ({
        ...old,
        current_step: data.step,
      }));
    });
    return () => { socket.off("blueprint:updated"); socket.off("pipeline:progress"); };
  }, [queryClient]);
}
```

---

### Task 18: Shared Components

**Files:**
- Create: `dashboard/src/components/shared/status-badge.tsx`
- Create: `dashboard/src/components/shared/video-player.tsx`
- Create: `dashboard/src/components/shared/filter-bar.tsx`
- Create: `dashboard/src/components/shared/data-table.tsx`
- Create: `dashboard/src/components/shared/empty-state.tsx`

Build reusable components:
- **StatusBadge:** Maps blueprint status to colored badge (success/warning/error/info)
- **VideoPlayer:** `<video>` wrapper with loop, seek, volume, poster frame
- **FilterBar:** Generic faceted filter using nuqs for URL state persistence
- **DataTable:** Sortable table built on TanStack Table (optional) or simple `<table>` with sort icons
- **EmptyState:** Illustrated empty state with icon + message + optional CTA

---

### Task 19: Pipeline Overview View

**Files:**
- Modify: `dashboard/src/views/pipeline.tsx`
- Create: `dashboard/src/components/charts/kpi-card.tsx`

Build the home view with:
1. KPI row (4 cards with counts + sparklines using Recharts)
2. Today's schedule (4 slots with status)
3. Platform health bars (4 horizontal bars)
4. Needs attention alerts
5. Recent activity feed

Use `useBlueprints`, `useSchedule`, `usePipeline` hooks to fetch data.

---

### Task 20: Content Board — Card Grid

**Files:**
- Modify: `dashboard/src/views/blueprints.tsx`
- Create: `dashboard/src/components/blueprints/blueprint-card.tsx`
- Create: `dashboard/src/components/blueprints/review-actions.tsx`
- Create: `dashboard/src/stores/selection-store.ts`

Build:
1. FilterBar at top (status pills, search, sort) — filters in URL via nuqs
2. Responsive card grid (CSS grid: 3/2/1 columns)
3. BlueprintCard component (thumbnail, hook, schedule, status, quick actions)
4. Batch selection via Zustand store
5. Virtual scrolling via TanStack Virtual for 100+ cards

---

### Task 21: Content Board — Detail Panel

**Files:**
- Create: `dashboard/src/components/blueprints/blueprint-detail.tsx`
- Create: `dashboard/src/components/blueprints/platform-preview.tsx`
- Create: `dashboard/src/components/shared/carousel-viewer.tsx`

Build the split-pane detail panel:
1. Slides in from right on card click (Framer Motion)
2. Video player + carousel viewer
3. Full content display (hook, caption, hashtags)
4. Platform preview tabs (IG/YT/TW/FB)
5. Review action buttons with feedback form
6. Metadata grid (template, story, score, schedule)

---

### Task 22: Keyboard Shortcuts

**Files:**
- Create: `dashboard/src/hooks/use-keyboard.ts`
- Create: `dashboard/src/components/layout/keyboard-help.tsx`

Implement global keyboard handler:
1. `j/k` navigate card list
2. `a/r/v/s` review actions
3. `Enter/Escape` open/close detail
4. `Space` toggle select
5. `Cmd+K` command palette
6. `Cmd+/` keyboard help overlay
7. `g+p/b/s/a/t/r` view navigation (two-key combo with timeout)

Use `useEffect` with `document.addEventListener("keydown")`. Track combo state (g pressed → wait for second key).

---

### Task 23: Command Palette

**Files:**
- Create: `dashboard/src/components/layout/command-palette.tsx`
- Create: `dashboard/src/hooks/use-ai-command.ts`
- Create: `dashboard/src/lib/ai-actions.ts`

Build with cmdk:
1. Fuzzy search through nav items, blueprint titles, story titles
2. Quick actions (approve all, trigger pipeline, etc.)
3. AI natural language input: if no fuzzy match → call `/api/v1/ai/command`
4. Preview panel: show action preview, [Confirm] / [Cancel]
5. Inline results for read-only queries

---

### Task 24: Schedule Board (Week View)

**Files:**
- Modify: `dashboard/src/views/schedule.tsx`
- Create: `dashboard/src/components/schedule/schedule-board.tsx`
- Create: `dashboard/src/components/schedule/time-slot.tsx`
- Create: `dashboard/src/components/schedule/drag-card.tsx`

Build with dnd-kit:
1. 7-day horizontal grid × 4 time slots
2. Droppable time slots
3. Draggable blueprint cards
4. Unscheduled pool at bottom
5. Coverage indicators per day
6. Conflict detection (red highlight)

---

### Task 25: Month Calendar View

**Files:**
- Create: `dashboard/src/components/schedule/calendar-month.tsx`

Build:
1. Standard month grid
2. Slot fill dots per day (●/○)
3. Color coding by status
4. Click day → expand inline showing 4 slots
5. Toggle between Week Board and Month Calendar

---

### Task 26: Focus Review Mode

**Files:**
- Modify: `dashboard/src/views/focus-review.tsx`
- Create: `dashboard/src/components/review/focus-mode.tsx`
- Create: `dashboard/src/components/review/progress-bar.tsx`
- Create: `dashboard/src/components/review/auto-approve-timer.tsx`
- Create: `dashboard/src/hooks/use-focus-mode.ts`

Build:
1. Full-screen layout (no sidebar, no header)
2. Large centered video/carousel
3. Content display below
4. Action buttons with keyboard shortcuts
5. Progress bar at bottom
6. Auto-advance after action (with 5s undo toast)
7. Auto-approve countdown bar for high-confidence cards
8. Mobile: swipe gestures (right=approve, left=reject)
9. Summary screen on completion

---

## Phase 4: Analytics & Advanced Features (Tasks 27-34)

### Task 27: Analytics View — Charts

**Files:**
- Modify: `dashboard/src/views/analytics.tsx`
- Create: `dashboard/src/components/charts/platform-chart.tsx`
- Create: `dashboard/src/components/charts/heatmap.tsx`
- Create: `dashboard/src/components/charts/cost-tracker.tsx`
- Create: `dashboard/src/components/charts/template-ranking.tsx`

Build with Recharts:
1. Date range selector at top
2. Publishing success rate line chart (per-platform)
3. Performance heatmap (hour × day-of-week)
4. Template ranking bar chart
5. Cost tracker sparkline with budget threshold
6. Error frequency donut chart

---

### Task 28: Stories Explorer View

**Files:**
- Modify: `dashboard/src/views/stories.tsx`

Build:
1. Sortable data table (title, source, score, cluster, blueprints, date)
2. Faceted filters (source, score range, date)
3. Search
4. Row expansion (full story text, claims, linked blueprints)

---

### Task 29: Pipeline Runs View

**Files:**
- Modify: `dashboard/src/views/runs.tsx`

Build:
1. Runs table (ID, date, duration, steps, errors, cost)
2. Click → detail view with step-by-step breakdown
3. Duration bars per step
4. Error highlighting

---

### Task 30: Settings View

**Files:**
- Modify: `dashboard/src/views/settings.tsx`

Build:
1. Tabs: Sources, Scoring, Schedule, Templates, Notifications, Auto-Approve, System
2. Read-only config display for Sources/Scoring/Schedule/Templates
3. Notification preferences form (Slack webhook URL, email digest frequency)
4. Auto-approve threshold slider
5. System health indicators

---

### Task 31: Notification System

**Files:**
- Create: `dashboard/src/components/layout/notification-center.tsx`
- Create: `dashboard/src/stores/notification-store.ts`
- Create: `dashboard/src/hooks/use-notifications.ts`
- Create: `execution/api/notifications.py`
- Create: `execution/notifications.py` (notification manager)

Build:
1. Bell icon in Shell header with unread count badge
2. Dropdown panel: notification list, grouped by type
3. Click notification → navigate to entity
4. Mark read / mark all read
5. Server-side: SQLite notifications table in `.tmp/notifications.db`
6. Socket.IO push for new notifications
7. Webhook integration (Slack, generic URL)

---

### Task 32: Inline Editing

**Files:**
- Create: `dashboard/src/components/blueprints/inline-editor.tsx`

Build:
1. Double-click hook text → inline input
2. Double-click caption → expandable textarea
3. Double-click schedule → date/time picker
4. Auto-save with 500ms debounce
5. Optimistic update via TanStack Query mutation
6. Undo with Cmd+Z

---

### Task 33: Comparison Mode

**Files:**
- Create: `dashboard/src/components/blueprints/comparison-view.tsx`

Build:
1. Select 2 blueprints → "Compare" button in batch bar
2. Two-column layout: video/content side by side
3. Diff table highlighting differences
4. Action: approve A / approve B / both

---

### Task 34: Content Versioning

**Files:**
- Create: `dashboard/src/components/blueprints/version-diff.tsx`
- Modify: `execution/api/blueprints.py` — add version history endpoint

Build:
1. Version list with timestamps and change source
2. Side-by-side diff viewer (red/green highlighting)
3. "Revert to this version" button

---

## Phase 5: Performance & Polish (Tasks 35-40)

### Task 35: Activity Feed

**Files:**
- Create: `dashboard/src/components/layout/activity-feed.tsx`

Build:
1. Collapsible right panel in Shell
2. Live event stream from Socket.IO
3. Event type icons + timestamps
4. Click → navigate to entity
5. Filter by event type

---

### Task 36: PWA Setup

**Files:**
- Create: `dashboard/public/manifest.json`
- Create: `dashboard/public/sw.js`
- Create: `dashboard/public/icons/icon-192.png`
- Create: `dashboard/public/icons/icon-512.png`
- Create: `dashboard/src/hooks/use-offline.ts`

Build:
1. PWA manifest with app name, colors, icons
2. Service worker: cache shell (cache-first), API (network-first with fallback)
3. IndexedDB: cache last 100 blueprints + 50 stories
4. Offline review queue: store actions in IndexedDB, sync on reconnect
5. Online/offline detection hook
6. Offline banner component

---

### Task 37: Web Workers

**Files:**
- Create: `dashboard/src/workers/search-indexer.worker.ts`
- Create: `dashboard/src/workers/filter-engine.worker.ts`

Build:
1. Search indexer: build Fuse.js index from blueprint/story data, update incrementally
2. Filter engine: heavy filtering/sorting off main thread
3. Integration with command palette (instant search results)

---

### Task 38: Predictive Scheduling

**Files:**
- Modify: `execution/api/schedule.py` — add suggestions endpoint
- Create: `dashboard/src/components/schedule/smart-suggestion.tsx`

Build:
1. Server: compute slot performance scores from Publishing_Analytics
2. API: `GET /api/v1/schedule/suggestions`
3. UI: suggestion banners on Schedule Board
4. Tooltip on drag showing slot performance score

---

### Task 39: Export & Reporting

**Files:**
- Create: `dashboard/src/lib/export.ts`
- Create: `execution/api/reports.py`

Build:
1. Client-side CSV export (from filtered table data)
2. Server-side PDF weekly report generation
3. Download buttons on table/chart views

---

### Task 40: Accessibility Audit

**Files:**
- Modify: multiple component files

Final pass:
1. ARIA labels on all interactive elements
2. Focus rings on keyboard navigation
3. Skip-to-content link
4. Screen reader announcements for toasts and status changes
5. Reduced motion mode
6. High contrast mode toggle in Settings
7. Minimum 44px touch targets on mobile

---

## Phase 6: Infrastructure (Tasks 41-44)

### Task 41: Gunicorn + launchd Setup

**Files:**
- Modify: `runbooks/com.genlab.review-server.plist`
- Create: `runbooks/review_server_wrapper.sh`
- Modify: `requirements.txt` — add gunicorn, eventlet

Build:
1. Install gunicorn + eventlet
2. Create wrapper script: `npm run build` → `gunicorn` start
3. Update launchd plist for Gunicorn (2 workers, eventlet, timeout 120)
4. Test: `launchctl load`, verify auto-restart

---

### Task 42: Cloudflare Tunnel + Access

**Files:**
- Modify: `~/.cloudflared/config.yml`

Build:
1. Add `review.aspirehub.ai` ingress rule to existing tunnel config
2. Configure Cloudflare Access policy (Google SSO)
3. Test: access from external device

---

### Task 43: Cloudflare Worker (Edge Cache)

**Files:**
- Create: `dashboard/worker/index.js` (Cloudflare Worker script)

Build:
1. Worker that caches GET API responses at edge
2. Cache rules: 60s for blueprints, 5min for analytics, 10s for health
3. POST/PATCH bypass + purge related keys
4. Deploy via Wrangler CLI

---

### Task 44: Final Integration Test

**Step 1: Run full Python test suite**
```bash
python -m pytest tests/ -v
```
Expected: All 848+ tests pass.

**Step 2: Build and verify dashboard**
```bash
cd dashboard && npm run build
```
Expected: Build succeeds, dist/ < 200KB gzipped.

**Step 3: Start server and verify all views**
```bash
venv/bin/python execution/review_server.py --port 5151
```

Manual verification checklist:
- [ ] http://localhost:5151 loads React dashboard
- [ ] Sidebar navigation works
- [ ] /api/v1/blueprints returns data
- [ ] Content Board shows blueprint cards
- [ ] Detail panel opens on card click
- [ ] Video playback works
- [ ] Keyboard shortcuts work
- [ ] Command palette opens with Cmd+K
- [ ] Schedule Board renders
- [ ] Analytics charts render
- [ ] Focus Review Mode works
- [ ] Mobile layout is responsive

**Step 4: Commit everything**
```bash
git add -A
git commit -m "feat: operations command center v1 complete — 9 views, 28 API endpoints, AI command bar"
```

---

## Dependency Graph

```
Phase 1 (Foundation) ─── must complete before all others
  │
  ├── Phase 2 (API Layer) ─── must complete before Phase 3
  │     │
  │     └── Phase 3 (Core Views) ─── most development work
  │           │
  │           ├── Phase 4 (Advanced Features) ─── can partially parallel with Phase 3
  │           │
  │           └── Phase 5 (Performance & Polish) ─── after core views work
  │
  └── Phase 6 (Infrastructure) ─── independent, can parallel with Phase 3+
```

## Estimated Effort

| Phase | Tasks | Estimated Time |
|-------|-------|---------------|
| Phase 1: Foundation | 8 | 2-3 hours |
| Phase 2: API Layer | 8 | 3-4 hours |
| Phase 3: Core Views | 10 | 8-12 hours |
| Phase 4: Advanced Features | 8 | 6-8 hours |
| Phase 5: Performance & Polish | 6 | 4-6 hours |
| Phase 6: Infrastructure | 4 | 2-3 hours |
| **Total** | **44** | **25-36 hours** |
