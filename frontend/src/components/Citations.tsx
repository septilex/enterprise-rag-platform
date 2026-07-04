import { useState } from "react";
import type { Citation } from "../types";

// UI-02: citations shown alongside an answer; clicking one reveals the source
// chunk (id + snippet) it references.
export function Citations({ citations }: { citations: Citation[] }) {
  const [open, setOpen] = useState<number | null>(null);
  if (!citations.length) return null;

  return (
    <div className="citations">
      <div className="citations-row">
        {citations.map((c) => (
          <button
            key={c.chunk_id}
            className={`citation-chip ${open === c.index ? "active" : ""}`}
            onClick={() => setOpen(open === c.index ? null : c.index)}
            title="Show source chunk"
          >
            [{c.index}]
          </button>
        ))}
      </div>
      {open !== null && (() => {
        const c = citations.find((x) => x.index === open)!;
        return (
          <div className="citation-source">
            <div className="citation-source-meta">
              <span>source [{c.index}]</span>
              <code>doc {c.document_id.slice(0, 8)} · chunk #{c.chunk_index}</code>
            </div>
            <p>{c.snippet}</p>
          </div>
        );
      })()}
    </div>
  );
}
