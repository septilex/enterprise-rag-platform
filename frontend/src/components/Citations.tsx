import { useState } from "react";
import type { Citation } from "../types";

// UI-02 + retrieval transparency: shows the sources an answer was grounded in.
// Each chip expands the referenced chunk (doc + chunk index + snippet), and a
// "why this answer" toggle reveals the full retrieved context inline.
export function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  const [showAll, setShowAll] = useState(false);
  if (!citations.length) return null;

  return (
    <div className="citations">
      <div className="citations-head">
        <span className="citations-label">
          Grounded in {citations.length} source{citations.length === 1 ? "" : "s"}
        </span>
        <button className="why-toggle" onClick={() => setShowAll((v) => !v)}>
          {showAll ? "Hide retrieved context" : "Why this answer?"}
        </button>
      </div>

      <div className="citations-row">
        {citations.map((c) => (
          <button
            key={c.chunk_id}
            className={`citation-chip ${open === c.index ? "active" : ""}`}
            onClick={() => setOpen(open === c.index ? null : c.index)}
            title="Show source"
          >
            <span className="chip-num">{c.index}</span>
            <span className="chip-doc">doc {c.document_id.slice(0, 6)} · #{c.chunk_index}</span>
          </button>
        ))}
      </div>

      {open !== null && !showAll && (() => {
        const c = citations.find((x) => x.index === open)!;
        return <SourceCard c={c} />;
      })()}

      {showAll && (
        <div className="retrieved-context">
          {citations.map((c) => <SourceCard key={c.chunk_id} c={c} />)}
        </div>
      )}
    </div>
  );
}

function SourceCard({ c }: { c: Citation }) {
  return (
    <div className="citation-source">
      <div className="citation-source-meta">
        <span>Source [{c.index}]</span>
        <code>document {c.document_id.slice(0, 8)} · chunk #{c.chunk_index}</code>
      </div>
      <p>{c.snippet}</p>
    </div>
  );
}
