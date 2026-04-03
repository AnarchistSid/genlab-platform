import { useMemo } from "react";
import { Calendar, LayoutGrid } from "lucide-react";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScheduleBoard } from "@/components/schedule/schedule-board";
import { CalendarMonth } from "@/components/schedule/calendar-month";
import { PageHeader } from "@/components/shared/page-header";
import { ErrorState } from "@/components/shared/error-state";
import { useSocketUpdates } from "@/hooks/use-socket";
import { useSchedule } from "@/hooks/use-schedule";

function toISO(date: Date): string {
  const y = date.getFullYear();
  const m = String(date.getMonth() + 1).padStart(2, "0");
  const d = String(date.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

export default function ScheduleView() {
  useSocketUpdates();

  // Light-weight probe query to detect API errors at the view level
  const today = useMemo(() => new Date(), []);
  const probeFrom = toISO(today);
  const probeTo = toISO(today);
  const { isError, data, refetch } = useSchedule(probeFrom, probeTo);

  if (isError && !data) {
    return (
      <div className="space-y-6 px-1">
        <PageHeader
          title="Publishing Schedule"
          subtitle="Manage and preview upcoming posts across all 5 channels."
        />
        <ErrorState
          title="Unable to load schedule data"
          message="Could not connect to the API."
          onRetry={() => void refetch()}
        />
      </div>
    );
  }

  return (
    <div className="space-y-6 px-1">
      {/* Header */}
      <PageHeader
        title="Publishing Schedule"
        subtitle="Manage and preview upcoming posts across all 5 channels. Drag posts between slots or use the assign dialog."
      />

      <Tabs defaultValue="week">
        <TabsList className="bg-bg-elevated">
          <TabsTrigger value="week" className="gap-1.5">
            <LayoutGrid className="size-3.5" />
            Week
          </TabsTrigger>
          <TabsTrigger value="month" className="gap-1.5">
            <Calendar className="size-3.5" />
            Month
          </TabsTrigger>
        </TabsList>

        <TabsContent value="week" className="mt-4">
          <ScheduleBoard />
        </TabsContent>

        <TabsContent value="month" className="mt-4">
          <CalendarMonth />
        </TabsContent>
      </Tabs>
    </div>
  );
}
