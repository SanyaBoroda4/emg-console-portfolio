"""Multi-round decision flow: each decision answers ONE question
(answers_event_id), so the workflow may ask again after an answer didn't pan
out — while first-tap-wins still holds within every round."""

import pytest

HOOK = {"X-Pilot-Secret": "test-pilot-secret"}
RESUME_URL = "https://n8n.test/resume"


class FakeResponse:
    def raise_for_status(self) -> None:
        pass


@pytest.fixture()
def resume_calls(monkeypatch):
    """Mock the workflow's resume endpoint; records every POST body."""
    calls: list[dict] = []

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return FakeResponse()

    monkeypatch.setattr("app.routers.decisions.httpx.post", fake_post)
    return calls


def make_item(client) -> str:
    res = client.post(
        "/api/hooks/pilot/items",
        json={"amount": "100.00", "payer_name": "Test Payer"},
        headers=HOOK,
    )
    assert res.status_code == 201
    return res.json()["review_item_id"]


def ask(client, item_id: str, body: str = "Which job?", **overrides):
    payload = {
        "review_item_id": item_id,
        "body": body,
        "candidates": [{"label": "Job A", "job_id": "J-1"}],
        "resume_url": RESUME_URL,
        "allowed_freeform": True,
        "format_hint": "4-digit invoice #",
    }
    payload.update(overrides)
    return client.post("/api/hooks/pilot/question", json=payload, headers=HOOK)


def decide(client, item_id: str, payload: dict):
    return client.post(f"/api/review-items/{item_id}/decision", json=payload)


def question_id(client, item_id: str) -> str:
    events = client.get(f"/api/review-items/{item_id}/events").json()["events"]
    return [e for e in events if e["kind"] == "bot_question"][-1]["id"]


def test_multi_round_ask_answer_ask_again(client, resume_calls):
    item_id = make_item(client)
    assert ask(client, item_id).status_code == 200
    q1 = question_id(client, item_id)

    # Round 1: freeform answer; resume receives {secret, text}.
    res = decide(client, item_id, {"text": "Simmons job"})
    assert res.status_code == 200
    assert resume_calls[-1]["url"] == RESUME_URL
    assert resume_calls[-1]["json"]["text"] == "Simmons job"
    assert resume_calls[-1]["json"]["secret"] == "test-pilot-secret"

    # The workflow searched, found nothing — it may ask AGAIN.
    res = ask(client, item_id, body="Still nothing. Exact job name?")
    assert res.status_code == 200
    q2 = question_id(client, item_id)
    assert q2 != q1

    # The open round shows on the board again.
    ids = client.get("/api/review-items/needs-decision").json()["ids"]
    assert item_id in ids

    # Round 2 answers with a choice; the decision pairs with q2.
    res = decide(client, item_id, {"choice": {"label": "Job A", "job_id": "J-1"}})
    assert res.status_code == 200
    events = client.get(f"/api/review-items/{item_id}/events").json()["events"]
    decisions = [e for e in events if e["kind"] == "decision"]
    assert [d["answers_event_id"] for d in decisions] == [q1, q2]

    ids = client.get("/api/review-items/needs-decision").json()["ids"]
    assert item_id not in ids


def test_second_question_blocked_while_open(client):
    item_id = make_item(client)
    assert ask(client, item_id).status_code == 200
    res = ask(client, item_id, body="Impatient repeat")
    assert res.status_code == 409
    # The app flattens HTTPException detail into the response body.
    assert res.json()["error"] == "question_open"


def test_second_tap_names_the_winner(client, resume_calls):
    item_id = make_item(client)
    ask(client, item_id)
    assert decide(client, item_id, {"text": "first"}).status_code == 200
    res = decide(client, item_id, {"text": "second"})
    assert res.status_code == 409
    detail = res.json()
    assert detail["error"] == "already_decided"
    assert detail["decided_by"] == "admin@test.local"


def test_resume_failure_leaves_round_answerable(client, monkeypatch):
    item_id = make_item(client)
    ask(client, item_id)

    def broken_post(url, json=None, timeout=None):
        raise ConnectionError("workflow down")

    monkeypatch.setattr("app.routers.decisions.httpx.post", broken_post)
    res = decide(client, item_id, {"text": "lost answer"})
    assert res.status_code == 502

    # Compensating delete: no decision event survived, retry succeeds.
    events = client.get(f"/api/review-items/{item_id}/events").json()["events"]
    assert not [e for e in events if e["kind"] == "decision"]

    def ok_post(url, json=None, timeout=None):
        return FakeResponse()

    monkeypatch.setattr("app.routers.decisions.httpx.post", ok_post)
    assert decide(client, item_id, {"text": "retry"}).status_code == 200


def test_freeform_rejected_when_question_forbids_it(client, resume_calls):
    item_id = make_item(client)
    ask(client, item_id, allowed_freeform=False)
    res = decide(client, item_id, {"text": "not allowed"})
    assert res.status_code == 422
    assert (
        decide(client, item_id, {"choice": {"label": "Job A", "job_id": "J-1"}})
        .status_code
        == 200
    )


