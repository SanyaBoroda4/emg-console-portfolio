"""Slab scans: create session, edit slab list, assign job, confirm posts
the appended note to the bridge's job-form-note endpoint."""


SLABS = [
    {"id": "2287478", "source": "qr", "material": "TAJ MAHAL QUARTZITE"},
    {"id": "1945313", "source": "qr", "material": "TAJ MAHAL QUARTZITE"},
    {"id": "1663704", "source": "ocr", "material": "NAMIB WHITE"},
]


def create(client, slabs=SLABS):
    res = client.post("/api/scans", json={"slab_ids": slabs})
    assert res.status_code == 201
    body = res.json()
    assert body["item_type"] == "slab_scan"
    assert body["status"] == "pending"
    assert body["scan_details"] is not None
    return body["id"]


def _mock_bridge(monkeypatch, sent, ok=True):
    class Resp:
        def raise_for_status(self):
            if not ok:
                raise RuntimeError("bridge down")

    def fake_post(url, headers=None, json=None, timeout=None):
        sent.append({"url": url, "headers": headers, "json": json})
        return Resp()

    monkeypatch.setattr("app.routers.scans.httpx.post", fake_post)
    monkeypatch.setenv("BRIDGE_CONSOLE_KEY", "test-bridge-key")
    from app.config import get_settings
    get_settings.cache_clear()


def test_create_dedupes_ids(client):
    item_id = create(client, SLABS + [{"id": "2287478", "source": "manual"}])
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    ids = [s["id"] for s in item["scan_details"]["slab_ids"]]
    assert ids == ["2287478", "1945313", "1663704"]
    assert item["scan_details"]["scanned_date"] is not None


def test_update_slabs_and_assign(client):
    item_id = create(client)
    res = client.put(f"/api/scans/{item_id}/slabs", json={"slab_ids": [
        {"id": "2287478", "source": "qr"},
        {"id": "7654321", "source": "manual"},
    ]})
    assert res.status_code == 200 and res.json()["count"] == 2

    res = client.post(f"/api/scans/{item_id}/assign", json={
        "job_id": 5829, "job_name": "Oliver Kahn TEST",
        "moraware_url": "https://m/5829",
    })
    assert res.status_code == 200
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["matched_job_id"] == "5829"
    assert item["matched_job_name"] == "Oliver Kahn TEST"


def test_confirm_requires_job_and_slabs(client):
    item_id = create(client, slabs=[])
    assert client.post(f"/api/scans/{item_id}/confirm").status_code == 409
    client.put(f"/api/scans/{item_id}/slabs",
               json={"slab_ids": [{"id": "2287478", "source": "qr"}]})
    res = client.post(f"/api/scans/{item_id}/confirm")
    assert res.status_code == 409
    assert res.json()["error"] == "no_job"


def test_confirm_requires_material_on_every_slab(client, monkeypatch):
    item_id = create(client, slabs=[
        {"id": "2287478", "source": "qr", "material": "NAMIB WHITE"},
        {"id": "1945313", "source": "qr"},
    ])
    client.post(f"/api/scans/{item_id}/assign",
                json={"job_id": 5829, "job_name": "Oliver Kahn TEST"})
    res = client.post(f"/api/scans/{item_id}/confirm")
    assert res.status_code == 409
    assert res.json()["error"] == "no_material"
    assert "1945313" in res.json()["message"]


def test_materials_search_collapses_variants(client):
    # Different suppliers / finishes / sizes of the same stone...
    for name in ["Taj Mahal Quartzite", "TAJ MAHAL 3CM",
                 "Taj Mahal Extra Honed FF 3CM", "Genesis Taj Mahal Quartz 3CM",
                 "Super White", "Namib White"]:
        assert client.post("/api/materials", json={"name": name}).status_code == 200
    names = [m["name"] for m in
             client.get("/api/materials/search", params={"q": "taj"}).json()["materials"]]
    # ...collapse to base names; supplier/finish/size gone. (Genesis is an
    # engineered-quartz line, kept distinct from the natural stone.)
    assert "Taj Mahal" in names
    assert "Taj Mahal Quartzite" not in names
    assert "Taj Mahal Extra Honed FF 3CM" not in names
    # "Super White" must NOT collapse to "White"
    white = [m["name"] for m in
             client.get("/api/materials/search", params={"q": "super"}).json()["materials"]]
    assert "Super White" in white


