"""Push mechanics slice tests — subscribe/unsubscribe endpoints and the VAPID
public-key endpoint (test-send is covered separately)."""

from sqlalchemy import func, select

from app.config import get_settings
from app.models import PushSubscription
from app.tests.conftest import TEST_ADMIN_EMAIL, login_as

SUB = {
    "endpoint": "https://push.example.com/sub/abc123",
    "keys": {"p256dh": "PUBLICKEY", "auth": "AUTHSECRET"},
}


def _count(db_session) -> int:
    return db_session.execute(
        select(func.count()).select_from(PushSubscription)
    ).scalar()


def test_subscribe_creates_row(client, db_session):
    r = client.post("/api/push/subscribe", json=SUB)
    assert r.status_code == 201
    assert _count(db_session) == 1
    sub = db_session.execute(select(PushSubscription)).scalar_one()
    assert sub.endpoint == SUB["endpoint"]
    assert sub.p256dh == "PUBLICKEY"
    assert sub.auth == "AUTHSECRET"


def test_subscribe_upserts_by_endpoint(client, db_session):
    client.post("/api/push/subscribe", json=SUB)
    # Same endpoint, rotated keys — must update in place, not duplicate.
    updated = {**SUB, "keys": {"p256dh": "NEWPUB", "auth": "NEWAUTH"}}
    r = client.post("/api/push/subscribe", json=updated)
    assert r.status_code == 201
    assert _count(db_session) == 1
    sub = db_session.execute(select(PushSubscription)).scalar_one()
    assert sub.p256dh == "NEWPUB"
    assert sub.auth == "NEWAUTH"


def test_subscribe_forbidden_for_yard(client, db_session):
    login_as(client, db_session, "yard@test.local", "yard")
    r = client.post("/api/push/subscribe", json=SUB)
    assert r.status_code == 403
    assert _count(db_session) == 0


def test_unsubscribe_removes_and_is_idempotent(client, db_session):
    client.post("/api/push/subscribe", json=SUB)
    assert _count(db_session) == 1

    r = client.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})
    assert r.status_code == 204
    assert _count(db_session) == 0

    # Removing again is a no-op, still 204.
    r2 = client.post("/api/push/unsubscribe", json={"endpoint": SUB["endpoint"]})
    assert r2.status_code == 204


def test_vapid_public_key_returns_configured_value(client, monkeypatch):
    monkeypatch.setenv("VAPID_PUBLIC_KEY", "TEST_PUBLIC_KEY_B64URL")
    get_settings.cache_clear()
    r = client.get("/api/push/vapid-public-key")
    assert r.status_code == 200
    assert r.json() == {"key": "TEST_PUBLIC_KEY_B64URL"}


def _configure_vapid(monkeypatch):
    """Set a real, valid VAPID pair so the endpoint's Vapid02.from_raw works."""
    from app.scripts.generate_vapid_keys import generate

    public_key, private_key = generate()
    monkeypatch.setenv("VAPID_PUBLIC_KEY", public_key)
    monkeypatch.setenv("VAPID_PRIVATE_KEY", private_key)
    monkeypatch.setenv("VAPID_SUBJECT", "mailto:test@test.local")
    get_settings.cache_clear()


def test_test_send_503_when_not_configured(client, monkeypatch):
    monkeypatch.delenv("VAPID_PRIVATE_KEY", raising=False)
    monkeypatch.delenv("VAPID_PUBLIC_KEY", raising=False)
    get_settings.cache_clear()
    r = client.post("/api/push/test-send")
    assert r.status_code == 503
    assert r.json()["error"] == "push_not_configured"


def test_test_send_admin_only(client, db_session, monkeypatch):
    _configure_vapid(monkeypatch)
    monkeypatch.setattr("app.routers.push.webpush", lambda **kw: None)
    login_as(client, db_session, "mgr@test.local", "manager")
    assert client.post("/api/push/test-send").status_code == 403
    login_as(client, db_session, "yard2@test.local", "yard")
    assert client.post("/api/push/test-send").status_code == 403


def test_test_send_pushes_to_own_subscriptions(client, monkeypatch):
    _configure_vapid(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr("app.routers.push.webpush", lambda **kw: calls.append(kw))
    client.post("/api/push/subscribe", json=SUB)
    r = client.post("/api/push/test-send")
    assert r.status_code == 200
    assert r.json() == {"sent": 1, "pruned": 0, "recipients": {TEST_ADMIN_EMAIL: 1}}
    assert len(calls) == 1
    assert calls[0]["subscription_info"]["endpoint"] == SUB["endpoint"]


def test_test_send_all_reaches_every_user(client, db_session, monkeypatch):
    """scope='all' pushes to every subscription, not just the caller's, and
    the sends carry a real TTL (ttl=0 silently drops on sleeping phones)."""
    from app.models import PushSubscription

    _configure_vapid(monkeypatch)
    calls: list[dict] = []
    monkeypatch.setattr("app.routers.push.webpush", lambda **kw: calls.append(kw))

    client.post("/api/push/subscribe", json=SUB)  # the admin's own device
    db_session.add(  # another user's device, registered elsewhere
        PushSubscription(
            user_email="mgr@test.local",
            endpoint="https://push.example.com/sub/mgr",
            p256dh="P2",
            auth="A2",
        )
    )
    db_session.commit()

    r = client.post("/api/push/test-send", json={"scope": "all"})
    assert r.status_code == 200
    assert r.json() == {
        "sent": 2,
        "pruned": 0,
        "recipients": {TEST_ADMIN_EMAIL: 1, "mgr@test.local": 1},
    }
    endpoints = {c["subscription_info"]["endpoint"] for c in calls}
    assert endpoints == {SUB["endpoint"], "https://push.example.com/sub/mgr"}
    assert all(c["ttl"] > 0 for c in calls)

    # scope='self' (and the no-body default) stays scoped to the caller.
    calls.clear()
    r2 = client.post("/api/push/test-send", json={"scope": "self"})
    assert r2.json()["sent"] == 1
    assert calls[0]["subscription_info"]["endpoint"] == SUB["endpoint"]


def test_test_send_prunes_dead_subscription(client, db_session, monkeypatch):
    import types

    from pywebpush import WebPushException

    _configure_vapid(monkeypatch)

    def gone(**kw):
        exc = WebPushException("gone")
        exc.response = types.SimpleNamespace(status_code=410)
        raise exc

    monkeypatch.setattr("app.routers.push.webpush", gone)
    client.post("/api/push/subscribe", json=SUB)
    assert _count(db_session) == 1

    r = client.post("/api/push/test-send")
    assert r.status_code == 200
    assert r.json() == {"sent": 0, "pruned": 1, "recipients": {}}
    assert _count(db_session) == 0
