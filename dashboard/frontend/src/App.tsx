import { lazy, Suspense } from "react";
import { createBrowserRouter, RouterProvider, Outlet, useLocation } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { NuqsAdapter } from "nuqs/adapters/react-router/v7";
import { Toaster } from "sonner";
import { AnimatePresence, motion } from "framer-motion";
import { Shell } from "@/components/layout/shell";
import { KeyboardHelp } from "@/components/layout/keyboard-help";
import { CommandPalette } from "@/components/layout/command-palette";
import { useKeyboardShortcuts } from "@/hooks/use-keyboard";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBoundary } from "@/components/ui/error-boundary";

const MissionControlView = lazy(() => import("@/views/mission-control/MissionControl"));
const PipelineView = lazy(() => import("@/views/pipeline/PipelineMonitor"));
const BlueprintsView = lazy(() => import("@/views/blueprints"));
const ScheduleView = lazy(() => import("@/views/schedule"));
const AnalyticsView = lazy(() => import("@/views/analytics/Analytics"));
const StoriesView = lazy(() => import("@/views/stories"));
const RunsView = lazy(() => import("@/views/runs"));
const SettingsView = lazy(() => import("@/views/settings"));
const FocusReviewView = lazy(() => import("@/views/focus-review"));
const BulkReviewView = lazy(() => import("@/views/bulk-review"));
const MediaKitView = lazy(() => import("@/views/media-kit/MediaKit"));
const PublishingQueueView = lazy(() => import("@/views/publishing-queue/PublishingQueue"));
const ChannelHealthView = lazy(() => import("@/views/channel-health/ChannelHealth"));
const MonetisationView = lazy(() => import("@/views/monetisation/MonetisationProgress"));
const LearningView = lazy(() => import("@/views/learning/LearningView"));
const EngagementView = lazy(() => import("@/views/engagement/EngagementView"));
const ContentReviewView = lazy(() => import("@/views/content/ContentReviewView"));
const SystemHealthView = lazy(() => import("@/views/health/SystemHealthView"));

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // Socket.IO handles data freshness via event-based invalidation.
      // 5 min staleTime prevents unnecessary refetches on tab focus / mount.
      staleTime: 5 * 60_000,
      retry: 2,
    },
  },
});

function LoadingFallback() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-8 w-48" />
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
        <Skeleton className="h-24" />
      </div>
      <Skeleton className="h-64" />
    </div>
  );
}

/** Shared global overlays — must live inside the router context for useNavigate. */
function GlobalShortcuts() {
  useKeyboardShortcuts();
  return (
    <>
      <KeyboardHelp />
      <CommandPalette />
    </>
  );
}

function DashboardLayout() {
  const location = useLocation();
  return (
    <NuqsAdapter>
      <GlobalShortcuts />
      <Shell>
        <ErrorBoundary>
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 4 }}
              animate={{ opacity: 1, y: 0, transition: { duration: 0.12, ease: "easeOut" } }}
              exit={{ opacity: 0, transition: { duration: 0.08, ease: "easeIn" } }}
            >
              <Suspense fallback={<LoadingFallback />}>
                <Outlet />
              </Suspense>
            </motion.div>
          </AnimatePresence>
        </ErrorBoundary>
      </Shell>
    </NuqsAdapter>
  );
}

const router = createBrowserRouter([
  {
    element: <DashboardLayout />,
    children: [
      { path: "/", element: <MissionControlView /> },
      { path: "/pipeline", element: <PipelineView /> },
      { path: "/blueprints", element: <BlueprintsView /> },
      { path: "/blueprints/:id", element: <BlueprintsView /> },
      { path: "/schedule", element: <ScheduleView /> },
      { path: "/analytics", element: <AnalyticsView /> },
      { path: "/stories", element: <StoriesView /> },
      { path: "/runs", element: <RunsView /> },
      { path: "/runs/:id", element: <RunsView /> },
      { path: "/settings", element: <SettingsView /> },
      { path: "/queue", element: <PublishingQueueView /> },
      { path: "/channel-health", element: <ChannelHealthView /> },
      { path: "/monetisation", element: <MonetisationView /> },
      { path: "/learning", element: <LearningView /> },
      { path: "/engagement", element: <EngagementView /> },
      { path: "/content", element: <ContentReviewView /> },
      { path: "/health", element: <SystemHealthView /> },
      { path: "*", element: <div className="flex items-center justify-center h-[60vh] text-text-muted">Page not found</div> },
    ],
  },
  {
    path: "/focus-review",
    element: (
      <NuqsAdapter>
        <GlobalShortcuts />
        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            <FocusReviewView />
          </Suspense>
        </ErrorBoundary>
      </NuqsAdapter>
    ),
  },
  {
    // 2026-06-14: 5-at-a-time grid review for accelerating the
    // operator-review-marathon bottleneck. See components/review/bulk-review.tsx.
    path: "/bulk-review",
    element: (
      <NuqsAdapter>
        <GlobalShortcuts />
        <ErrorBoundary>
          <Suspense fallback={<LoadingFallback />}>
            <BulkReviewView />
          </Suspense>
        </ErrorBoundary>
      </NuqsAdapter>
    ),
  },
  {
    // PR U (2026-06-23): standalone print-friendly per-niche media kit.
    // No dashboard chrome — the route is the printable surface. Operator
    // navigates to /media-kit/<niche_id>, Cmd+P → Save as PDF.
    path: "/media-kit/:niche",
    element: (
      <ErrorBoundary>
        <Suspense fallback={<LoadingFallback />}>
          <MediaKitView />
        </Suspense>
      </ErrorBoundary>
    ),
  },
]);

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            color: "var(--text-primary)",
          },
        }}
      />
      <div id="sr-announcements" className="sr-only" aria-live="polite" aria-atomic="true" />
      <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-left" />
    </QueryClientProvider>
  );
}