def test_materials_bulk_upsert_hook(client):
    res = client.post("/api/materials/upsert",
                      json={"source": "website", "materials": [
                          {"name": "ACQUA BELLA QUARTZITE 3CM", "supplier": "AGM"},
                          {"name": "ACQUA BELLA QUARTZITE 3CM", "supplier": "AGM"},
                      ]},
                      headers={"X-Pilot-Secret": "test-pilot-secret"})
    assert res.status_code == 200
    assert res.json()["added"] == 1
    assert client.post("/api/materials/upsert",
                       json={"source": "website", "materials": []},
                       headers={"X-Pilot-Secret": "wrong"}).status_code == 401


def test_confirm_posts_note_and_turns_confirmed(client, monkeypatch):
    item_id = create(client)
    client.post(f"/api/scans/{item_id}/assign", json={
        "job_id": 5829, "job_name": "Oliver Kahn TEST",
        "moraware_url": "https://m/5829",
    })
    sent = []
    _mock_bridge(monkeypatch, sent)
    res = client.post(f"/api/scans/{item_id}/confirm")
    assert res.status_code == 200

    assert sent[0]["url"].endswith("/api/console/job-form-note")
    assert sent[0]["headers"]["X-Console-Key"] == "test-bridge-key"
    assert sent[0]["json"]["jobId"] == 5829
    assert sent[0]["json"]["form"] == "details"
    lines = sent[0]["json"]["text"].split("\n")
    assert lines[0].startswith("Slabs scanned ")
    assert lines[1:] == [
        "TAJ MAHAL QUARTZITE — 2287478",
        "TAJ MAHAL QUARTZITE — 1945313",
        "NAMIB WHITE — 1663704",
    ]

    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["status"] == "confirmed"

    # double confirm blocked
    assert client.post(f"/api/scans/{item_id}/confirm").status_code == 409
    from app.config import get_settings
    get_settings.cache_clear()


def test_confirm_bridge_failure_keeps_pending(client, monkeypatch):
    item_id = create(client)
    client.post(f"/api/scans/{item_id}/assign",
                json={"job_id": 5829, "job_name": "Oliver Kahn TEST"})
    sent = []
    _mock_bridge(monkeypatch, sent, ok=False)
    res = client.post(f"/api/scans/{item_id}/confirm")
    assert res.status_code == 502
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["status"] == "pending"
    from app.config import get_settings
    get_settings.cache_clear()


def test_yard_can_scan_but_not_payments(client, db_session, monkeypatch):
    from app.tests.conftest import login_as
    # Seed a job for the typeahead.
    from datetime import date, datetime, timezone
    from app.models import JobDirectory
    db_session.add(JobDirectory(job_id=5829, customer_name="Oliver Kahn TEST",
                                lead_url="https://m/5829", creation_date=date(2026, 7, 1),
                                synced_at=datetime.now(timezone.utc)))
    db_session.commit()

    login_as(client, db_session, "wade@example.com", "yard")

    # yard CAN use the scan section end to end
    item_id = create(client)
    assert client.get("/api/scans/list").status_code == 200
    assert client.get(f"/api/scans/{item_id}/card").status_code == 200
    assert client.get("/api/jobs/search", params={"q": "oliver"}).status_code == 200
    assert client.get("/api/materials/search", params={"q": "taj"}).status_code == 200

    # yard CANNOT see payments or the general review-items list
    assert client.get("/api/review-items?item_type=payment").status_code == 403
    assert client.get("/api/deliveries", params={}).status_code in (403, 405)


def test_slab_ids_unique_across_cards(client):
    # First card takes 2287478.
    first = create(client, slabs=[{"id": "2287478", "source": "qr", "material": "X"}])
    # A second card can't reuse it.
    res = client.post("/api/scans", json={"slab_ids": [
        {"id": "2287478", "source": "qr"},
        {"id": "9999999", "source": "qr"},
    ]})
    assert res.status_code == 409
    assert res.json()["error"] == "duplicate_slabs"
    assert "2287478" in res.json()["duplicates"]

    # used-ids reflects the first card.
    used = client.get("/api/scans/used-ids").json()["ids"]
    assert "2287478" in used

    # Updating a DIFFERENT card to include the taken id is also rejected.
    other = create(client, slabs=[{"id": "1111111", "source": "qr", "material": "Y"}])
    res = client.put(f"/api/scans/{other}/slabs", json={"slab_ids": [
        {"id": "1111111", "source": "qr"},
        {"id": "2287478", "source": "qr"},
    ]})
    assert res.status_code == 409
    # ...but the same card keeping its own id is fine.
    assert client.put(f"/api/scans/{first}/slabs", json={"slab_ids": [
        {"id": "2287478", "source": "qr", "material": "X"},
    ]}).status_code == 200
