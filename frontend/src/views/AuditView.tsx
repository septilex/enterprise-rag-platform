import { useEffect, useState } from "react";
import { api } from "../api";
import type { AuditEntry } from "../types";
import { Placeholder } from "./DocumentsView";
import { timeAgo } from "../lib/format";

const ACTION_LABELS: Record<string, string> = {
  "tenant.create": "Created workspace",
  "collection.create": "Created collection",
  "document.upload": "Uploaded document",
  "document.delete": "Deleted document",
  "document.erase": "Erased document",
  "membership.grant": "Granted access",
  "membership.revoke": "Revoked access",
  "source.update": "Updated source",
  "source.reindex": "Reindexed source",
  "ingest.webhook": "Webhook ingestion",
  "bootstrap.admin": "Bootstrapped admin",
};

function summarize(e: AuditEntry): string {
  const t = e.target || {};
  const parts: string[] = [];
  if (t.name) parts.push(String(t.name));
  if (t.email) parts.push(String(t.email));
  if (t.role) parts.push(`as ${t.role}`);
  if (t.filename) parts.push(String(t.filename));
  if (t.run_status) parts.push(`(${t.run_status})`);
  return parts.join(" ");
}

export function AuditView({ tenantId, isAdmin }: { tenantId: string; isAdmin: boolean }) {
  const [entries, setEntries] = useState<AuditEntry[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!isAdmin) { setLoading(false); return; }
    api.listAudit(tenantId).then(setEntries).catch(() => setEntries([]))
      .finally(() => setLoading(false));
  }, [tenantId, isAdmin]);

  if (!isAdmin) {
    return <Placeholder title="Admins only"
      body="You need the Admin role in this workspace to view the audit log." />;
  }

  return (
    <div className="view">
      <div className="view-head">
        <div><h2>Audit log</h2><p>Recent administrative activity in this workspace</p></div>
      </div>
      {loading && <p className="muted-block">Loading…</p>}
      {!loading && entries.length === 0 && (
        <Placeholder title="No activity yet" body="Administrative actions will appear here." />
      )}
      {entries.length > 0 && (
        <ul className="audit-feed">
          {entries.map((e) => (
            <li key={e.id} className="audit-item">
              <div className="audit-dot" />
              <div className="audit-body">
                <div className="audit-line">
                  <span className="audit-action">{ACTION_LABELS[e.action] ?? e.action}</span>
                  <span className="audit-detail">{summarize(e)}</span>
                </div>
                <div className="audit-meta">
                  <span>{e.actor}</span><span>·</span><span>{timeAgo(e.created_at)}</span>
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
