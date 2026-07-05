import { useState } from "react";
import type { IngestionRun, Source } from "../types";
import { Placeholder } from "./DocumentsView";
import { sourceLabel, statusLabel, statusTone, timeAgo, triggerLabel } from "../lib/format";

// Sources + ingestion run history: the operator view of ingestion health.
export function ActivityView({
  collectionName,
  sources,
  runs,
  loading,
  canEdit,
  onToggleSource,
  onReindex,
}: {
  collectionName: string | null;
  sources: Source[];
  runs: IngestionRun[];
  loading: boolean;
  canEdit: boolean;
  onToggleSource: (s: Source) => void;
  onReindex: (s: Source) => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);

  if (!collectionName) {
    return <Placeholder title="No collection selected"
      body="Choose a collection to see its sources and ingestion activity." />;
  }

  const act = async (id: string, fn: () => Promise<void> | void) => {
    setBusy(id);
    try { await fn(); } finally { setBusy(null); }
  };

  return (
    <div className="view">
      <div className="view-head">
        <div><h2>Sources & activity</h2><p>Ingestion health for “{collectionName}”</p></div>
      </div>

      <h4 className="section-label">Sources</h4>
      {sources.length === 0 && <p className="muted-block">No sources yet — upload a document to create one.</p>}
      {sources.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Source</th><th>Type</th><th>State</th><th>Last success</th><th>Last error</th>
                {canEdit && <th></th>}</tr>
            </thead>
            <tbody>
              {sources.map((s) => (
                <tr key={s.id}>
                  <td className="cell-strong">{s.display_name}</td>
                  <td>{sourceLabel(s.source_type)}</td>
                  <td><span className={`pill ${s.enabled ? "ok" : "pending"}`}>{s.enabled ? "Enabled" : "Disabled"}</span></td>
                  <td className="muted">{timeAgo(s.last_success_at)}</td>
                  <td className={s.last_error_at ? "danger-text" : "muted"}>{timeAgo(s.last_error_at)}</td>
                  {canEdit && (
                    <td className="cell-actions">
                      <button className="btn-ghost" disabled={busy === s.id}
                        onClick={() => act(s.id, () => onReindex(s))}>Reindex</button>
                      <button className="btn-ghost" disabled={busy === s.id}
                        onClick={() => act(s.id, () => onToggleSource(s))}>
                        {s.enabled ? "Disable" : "Enable"}
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="section-label">Recent runs</h4>
      {loading && <p className="muted-block">Loading…</p>}
      {!loading && runs.length === 0 && <p className="muted-block">No ingestion runs yet.</p>}
      {runs.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Trigger</th><th>Status</th><th>Indexed</th><th>Quarantined</th><th>Chunks</th><th>When</th></tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id}>
                  <td>{triggerLabel(r.trigger_type)}</td>
                  <td><span className={`pill ${statusTone(r.status)}`}>{statusLabel(r.status)}</span></td>
                  <td>{r.documents_indexed}</td>
                  <td className={r.documents_quarantined ? "danger-text" : "muted"}>{r.documents_quarantined}</td>
                  <td>{r.chunks_created}</td>
                  <td className="muted">{timeAgo(r.completed_at ?? r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {runs.some((r) => r.error_summary) && (
        <div className="run-errors">
          {runs.filter((r) => r.error_summary).slice(0, 4).map((r) => (
            <div key={r.id} className="run-error">{triggerLabel(r.trigger_type)}: {r.error_summary}</div>
          ))}
        </div>
      )}
    </div>
  );
}
