import { Instagram, Youtube, Facebook } from "lucide-react";

interface Props {
  platform: string;
  size?: number;
}

export function PlatformIcon({ platform, size = 14 }: Props) {
  const p = platform.toLowerCase();
  if (p === "instagram") return <Instagram size={size} style={{ color: "#E1306C" }} />;
  if (p === "youtube") return <Youtube size={size} style={{ color: "#FF0000" }} />;
  if (p === "facebook") return <Facebook size={size} style={{ color: "#1877F2" }} />;
  if (p === "x" || p === "twitter" || p === "x_twitter")
    return <span style={{ fontSize: size - 2, fontWeight: 700, color: "#1DA1F2" }}>𝕏</span>;
  if (p === "threads")
    return <span style={{ fontSize: size - 3, fontWeight: 600, color: "#A0A0A0" }}>TH</span>;
  if (p === "tiktok")
    return <span style={{ fontSize: size - 3, fontWeight: 600, color: "#00F2EA" }}>TK</span>;
  return <span style={{ fontSize: size - 3, color: "var(--text-muted)" }}>{p}</span>;
}
