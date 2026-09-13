export function fmtPrice(v: number | null | undefined, market?: string): string {
  if (v === null || v === undefined) return "–";
  const abs = Math.abs(v);
  const digits = abs < 10 ? (abs < 1 ? 5 : 4) : abs > 5000 ? 1 : 2;
  return v.toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

export function fmtPct(v: number | null | undefined, sign = true): string {
  if (v === null || v === undefined) return "–";
  return `${sign && v > 0 ? "+" : ""}${v.toFixed(2)}%`;
}

export function fmtR(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)}R`;
}

export function fmtTime(ts: string | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}

export function fmtDateTime(ts: string | undefined): string {
  if (!ts) return "";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts;
  return d.toLocaleString("en-GB", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}

export function shortAgo(ts: string | undefined): string {
  if (!ts) return "";
  const d = new Date(ts).getTime();
  if (isNaN(d)) return ts;
  const s = Math.max(0, (Date.now() - d) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export const TREND_LABEL: Record<number, string> = { 1: "Bullish", "-1": "Bearish", 0: "Neutral" };

export function statusTone(status: string): "green" | "red" | "amber" | "violet" | "blue" | "slate" {
  if (["TP1_HIT", "TP2_HIT", "TP3_HIT"].includes(status)) return "green";
  if (status === "SL_HIT") return "red";
  if (status === "ACTIVE") return "blue";
  if (status === "EXPIRED") return "slate";
  return "violet";
}

export function statusToneShort(status: string): string {
  if (status === "TP1_HIT") return "TP1";
  if (status === "TP2_HIT") return "TP2";
  if (status === "TP3_HIT") return "TP3";
  if (status === "SL_HIT") return "SL";
  if (status === "ACTIVE") return "Live";
  if (status === "EXPIRED") return "Expired";
  return status;
}

export function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return "Good night";
  if (h < 12) return "Good morning";
  if (h < 17) return "Good afternoon";
  return "Good evening";
}

export const dot: Record<number, string> = { 1: "text-pos", "-1": "text-neg", 0: "text-txt-faint" };
