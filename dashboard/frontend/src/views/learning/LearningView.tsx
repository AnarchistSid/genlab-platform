import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { useLearningStatus } from "@/hooks/use-learning";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { LearningOverview } from "./LearningOverview";
import { BanditArms } from "./BanditArms";
import { RewardHistory } from "./RewardHistory";
import { HookClassifier } from "./HookClassifier";
import { ConfigUpdates } from "./ConfigUpdates";

// ── Tab definitions ────────────────────────────────────────

const TABS = [
  { value: "overview",         label: "Overview",         Component: LearningOverview },
  { value: "bandit-arms",      label: "Bandit Arms",      Component: BanditArms },
  { value: "rewards",          label: "Rewards",          Component: RewardHistory },
  { value: "hook-classifier",  label: "Hook Classifier",  Component: HookClassifier },
  { value: "config-updates",   label: "Config Updates",   Component: ConfigUpdates },
] as const;

// ── Main export ────────────────────────────────────────────

export default function LearningView() {
  const { isError, data, refetch } = useLearningStatus();

  // Error state — show banner when query failed and no cached data
  if (isError && !data) {
    return (
      <>
        <PageHeader
          title="Learning Intelligence"
          subtitle="How the system learns from engagement data to improve content strategy"
        />
        <ErrorState
          title="Unable to load learning data"
          onRetry={() => void refetch()}
        />
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Learning Intelligence"
        subtitle="How the system learns from engagement data to improve content strategy"
      />

      {/* Tabbed content */}
      <Tabs defaultValue="overview">
        <TabsList className="bg-bg-elevated border border-border mb-6">
          {TABS.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        {TABS.map(({ value, Component }) => (
          <TabsContent key={value} value={value}>
            <Component />
          </TabsContent>
        ))}
      </Tabs>
    </>
  );
}
