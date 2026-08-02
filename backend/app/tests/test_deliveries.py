"""Slab deliveries: upload, slab hooks, assignment flow, confirm."""

import pytest

HOOK = {"X-Pilot-Secret": "test-pilot-secret"}
JPEG = b"\xff\xd8\xff\xe0" + b"slip" * 32

MATERIALS = [
    {"material": "NAMIB WHITE", "slab_count": 2, "total_sf": 115.0,
     "serials": "1511-40, 1511-41"},
    {"material": "EXOTICA", "slab_count": 5, "total_sf": 300.64},
]


def upload(client):
    res = client.post("/api/deliveries",
                      files={"file": ("slip.jpg", JPEG, "image/jpeg")})
    assert res.status_code == 201
    body = res.json()
    assert body["item_type"] == "slab_delivery"
    assert body["delivery_details"] is not None
    return body["id"]


def slab_update(client, item_id, materials=MATERIALS, push=False, **details):
    payload = {
        "review_item_id": item_id,
        "body": "Read the slip: CRS Marble & Granite, 7 slabs.",
        "details": {"supplier": "CRS Marble & Granite", "document_number": "SC23317",
                    "total": "30921.34", "slab_count": 7,
                    "materials": materials, **details},
        "status": "needs_job",
        "push": push,
    }
    return client.post("/api/hooks/slab/update", json=payload, headers=HOOK)


def test_upload_and_hook_fill(client, tmp_path):
    item_id = upload(client)
    res = slab_update(client, item_id)
    assert res.status_code == 200
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    d = item["delivery_details"]
    assert d["supplier"] == "CRS Marble & Granite"
    assert d["slab_count"] == 7
    assert len(d["materials"]) == 2
    assert d["materials"][0]["job_id"] is None
    assert item["status"] == "needs_job"


def test_assignment_one_mode_and_confirm(client, monkeypatch):
    item_id = upload(client)
    slab_update(client, item_id)
    assert client.post(f"/api/deliveries/{item_id}/mode",
                       json={"mode": "one"}).status_code == 200
    res = client.post(f"/api/deliveries/{item_id}/assign",
                      json={"job_id": "5829", "job_name": "Oliver Kahn",
                            "moraware_url": "https://m/5829"})
    assert res.status_code == 200
    assert res.json() == {"ok": True, "assigned": 2, "total": 2}

    sent = []

    class OkResp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.routers.deliveries.httpx.post",
                        lambda url, json=None, timeout=None:
                        (sent.append({"url": url, "json": json}), OkResp())[1])
    monkeypatch.setenv("N8N_SLAB_DECISION_URL", "https://n8n.test/slab-decision")
    from app.config import get_settings
    get_settings.cache_clear()

    res = client.post(f"/api/deliveries/{item_id}/confirm")
    assert res.status_code == 200
    body = sent[0]["json"]
    assert body["review_item_id"] == item_id
    assert body["jobs"][0]["job_id"] == "5829"
    assert len(body["jobs"][0]["materials"]) == 2
    assert body["all_stock"] is False
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["matched_job_name"] == "Oliver Kahn"
    get_settings.cache_clear()


def test_split_assignment_and_stock(client, monkeypatch):
    item_id = upload(client)
    slab_update(client, item_id)
    client.post(f"/api/deliveries/{item_id}/mode", json={"mode": "split"})
    # material 0 → job, material 1 → stock
    assert client.post(f"/api/deliveries/{item_id}/assign",
                       json={"material_index": 0, "job_id": "5829",
                             "job_name": "Oliver Kahn"}).status_code == 200
    # confirm blocked while material 1 unassigned
    monkeypatch.setenv("N8N_SLAB_DECISION_URL", "https://n8n.test/slab-decision")
    from app.config import get_settings
    get_settings.cache_clear()
    assert client.post(f"/api/deliveries/{item_id}/confirm").status_code == 409
    assert client.post(f"/api/deliveries/{item_id}/assign",
                       json={"material_index": 1, "stock": True}).status_code == 200

    class OkResp:
        def raise_for_status(self):
            pass

    calls = []
    monkeypatch.setattr("app.routers.deliveries.httpx.post",
                        lambda url, json=None, timeout=None:
                        (calls.append(json), OkResp())[1])
    assert client.post(f"/api/deliveries/{item_id}/confirm").status_code == 200
    assert calls[0]["stock_materials"][0]["material"] == "EXOTICA"
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["matched_job_name"] == "1 jobs + stock"
    get_settings.cache_clear()


