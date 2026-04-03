import { useMemo } from "react";
import { AlertTriangle } from "lucide-react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Badge } from "@/components/ui/badge";
import type { Blueprint } from "@/api/types";

/* Character limits per platform — hard limits where truncation occurs */
const LIMITS: Record<string, Record<string, number>> = {
  instagram: { caption: 2200, hashtags: 2200 },
  youtube: { title: 100, description: 5000 },
  x_twitter: { tweet: 280 },
  facebook: { post: 63206 },
};

interface PlatformTab {
  id: string;
  label: string;
  color: string;
  data: Record<string, unknown> | null | undefined;
}

function CharCount({ text, limit }: { text: string; limit: number }) {
  const len = text.length;
  const isOver = len > limit;
  return (
    <span
      className="ml-2 text-xs tabular-nums"
      style={{ color: isOver ? "var(--destructive, #ef4444)" : "var(--text-muted)" }}
    >
      {isOver && <AlertTriangle className="mb-0.5 mr-0.5 inline size-3" />}
      {len}/{limit}
    </span>
  );
}

function ContentField({
  label,
  value,
  limit,
}: {
  label: string;
  value: string;
  limit?: number;
}) {
  if (!value) return null;
  return (
    <div className="mb-3">
      <div className="flex items-center">
        <h4
          className="text-xs font-medium uppercase tracking-wider text-text-muted"
        >
          {label}
        </h4>
        {limit && <CharCount text={value} limit={limit} />}
      </div>
      <p
        className="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-text-secondary"
      >
        {value}
      </p>
    </div>
  );
}

function HashtagField({ value }: { value: string }) {
  if (!value) return null;
  const tags = value
    .split(/[\s,]+/)
    .filter((h) => h.length > 0)
    .map((h) => (h.startsWith("#") ? h : `#${h}`));

  return (
    <div className="mb-3">
      <h4
        className="mb-1.5 text-xs font-medium uppercase tracking-wider text-text-muted"
      >
        Hashtags
      </h4>
      <div className="flex flex-wrap gap-1.5">
        {tags.map((tag) => (
          <Badge
            key={tag}
            variant="outline"
            className="text-xs border-border bg-bg-elevated text-text-muted"
          >
            {tag}
          </Badge>
        ))}
      </div>
    </div>
  );
}

function InstagramTab({ item }: { item: Blueprint }) {
  return (
    <div>
      <ContentField
        label="Caption"
        value={item.caption ?? ""}
        limit={LIMITS.instagram.caption}
      />
      <HashtagField value={item.hashtags ?? ""} />
      {item.cta && <ContentField label="CTA" value={item.cta} />}
    </div>
  );
}

function YouTubeTab({ data }: { data: Record<string, unknown> }) {
  return (
    <div>
      <ContentField
        label="Title"
        value={String(data.title ?? "")}
        limit={LIMITS.youtube.title}
      />
      <ContentField
        label="Description"
        value={String(data.description ?? "")}
        limit={LIMITS.youtube.description}
      />
      <HashtagField value={String(data.hashtags ?? data.tags ?? "")} />
    </div>
  );
}

function XTwitterTab({ data }: { data: Record<string, unknown> }) {
  const tweet = String(data.tweet ?? data.text ?? data.body ?? "");
  return (
    <div>
      <ContentField label="Tweet" value={tweet} limit={LIMITS.x_twitter.tweet} />
      <HashtagField value={String(data.hashtags ?? "")} />
    </div>
  );
}

function FacebookTab({ data }: { data: Record<string, unknown> }) {
  const post = String(data.post ?? data.text ?? data.body ?? "");
  return (
    <div>
      <ContentField label="Post" value={post} limit={LIMITS.facebook.post} />
      <HashtagField value={String(data.hashtags ?? "")} />
    </div>
  );
}

export function PlatformAdaptationsPanel({ item }: { item: Blueprint }) {
  const tabs = useMemo<PlatformTab[]>(() => {
    const result: PlatformTab[] = [];
    // Instagram always shown (uses base caption/hashtags)
    result.push({
      id: "instagram",
      label: "IG",
      color: "var(--platform-instagram)",
      data: null,
    });
    if (item.youtube_content && Object.keys(item.youtube_content).length > 0) {
      result.push({
        id: "youtube",
        label: "YT",
        color: "var(--platform-youtube)",
        data: item.youtube_content,
      });
    }
    if (item.twitter_content && Object.keys(item.twitter_content).length > 0) {
      result.push({
        id: "x_twitter",
        label: "X",
        color: "var(--platform-x)",
        data: item.twitter_content,
      });
    }
    if (item.facebook_content && Object.keys(item.facebook_content).length > 0) {
      result.push({
        id: "facebook",
        label: "FB",
        color: "var(--platform-facebook)",
        data: item.facebook_content,
      });
    }
    return result;
  }, [item.youtube_content, item.twitter_content, item.facebook_content]);

  if (tabs.length <= 1 && !item.caption) return null;

  return (
    <div>
      <h3
        className="mb-2 text-xs font-medium uppercase tracking-wider text-text-muted"
      >
        Platform Content
      </h3>
      <Tabs defaultValue="instagram">
        <TabsList variant="line" className="mb-3">
          {tabs.map((tab) => (
            <TabsTrigger
              key={tab.id}
              value={tab.id}
              className="text-xs"
              style={{ "--tab-color": tab.color } as React.CSSProperties}
            >
              {tab.label}
            </TabsTrigger>
          ))}
        </TabsList>

        <TabsContent value="instagram">
          <InstagramTab item={item} />
        </TabsContent>
        {tabs
          .filter((t) => t.id !== "instagram" && t.data)
          .map((tab) => (
            <TabsContent key={tab.id} value={tab.id}>
              {tab.id === "youtube" && (
                <YouTubeTab data={tab.data as Record<string, unknown>} />
              )}
              {tab.id === "x_twitter" && (
                <XTwitterTab data={tab.data as Record<string, unknown>} />
              )}
              {tab.id === "facebook" && (
                <FacebookTab data={tab.data as Record<string, unknown>} />
              )}
            </TabsContent>
          ))}
      </Tabs>
    </div>
  );
}
