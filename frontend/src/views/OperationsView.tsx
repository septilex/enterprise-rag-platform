import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { IngestionRun, SourceHealth, SystemStatus } from "../types";
import { Placeholder } from "./DocumentsView";
import { statusLabel, statusTone, timeAgo, triggerLabel } from "../lib/format";

const HEALTH_TONE: Record<string, "ok" | "bad" | "pending"> = {
  healthy: "ok", failing: "bad", stale: "pending", idle: "pending",
};
const HEALTH_LABEL: Record<string, string> = {
  healthy: "Healthy", failing: "Failing", stale: "Stale", idle: "Idle",
};

// Enterprise ops console: system health, ingestion overview, connector health,
// activity timeline — usable by a non-developer operator in ~10 seconds.
export function OperationsView({ tenantId, isAdmin }: { tenantId: string; isAdmin: boolean }) {
  const [status, setStatus] = useState<SystemStatus | null>(null);
  const [runs, setRuns] = useState<IngestionRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [retrying, setRetrying] = useState<string | null>(null);

  const load = useCallback(() => {
    if (!isAdmin) { setLoading(false); return; }
    Promise.allSettled([
      api.systemStatus(tenantId).then(setStatus),
      api.tenantRuns(tenantId, 20).then(setRuns),
    ]).finally(() => setLoading(false));
  }, [tenantId, isAdmin]);

  useEffect(() => { load(); }, [load]);
  // Live-ish refresh so the ops view reflects worker/queue changes.
  useEffect(() => {
    if (!isAdmin) return;
    const t = setInterval(load, 5000);
    return () => clearInterval(t);
  }, [load, isAdmin]);

  const retry = async (r: IngestionRun) => {
    setRetrying(r.id);
    try { await api.retryRun(tenantId, r.id); setTimeout(load, 400); }
    finally { setRetrying(null); }
  };

  if (!isAdmin) {
    return <Placeholder title="Admins only"
      body="You need the Admin role in this workspace to view operations." />;
  }

  const failed = runs.filter((r) => r.status === "failed");

  return (
    <div className="view">
      <div className="view-head">
        <div><h2>Operations</h2><p>Live system health and ingestion activity</p></div>
      </div>

      {/* System health panel */}
      <div className="stat-grid">
        <StatCard label="API" value="Online" tone="ok" />
        <StatCard
          label="Worker"
          value={status?.worker.alive ? "Alive" : "Offline"}
          tone={status?.worker.alive ? "ok" : "bad"}
          sub={status?.worker.last_heartbeat_seconds_ago != null
            ? `heartbeat ${Math.round(status.worker.last_heartbeat_seconds_ago)}s ago`
            : "no heartbeat"} />
        <StatCard label="Active runs" value={String(status?.active_runs ?? 0)}
          tone={(status?.active_runs ?? 0) > 0 ? "pending" : "ok"} />
        <StatCard label="Success rate"
          value={status ? `${Math.round(status.success_rate * 100)}%` : "—"}
          tone={status && status.success_rate < 0.8 ? "bad" : "ok"} />
        <StatCard label="Queue" value={String((status?.queue.incremental ?? 0) + (status?.queue.bulk ?? 0))}
          sub={`${status?.queue.dead ?? 0} dead-letter`} tone={(status?.queue.dead ?? 0) > 0 ? "bad" : "ok"} />
        <StatCard label="Failed runs" value={String(status?.failed_runs ?? 0)}
          tone={(status?.failed_runs ?? 0) > 0 ? "bad" : "ok"} />
      </div>

      {/* Failed runs + retry */}
      {failed.length > 0 && (
        <>
          <h4 className="section-label">Failed jobs — needs attention</h4>
          <div className="table-wrap">
            <table className="data-table">
              <thead><tr><th>Trigger</th><th>Error</th><th>When</th><th></th></tr></thead>
              <tbody>
                {failed.map((r) => (
                  <tr key={r.id}>
                    <td>{triggerLabel(r.trigger_type)}</td>
                    <td className="danger-text cell-strong">{r.error_summary ?? "Unknown error"}</td>
                    <td className="muted">{timeAgo(r.completed_at ?? r.created_at)}</td>
                    <td className="cell-actions">
                      <button className="btn-ghost" disabled={retrying === r.id}
                        onClick={() => retry(r)}>{retrying === r.id ? "…" : "Retry"}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Connector health */}
      <h4 className="section-label">Connector health</h4>
      {status && status.sources.length === 0 && <p className="muted-block">No sources configured yet.</p>}
      {status && status.sources.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead><tr><th>Source</th><th>Type</th><th>Health</th><th>Last success</th><th>Last error</th></tr></thead>
            <tbody>
              {status.sources.map((s: SourceHealth) => (
                <tr key={s.id}>
                  <td className="cell-strong">{s.display_name}</td>
                  <td>{s.source_type}</td>
                  <td><span className={`pill ${HEALTH_TONE[s.health]}`}>{HEALTH_LABEL[s.health]}</span></td>
                  <td className="muted">{timeAgo(s.last_success_at)}</td>
                  <td className={s.last_error_at ? "danger-text" : "muted"}>{timeAgo(s.last_error_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Activity timeline */}
      <h4 className="section-label">Recent activity</h4>
      {loading && <p className="muted-block">Loading…</p>}
      <ul className="timeline">
        {runs.slice(0, 20).map((r) => (
          <li key={r.id} className="timeline-item">
            <span className={`timeline-dot ${statusTone(r.status)}`} />
            <div className="timeline-body">
              <span className="timeline-title">
                {triggerLabel(r.trigger_type)} ingestion — {statusLabel(r.status)}
              </span>
              <span className="timeline-meta">
                {r.documents_indexed} indexed
                {r.documents_deleted ? ` · ${r.documents_deleted} deleted` : ""}
                {r.documents_quarantined ? ` · ${r.documents_quarantined} quarantined` : ""}
                {" · "}{timeAgo(r.completed_at ?? r.created_at)}
              </span>
            </div>
          </li>
        ))}
        {!loading && runs.length === 0 && <li className="muted-block">No activity yet.</li>}
      </ul>
    </div>
  );
}

function StatCard({ label, value, sub, tone }:
  { label: string; value: string; sub?: string; tone: "ok" | "bad" | "pending" }) {
  return (
    <div className="stat-card">
      <div className="stat-label">{label}</div>
      <div className={`stat-value ${tone}`}>{value}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}
