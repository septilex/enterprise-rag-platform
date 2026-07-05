"""Item 7: one-command end-to-end production smoke test.

Exercises the full stack against a running API and exits non-zero on any
failure, so a deployment can be gate-checked with a single command. Covers:
health/ready, tenant+collection, upload, search, grounded chat + citations,
sources/runs visibility, and metrics. Stdlib only; additive.

Usage:
  python -m scripts.smoke_test --base http://localhost:8000 [--api-key KEY] [--user-email X]
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
import urllib.request

PASS, FAIL = "\033[32mPASS\033[0m", "\033[31mFAIL\033[0m"


class Ctx:
    def __init__(self, base, api_key, user_email):
        self.base, self.api_key, self.user_email = base, api_key, user_email
        self.failures = 0

    def _headers(self, extra=None):
        h = dict(extra or {})
        if self.api_key:
            h["X-API-Key"] = self.api_key
        if self.user_email:
            h["X-User-Email"] = self.user_email
        return h

    def call(self, path, payload=None, method="POST", raw=None, ctype="application/json"):
        data = raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None)
        headers = self._headers({"Content-Type": ctype} if data and raw is None else (
            {"Content-Type": ctype} if raw is not None else {}))
        req = urllib.request.Request(self.base + path, data=data, headers=headers,
                                     method=method if data else "GET")
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read()
            return r.status, (json.loads(body) if body[:1] in (b"{", b"[") else body.decode(errors="replace"))

    def check(self, name, ok, detail=""):
        print(f"  [{PASS if ok else FAIL}] {name}{(' — ' + detail) if detail and not ok else ''}")
        if not ok:
            self.failures += 1
        return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--api-key", default=None)
    ap.add_argument("--user-email", default=None)
    a = ap.parse_args()
    c = Ctx(a.base.rstrip("/"), a.api_key, a.user_email)
    print(f"Smoke test -> {c.base}")

    try:
        s, _ = c.call("/health", method="GET"); c.check("health 200", s == 200)
        s, _ = c.call("/ready", method="GET"); c.check("ready 200", s == 200)

        _, t = c.call("/tenants", {"name": f"smoke-{int(time.time())}"})
        tid = t["id"]; c.check("create tenant", bool(tid))
        _, col = c.call("/collections", {"tenant_id": tid, "name": "smoke"})
        cid = col["id"]; c.check("create collection", bool(cid))

        # multipart upload
        bnd = "smokebnd"; buf = io.BytesIO()
        for k, v in (("tenant_id", tid), ("collection_id", cid)):
            buf.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
        buf.write(f"--{bnd}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"p.txt\"\r\n\r\n".encode())
        buf.write(b"Vacation policy grants twenty paid days per year. Remote work three days per week.\r\n")
        buf.write(f"--{bnd}--\r\n".encode())
        s, up = c.call("/documents/upload", raw=buf.getvalue(),
                       ctype=f"multipart/form-data; boundary={bnd}")
        c.check("upload document embedded", s == 201 and up.get("status") == "embedded",
                str(up))

        s, res = c.call("/search", {"tenant_id": tid, "collection_id": cid,
                                    "query": "vacation policy", "top_k": 5})
        c.check("search returns hits", res.get("total", 0) > 0)

        s, chat = c.call("/chat", {"tenant_id": tid, "collection_id": cid,
                                   "query": "how many vacation days?"})
        c.check("chat grounded + citations",
                chat.get("grounded") is True and len(chat.get("citations", [])) >= 1)

        _, srcs = c.call(f"/sources?tenant_id={tid}&collection_id={cid}", method="GET")
        c.check("source recorded", any(s2["source_type"] == "manual_upload" for s2 in srcs))
        _, runs = c.call(f"/ingestion/runs?tenant_id={tid}&collection_id={cid}", method="GET")
        c.check("ingestion run visible", len(runs) >= 1)

        s, metrics = c.call("/metrics", method="GET")
        c.check("metrics endpoint", "rag_requests_total" in metrics)
    except Exception as exc:  # any unhandled error = failure
        print(f"  [{FAIL}] unexpected error: {exc}")
        c.failures += 1

    print(f"\n{'SMOKE TEST PASSED' if c.failures == 0 else f'SMOKE TEST FAILED ({c.failures} check(s))'}")
    return 0 if c.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