def test_update_hook_writes_job_fields(client):
    item_id = make_item(client)
    res = client.post(
        "/api/hooks/pilot/update",
        json={
            "review_item_id": item_id,
            "body": "Matched to Simmons — Kiawah.",
            "fields": {
                "matched_job_name": "Simmons — Kiawah",
                "matched_job_id": "J-1042",
                "moraware_url": "https://example.moraware.net/sys/job/1042",
                "check_number": "4127",
            },
        },
        headers=HOOK,
    )
    assert res.status_code == 200
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["matched_job_name"] == "Simmons — Kiawah"
    assert item["matched_job_id"] == "J-1042"
    assert item["moraware_url"] == "https://example.moraware.net/sys/job/1042"
    assert item["payment_details"]["check_number"] == "4127"


def test_update_hook_rejects_bad_moraware_url_and_unknown_fields(client):
    item_id = make_item(client)
    res = client.post(
        "/api/hooks/pilot/update",
        json={"review_item_id": item_id, "body": "x",
              "fields": {"moraware_url": "javascript:alert(1)"}},
        headers=HOOK,
    )
    assert res.status_code == 422
    res = client.post(
        "/api/hooks/pilot/update",
        json={"review_item_id": item_id, "body": "x",
              "fields": {"qb_invoice": "1234"}},
        headers=HOOK,
    )
    assert res.status_code == 403


def test_items_hook_accepts_check_number(client):
    res = client.post(
        "/api/hooks/pilot/items",
        json={"amount": "500.00", "check_number": "4127", "qb_payment_id": "77"},
        headers=HOOK,
    )
    assert res.status_code == 201
    item_id = res.json()["review_item_id"]
    item = client.get(f"/api/review-items/{item_id}/events").json()["item"]
    assert item["payment_details"]["check_number"] == "4127"
    assert item["payment_details"]["qb_payment_id"] == "77"


def test_list_hook_returns_register_snapshot(client):
    a = make_item(client)
    client.post(
        "/api/hooks/pilot/update",
        json={"review_item_id": a, "body": "job set",
              "fields": {"matched_job_name": "Simmons", "qb_payment_id": "99"}},
        headers=HOOK,
    )
    res = client.get("/api/hooks/pilot/list", headers=HOOK)
    assert res.status_code == 200
    rows = res.json()["items"]
    row = next(r for r in rows if r["review_item_id"] == a)
    assert row["qb_payment_id"] == "99"
    assert row["matched_job_name"] == "Simmons"
    assert row["status"]
    # secret required
    assert client.get("/api/hooks/pilot/list").status_code == 401


def test_notify_hook_pushes_to_pool(client, monkeypatch):
    calls = []

    def fake_pool(db, *, title, body, url):
        calls.append({"title": title, "body": body, "url": url})
        return (3, 0)

    monkeypatch.setattr("app.routers.hooks.push_to_pilot_pool", fake_pool)
    res = client.post(
        "/api/hooks/pilot/notify",
        json={"title": "Sweep: 4 recorded", "body": "1 needs a job"},
        headers=HOOK,
    )
    assert res.status_code == 200
    assert res.json()["pushed"] == 3
    assert calls[0]["url"] == "/payments"
    res = client.post(
        "/api/hooks/pilot/notify",
        json={"title": " ", "body": "x"}, headers=HOOK,
    )
    assert res.status_code == 422


def test_photo_hook_serves_original(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"original-quality" * 8
    res = client.post("/api/checks", files={"file": ("check.jpg", jpeg, "image/jpeg")})
    assert res.status_code == 201
    item_id = res.json()["id"]
    res = client.get(f"/api/hooks/pilot/photo/{item_id}", headers=HOOK)
    assert res.status_code == 200
    assert res.content == jpeg
    assert client.get(f"/api/hooks/pilot/photo/{item_id}").status_code == 401
    photoless = make_item(client)
    assert (
        client.get(f"/api/hooks/pilot/photo/{photoless}", headers=HOOK).status_code
        == 404
    )


def test_question_and_final_respect_push_flag(client, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "app.routers.hooks.push_to_pilot_pool",
        lambda db, *, title, body, url: (calls.append(title), (1, 0))[1],
    )
    item_id = make_item(client)
    res = ask(client, item_id, push=False)
    assert res.status_code == 200 and res.json()["pushed"] == 0
    client.post(
        f"/api/review-items/{item_id}/decision", json={"text": "x"}
    )  # resume mocked elsewhere; ignore result
    res = client.post(
        "/api/hooks/pilot/final",
        json={"review_item_id": item_id, "body": "done", "status": "confirmed",
              "push": False},
        headers=HOOK,
    )
    assert res.status_code == 200 and res.json()["pushed"] == 0
    assert calls == []
