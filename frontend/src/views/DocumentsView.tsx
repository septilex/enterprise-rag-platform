import type { DocumentSummary } from "../types";
import { sourceLabel, statusLabel, statusTone, timeAgo } from "../lib/format";

function docName(d: DocumentSummary): string {
  const meta = d.doc_metadata as Record<string, string> | undefined;
  return meta?.filename || meta?.title || d.source_uri.replace(/^[a-z_]+:\/\//, "");
}

export function DocumentsView({
  collectionName,
  documents,
  loading,
  canEdit,
  onUpload,
}: {
  collectionName: string | null;
  documents: DocumentSummary[];
  loading: boolean;
  canEdit: boolean;
  onUpload: () => void;
}) {
  if (!collectionName) {
    return <Placeholder title="No collection selected"
      body="Choose a collection to see its documents." />;
  }
  return (
    <div className="view">
      <div className="view-head">
        <div>
          <h2>Documents</h2>
          <p>{documents.length} document{documents.length === 1 ? "" : "s"} in “{collectionName}”</p>
        </div>
        {canEdit && <button className="btn-primary" onClick={onUpload}>Upload document</button>}
      </div>

      {loading && <p className="muted-block">Loading…</p>}
      {!loading && documents.length === 0 && (
        <Placeholder title="No documents yet"
          body={canEdit ? "Upload a file to index it into this collection."
                        : "No documents have been indexed in this collection yet."} />
      )}

      {documents.length > 0 && (
        <div className="table-wrap">
          <table className="data-table">
            <thead>
              <tr><th>Name</th><th>Source</th><th>Status</th><th>Chunks</th><th>Indexed</th></tr>
            </thead>
            <tbody>
              {documents.map((d) => (
                <tr key={d.id}>
                  <td className="cell-strong" title={docName(d)}>{docName(d)}</td>
                  <td>{sourceLabel(d.source_type)}</td>
                  <td><span className={`pill ${statusTone(d.status)}`}>{statusLabel(d.status)}</span></td>
                  <td>{d.chunk_count}</td>
                  <td className="muted">{timeAgo(d.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function Placeholder({ title, body }: { title: string; body: string }) {
  return (
    <div className="view-empty">
      <div className="view-empty-mark">◈</div>
      <h3>{title}</h3>
      <p>{body}</p>
    </div>
  );
}
