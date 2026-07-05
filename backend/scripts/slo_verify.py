"""Item 2: Live SLO verification harness with pass/fail gates.

Wraps the existing load test with hard SLO thresholds (docs/SLO.md) and exits
non-zero if any gate fails — usable in CI / release gating. Additive; reuses
scripts.loadtest primitives, changes no load-test logic.

Usage:
  python -m scripts.slo_verify --base http://localhost:8000 [--api-key KEY] \
      [--ingest 40 --queries 120 --concurrency 12]

SLO gates (overridable via flags):
  --slo-retrieval-success 0.99
  --slo-ingestion-success 0.99
  --slo-retrieval-p95 2.0    (seconds)
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor

from scripts.loadtest import _pct, _req


def _run(base, key, ingest, queries, concurrency):
    _, _, t = _req(base, "/tenants", {"name": f"slo-{int(time.time())}"}, api_key=key)
    tid = t["id"]
    _, _, c = _req(base, "/collections", {"tenant_id": tid, "name": "slo"}, api_key=key)
    cid = c["id"]

    def ingest_one(i):
        return _req(base, "/documents/text", {
            "tenant_id": tid, "collection_id": cid, "title": f"doc-{i}",
            "content": f"Doc {i}. Vacation policy grants {20 + i % 5} days. "
                       f"Remote work {i % 4} days/week. " * 3}, api_key=key)

    def query_one(_i):
        return _req(base, "/search", {
            "tenant_id": tid, "collection_id": cid,
            "query": "vacation policy remote work", "top_k": 5}, api_key=key)

    with ThreadPoolExecutor(max_workers=concurrency) as p:
        ing = list(p.map(ingest_one, range(ingest)))
    with ThreadPoolExecutor(max_workers=concurrency) as p:
        qry = list(p.map(query_one, range(queries)))
    return ing, qry


def _rate(results):
    return sum(1 for r in results if r[0]) / max(len(results), 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--ingest", type=int, default=40)
    ap.add_argument("--queries", type=int, default=120)
    ap.add_argument("--concurrency", type=int, default=12)
    ap.add_argument("--slo-retrieval-success", type=float, default=0.99)
    ap.add_argument("--slo-ingestion-success", type=float, default=0.99)
    ap.add_argument("--slo-retrieval-p95", type=float, default=2.0)
    a = ap.parse_args()

    ing, qry = _run(a.base, a.api_key, a.ingest, a.queries, a.concurrency)
    ing_ok, qry_ok = _rate(ing), _rate(qry)
    q_p95 = _pct([d for ok, d, _ in qry if ok], 0.95)

    gates = [
        ("retrieval success", qry_ok, ">=", a.slo_retrieval_success, qry_ok >= a.slo_retrieval_success),
        ("ingestion success", ing_ok, ">=", a.slo_ingestion_success, ing_ok >= a.slo_ingestion_success),
        ("retrieval p95 (s)", round(q_p95, 3), "<", a.slo_retrieval_p95, q_p95 < a.slo_retrieval_p95),
    ]
    print("\nSLO GATES")
    failed = 0
    for name, val, op, target, ok in gates:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {val} {op} {target}")
        failed += 0 if ok else 1

    print(f"\n{'ALL SLOs MET' if failed == 0 else f'{failed} SLO GATE(S) FAILED'}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
