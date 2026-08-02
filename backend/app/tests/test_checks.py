"""Check-photo upload and photo-serving endpoint tests."""

import uuid
from pathlib import Path

JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"fake-jpeg-body" * 16


def _upload(client, content=JPEG_BYTES, content_type="image/jpeg", name="check.jpg"):
    return client.post("/api/checks", files={"file": (name, content, content_type)})


def test_upload_creates_row_and_file_and_lists(client, tmp_path):
    res = _upload(client)
    assert res.status_code == 201

    body = res.json()
    assert body["status"] == "submitted"
    assert body["source"] == "console"
    assert body["item_type"] == "payment"
    assert body["airtable_id"] is None
    assert body["payment_details"] is not None
    assert body["payment_details"]["amount"] is None  # all-NULL details row
    # Owner rule: a console-captured check's payment date IS its receive day
    # (Eastern time — the company's clock, not the server's UTC).
    from datetime import datetime
    from zoneinfo import ZoneInfo

    today_eastern = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    assert body["payment_details"]["txn_date"] == today_eastern

    saved = Path(body["photo_path"])
    assert saved.parent == tmp_path
    assert saved.read_bytes() == JPEG_BYTES

    listed = client.get("/api/review-items").json()
    assert listed["total"] == 1
    assert listed["items"][0]["id"] == body["id"]


def test_wrong_content_type_is_415(client, tmp_path):
    res = _upload(client, content_type="text/plain", name="check.txt")
    assert res.status_code == 415
    assert list(tmp_path.iterdir()) == []


def test_oversize_is_413_and_leaves_no_file(client, tmp_path):
    res = _upload(client, content=b"x" * (15 * 1024 * 1024 + 1))
    assert res.status_code == 413
    assert list(tmp_path.iterdir()) == []


def test_empty_file_is_400(client, tmp_path):
    res = _upload(client, content=b"")
    assert res.status_code == 400
    assert list(tmp_path.iterdir()) == []


def test_delete_removes_row_details_and_file(client, db_session):
    from sqlalchemy import func, select

    from app.models import PaymentDetails

    body = _upload(client).json()
    saved = Path(body["photo_path"])
    assert saved.is_file()

    res = client.delete(f"/api/review-items/{body['id']}")
    assert res.status_code == 204

    assert not saved.exists()  # photo file removed
    assert client.get("/api/review-items").json()["total"] == 0
    assert client.get(f"/api/photos/{body['id']}").status_code == 404
    assert db_session.scalar(select(func.count()).select_from(PaymentDetails)) == 0


def test_delete_unknown_is_404(client):
    assert client.delete(f"/api/review-items/{uuid.uuid4()}").status_code == 404


def test_photo_endpoint_serves_bytes_then_404s(client):
    body = _upload(client).json()

    res = client.get(f"/api/photos/{body['id']}")
    assert res.status_code == 200
    assert res.headers["content-type"] == "image/jpeg"
    assert res.content == JPEG_BYTES

    # Unknown item → 404.
    assert client.get(f"/api/photos/{uuid.uuid4()}").status_code == 404

    # Row exists but the file vanished → clean 404, not a 500.
    Path(body["photo_path"]).unlink()
    assert client.get(f"/api/photos/{body['id']}").status_code == 404


def test_upload_stamps_payment_method_check(client, tmp_path):
    res = _upload(client)
    assert res.status_code == 201
    assert res.json()["payment_details"]["payment_method"] == "check"
