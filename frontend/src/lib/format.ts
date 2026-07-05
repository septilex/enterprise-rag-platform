// Shared presentation helpers so statuses/labels read the same everywhere.

export function timeAgo(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 45) return "just now";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  const d = Math.floor(h / 24);
  return d < 30 ? `${d}d ago` : new Date(iso).toLocaleDateString();
}

// Map raw backend status strings to a tone class + human label.
export function statusTone(status: string): "ok" | "bad" | "pending" {
  if (["embedded", "succeeded", "active"].includes(status)) return "ok";
  if (["quarantined", "failed"].includes(status)) return "bad";
  return "pending"; // pending, running, queued, partial, chunked
}

const STATUS_LABELS: Record<string, string> = {
  embedded: "Indexed",
  succeeded: "Completed",
  partial: "Completed with issues",
  failed: "Failed",
  quarantined: "Quarantined",
  running: "Running",
  queued: "Queued",
  pending: "Pending",
};
export function statusLabel(status: string): string {
  return STATUS_LABELS[status] ?? status;
}

const SOURCE_LABELS: Record<string, string> = {
  manual_upload: "Manual upload",
  api_text: "API text",
  webhook: "Webhook",
  filesystem: "File share",
  text_batch: "Batch",
};
export function sourceLabel(type: string): string {
  return SOURCE_LABELS[type] ?? type;
}

const TRIGGER_LABELS: Record<string, string> = {
  manual: "Manual",
  webhook: "Webhook",
  scheduled: "Scheduled",
  reindex: "Reindex",
  system: "System",
};
export function triggerLabel(t: string): string {
  return TRIGGER_LABELS[t] ?? t;
}

export function roleLabel(role: string): string {
  return role.charAt(0).toUpperCase() + role.slice(1);
}
