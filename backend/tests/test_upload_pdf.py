"""PDF upload: raw binary/NUL must never reach the DB (regression for the
'A string literal cannot contain NUL (0x00)' 400)."""

import io

from app.db.models import Collection
from app.services.ingestion import extract_text_from_upload


def _blank_pdf() -> bytes:
    from pypdf import PdfWriter
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def test_binary_nul_is_stripped_via_text_path():
    out = extract_text_from_upload("x.txt", "text/plain", b"hello\x00world\x01!")
    assert "\x00" not in out and out == "helloworld!"


def test_pdf_is_parsed_not_decoded_and_has_no_nul():
    text = extract_text_from_upload("doc.pdf", "application/pdf", _blank_pdf())
    assert "\x00" not in text  # never raw binary


def test_plain_text_upload_still_works():
    out = extract_text_from_upload("p.txt", "text/plain", b"vacation policy twenty days")
    assert out == "vacation policy twenty days"


def _coll(api_client, tenant):
    tid = str(tenant.id)
    cid = api_client.post("/collections", json={"tenant_id": tid, "name": "pdf"}).json()["id"]
    return tid, cid


def test_pdf_upload_endpoint_does_not_500_or_nul_error(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    r = api_client.post(
        "/documents/upload",
        data={"tenant_id": tid, "collection_id": cid},
        files={"file": ("doc.pdf", _blank_pdf(), "application/pdf")},
    )
    # Was previously 400 "cannot contain NUL"; now a clean 201 (blank -> quarantined).
    assert r.status_code == 201, r.text
    assert r.json()["status"] in {"embedded", "quarantined"}


def test_txt_upload_still_ingests(api_client, tenant):
    tid, cid = _coll(api_client, tenant)
    r = api_client.post(
        "/documents/upload",
        data={"tenant_id": tid, "collection_id": cid},
        files={"file": ("p.txt", b"Vacation policy grants twenty paid days. " * 4, "text/plain")},
    )
    assert r.status_code == 201 and r.json()["status"] == "embedded"
