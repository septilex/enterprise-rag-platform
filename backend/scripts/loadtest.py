"""Basic load + reliability test for ingestion and retrieval.

Drives concurrent ingestion then concurrent search/chat against a running API,
reporting p50/p95/p99 latency and success rate so SLO targets (docs/SLO.md) can
be checked. Stdlib only.

Usage (API on :8000):
  python -m scripts.loadtest --base http://localhost:8000 \
      --ingest 50 --queries 100 --concurrency 10 [--api-key KEY]
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor


def _req(base, path, payload=None, method="POST", api_key=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read())
        return True, time.perf_counter() - t0, body
    except Exception as exc:  # noqa: BLE001
        return False, time.perf_counter() - t0, str(exc)


def _pct(values, p):
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


def _summary(name, results):
    lat = [d for ok, d, _ in results if ok]
    ok = sum(1 for r in results if r[0])
    print(f"\n{name}: {ok}/{len(results)} ok ({ok / len(results) * 100:.1f}%)")
    print(f"  p50={_pct(lat, 0.50) * 1000:.0f}ms  p95={_pct(lat, 0.95) * 1000:.0f}ms  "
          f"p99={_pct(lat, 0.99) * 1000:.0f}ms  max={max(lat, default=0) * 1000:.0f}ms")
    return ok / len(results)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--ingest", type=int, default=50)
    ap.add_argument("--queries", type=int, default=100)
    ap.add_argument("--concurrency", type=int, default=10)
    ap.add_argument("--api-key", default=None)
    args = ap.parse_args()
    base, key = args.base, args.api_key

    _, _, t = _req(base, "/tenants", {"name": f"loadtest-{int(time.time())}"}, api_key=key)
    tid = t["id"]
    _, _, c = _req(base, "/collections", {"tenant_id": tid, "name": "load"}, api_key=key)
    cid = c["id"]
    print(f"tenant={tid} collection={cid}")

    def ingest_one(i):
        return _req(base, "/documents/text", {
            "tenant_id": tid, "collection_id": cid, "title": f"doc-{i}",
            "content": f"Document {i}. The vacation policy grants {20 + i % 5} paid "
                       f"days. Remote work is allowed {i % 4} days per week. " * 3,
        }, api_key=key)

    def query_one(i):
        return _req(base, "/search", {
            "tenant_id": tid, "collection_id": cid,
            "query": "vacation policy remote work", "top_k": 5}, api_key=key)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        ing = list(pool.map(ingest_one, range(args.ingest)))
    ing_ok = _summary("Ingestion", ing)

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        qry = list(pool.map(query_one, range(args.queries)))
    qry_ok = _summary("Retrieval", qry)

    print("\nSLO check (see docs/SLO.md):")
    print(f"  retrieval success >= 99%: {'PASS' if qry_ok >= 0.99 else 'FAIL'}")
    print(f"  ingestion success >= 99%: {'PASS' if ing_ok >= 0.99 else 'FAIL'}")
    q_lat = [d for ok, d, _ in qry if ok]
    print(f"  retrieval p95 < 2s: {'PASS' if _pct(q_lat, 0.95) < 2 else 'FAIL'}")


if __name__ == "__main__":
    main()