def test_confirm_failure_keeps_assignments(client, monkeypatch):
    item_id = upload(client)
    slab_update(client, item_id)
    client.post(f"/api/deliveries/{item_id}/assign",
                json={"job_id": "1", "job_name": "X"})
    monkeypatch.setenv("N8N_SLAB_DECISION_URL", "https://n8n.test/down")
    from app.config import get_settings
    get_settings.cache_clear()

    def boom(url, json=None, timeout=None):
        raise ConnectionError("down")

    monkeypatch.setattr("app.routers.deliveries.httpx.post", boom)
    assert client.post(f"/api/deliveries/{item_id}/confirm").status_code == 502
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["delivery_details"]["materials"][0]["job_name"] == "X"
    get_settings.cache_clear()


def test_slab_find_dedup_keys(client):
    item_id = upload(client)
    slab_update(client, item_id)
    res = client.get("/api/hooks/slab/find",
                     params={"supplier": "CRS Marble & Granite",
                             "document_number": "SC23317"}, headers=HOOK)
    assert [r["review_item_id"] for r in res.json()["items"]] == [item_id]
    res = client.get("/api/hooks/slab/find",
                     params={"supplier": "CRS Marble & Granite",
                             "total": "30921.34", "slab_count": 7}, headers=HOOK)
    assert [r["review_item_id"] for r in res.json()["items"]] == [item_id]
    assert client.get("/api/hooks/slab/find", headers=HOOK).status_code == 422
    assert client.get("/api/hooks/slab/find",
                      params={"supplier": "x"}).status_code == 401


def test_slab_update_push_flag(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.routers.slab_hooks.push_to_pilot_pool",
                        lambda db, *, title, body, url:
                        (calls.append((title, url)), (2, 0))[1])
    item_id = upload(client)
    res = slab_update(client, item_id, push=True)
    assert res.json()["pushed"] == 2
    assert calls[0][0].startswith("Delivery from CRS")
    assert calls[0][1] == f"/deliveries/item/{item_id}"


def test_deliveries_hidden_from_payments_board(client):
    item_id = upload(client)
    listed = client.get("/api/review-items", params={"item_type": "payment"}).json()
    assert item_id not in [i["id"] for i in listed["items"]]
    listed = client.get("/api/review-items",
                        params={"item_type": "slab_delivery"}).json()
    assert item_id in [i["id"] for i in listed["items"]]


def test_delivery_resend(client, db_session, monkeypatch):
    # A REAL tiny JPEG: resend re-encodes the stored photo (PIL) for the
    # outbound payload, so fake bytes would fail the send.
    import io

    from PIL import Image
    from sqlalchemy.orm import sessionmaker

    # The background task opens its own session — point its factory at the
    # TEST engine, or its writes land in a different database.
    monkeypatch.setattr(
        "app.routers.deliveries.SessionLocal",
        sessionmaker(bind=db_session.get_bind(), autoflush=False),
    )

    buf = io.BytesIO()
    Image.new("RGB", (24, 24), "white").save(buf, "JPEG")
    res = client.post("/api/deliveries",
                      files={"file": ("slip.jpg", buf.getvalue(), "image/jpeg")})
    item_id = res.json()["id"]
    # No workflow configured -> honest 502.
    assert client.post(f"/api/deliveries/{item_id}/resend").status_code == 502
    monkeypatch.setenv("N8N_SLAB_WEBHOOK_URL", "https://n8n.test/pilot-slabbot")
    from app.config import get_settings
    get_settings.cache_clear()
    sent = []

    class OkResp:
        def raise_for_status(self):
            pass

    monkeypatch.setattr("app.routers.deliveries.httpx.post",
                        lambda url, json=None, timeout=None:
                        (sent.append(url), OkResp())[1])
    assert client.post(f"/api/deliveries/{item_id}/resend").status_code == 202
    # The task wrote through its own session — drop this session's cache.
    db_session.expire_all()
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["status"] == "processing"
    assert sent == ["https://n8n.test/pilot-slabbot"]
    get_settings.cache_clear()


def test_delete_delivery_and_audit_label(client):
    item_id = upload(client)
    slab_update(client, item_id)
    assert client.delete(f"/api/review-items/{item_id}").status_code == 204
    listed = client.get("/api/review-items",
                        params={"item_type": "slab_delivery"}).json()
    assert item_id not in [i["id"] for i in listed["items"]]
    audit = client.get("/api/audit").json()
    labels = [e["item_label"] for e in audit["entries"]]
    assert "delivery CRS Marble & Granite — SC23317" in labels


def test_slab_update_pushes_only_once(client, monkeypatch):
    calls = []
    monkeypatch.setattr("app.routers.slab_hooks.push_to_pilot_pool",
                        lambda db, *, title, body, url:
                        (calls.append(title), (1, 0))[1])
    item_id = upload(client)
    assert slab_update(client, item_id, push=True).json()["pushed"] == 1
    # A workflow rerun/resend must stay quiet — one delivery, one push.
    assert slab_update(client, item_id, push=True).json()["pushed"] == 0
    assert len(calls) == 1
