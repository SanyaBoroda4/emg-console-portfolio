"""Stage 2 auth tests: session issuance, role wall, edge cases.

Google's verifier is mocked — these tests prove OUR logic (roster lookup,
cookie handling, role enforcement), not Google's cryptography.
"""

import uuid

from app.tests.conftest import TEST_ADMIN_EMAIL, login_as


def _mock_google_verify(monkeypatch, email: str, verified: bool = True):
    def fake_verify(credential, request, client_id):
        return {"email": email, "email_verified": verified}

    monkeypatch.setattr("app.routers.auth.id_token.verify_oauth2_token", fake_verify)


def test_unauthenticated_request_is_401(client):
    client.cookies.clear()
    res = client.get("/api/review-items")
    assert res.status_code == 401
    assert res.json() == {"error": "not_authenticated"}


def test_yard_role_is_403_on_every_payments_endpoint(client, db_session):
    login_as(client, db_session, "yard@test.local", "yard")
    calls = [
        lambda: client.get("/api/review-items"),
        lambda: client.get("/api/review-items/stats"),
        lambda: client.post(
            "/api/checks", files={"file": ("c.jpg", b"\xff\xd8\xff", "image/jpeg")}
        ),
        lambda: client.get(f"/api/photos/{uuid.uuid4()}"),
        lambda: client.delete(f"/api/review-items/{uuid.uuid4()}"),
    ]
    for call in calls:
        res = call()
        assert res.status_code == 403
        assert res.json() == {"error": "forbidden"}


def test_manager_can_view_and_submit_but_not_delete(client, db_session):
    login_as(client, db_session, "manager@test.local", "manager")

    assert client.get("/api/review-items").status_code == 200
    assert client.get("/api/review-items/stats").status_code == 200
    upload = client.post(
        "/api/checks", files={"file": ("c.jpg", b"\xff\xd8\xff", "image/jpeg")}
    )
    assert upload.status_code == 201

    # Deleting anything is admins only (owner rule, 2026-07-10).
    res = client.delete(f"/api/review-items/{upload.json()['id']}")
    assert res.status_code == 403
    assert res.json() == {"error": "forbidden"}


def test_health_needs_no_auth(client):
    client.cookies.clear()
    assert client.get("/api/health").status_code == 200


def test_google_login_unknown_email_is_403_with_no_cookie(client, monkeypatch):
    client.cookies.clear()
    _mock_google_verify(monkeypatch, "stranger@gmail.com")
    res = client.post("/api/auth/google", json={"credential": "mocked"})
    assert res.status_code == 403
    assert res.json()["error"] == "not_authorized"
    assert "set-cookie" not in {key.lower() for key in res.headers}
    assert "emg_session" not in client.cookies


def test_google_login_roster_email_sets_session_with_role(client, monkeypatch):
    client.cookies.clear()
    _mock_google_verify(monkeypatch, TEST_ADMIN_EMAIL)
    res = client.post("/api/auth/google", json={"credential": "mocked"})
    assert res.status_code == 200
    assert res.json()["role"] == "admin"
    assert "emg_session" in client.cookies

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json() == {
        "email": TEST_ADMIN_EMAIL,
        "display_name": "Test Admin",
        "role": "admin",
    }


def test_google_login_email_is_lowercased(client, monkeypatch):
    client.cookies.clear()
    _mock_google_verify(monkeypatch, "Admin@Test.LOCAL")
    res = client.post("/api/auth/google", json={"credential": "mocked"})
    assert res.status_code == 200
    assert res.json()["email"] == TEST_ADMIN_EMAIL


def test_google_login_invalid_token_is_401(client, monkeypatch):
    client.cookies.clear()

    def raise_value_error(*args, **kwargs):
        raise ValueError("bad token")

    monkeypatch.setattr("app.routers.auth.id_token.verify_oauth2_token", raise_value_error)
    res = client.post("/api/auth/google", json={"credential": "junk"})
    assert res.status_code == 401
    assert res.json()["error"] == "invalid_token"
    assert "emg_session" not in client.cookies


def test_google_login_unverified_email_is_401(client, monkeypatch):
    client.cookies.clear()
    _mock_google_verify(monkeypatch, TEST_ADMIN_EMAIL, verified=False)
    res = client.post("/api/auth/google", json={"credential": "mocked"})
    assert res.status_code == 401
    assert "emg_session" not in client.cookies


def test_logout_clears_the_session(client):
    assert client.get("/api/auth/me").status_code == 200
    res = client.post("/api/auth/logout")
    assert res.status_code == 204
    # The response must instruct the browser to drop the cookie...
    set_cookie = res.headers.get("set-cookie", "")
    assert "emg_session=" in set_cookie
    assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()
    # ...and once it's gone (what a real browser does), auth is over.
    client.cookies.clear()
    assert client.get("/api/auth/me").status_code == 401


def test_removed_user_with_valid_cookie_is_403(client, db_session):
    # A perfectly valid signed cookie for someone who left the roster —
    # roles are re-read from the database every request.
    from app.models import User

    login_as(client, db_session, "gone@test.local", "manager")
    db_session.delete(db_session.get(User, "gone@test.local"))
    db_session.commit()

    res = client.get("/api/review-items")
    assert res.status_code == 403
    assert res.json()["error"] == "not_authorized"


def test_tampered_cookie_is_401(client):
    from app.auth import SESSION_COOKIE

    token = client.cookies.get(SESSION_COOKIE)
    assert token
    client.cookies.set(SESSION_COOKIE, token + "x")
    assert client.get("/api/auth/me").status_code == 401


# --- GIS redirect mode (/api/auth/google/redirect — iOS login path) ---


def _post_redirect(client, credential="tok", csrf_cookie="c1", csrf_field="c1"):
    client.cookies.set("g_csrf_token", csrf_cookie)
    return client.post(
        "/api/auth/google/redirect",
        data={"credential": credential, "g_csrf_token": csrf_field},
        follow_redirects=False,
    )


def test_redirect_login_sets_cookie_and_redirects_home(client, monkeypatch):
    from app.auth import SESSION_COOKIE

    client.cookies.clear()
    _mock_google_verify(monkeypatch, TEST_ADMIN_EMAIL)
    res = _post_redirect(client)
    assert res.status_code == 303
    assert res.headers["location"] == "/"
    assert SESSION_COOKIE in res.cookies


def test_redirect_login_csrf_mismatch_redirects_to_login_with_error(client, monkeypatch):
    from app.auth import SESSION_COOKIE

    client.cookies.clear()
    _mock_google_verify(monkeypatch, TEST_ADMIN_EMAIL)
    res = _post_redirect(client, csrf_cookie="c1", csrf_field="DIFFERENT")
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login?error=")
    assert SESSION_COOKIE not in res.cookies


def test_redirect_login_unknown_email_redirects_with_message_no_cookie(client, monkeypatch):
    from app.auth import SESSION_COOKIE

    client.cookies.clear()
    _mock_google_verify(monkeypatch, "stranger@gmail.com")
    res = _post_redirect(client)
    assert res.status_code == 303
    assert res.headers["location"].startswith("/login?error=")
    assert "console" in res.headers["location"]  # the "isn't set up" message
    assert SESSION_COOKIE not in res.cookies
