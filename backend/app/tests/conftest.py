"""Shared test fixtures. Tests run against SQLite in-memory — fast, no
Docker Postgres needed, and the models are written to work on both."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings
from app.db import get_db
from app.main import create_app
from app.models import Base, User

TEST_ADMIN_EMAIL = "admin@test.local"


@pytest.fixture()
def engine():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,  # one shared connection so :memory: persists
    )
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(engine):
    TestingSession = sessionmaker(bind=engine, autoflush=False)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture()
def client(db_session, tmp_path, monkeypatch):
    """TestClient with the real app: DB dependency pointed at SQLite,
    uploads pointed at a throwaway temp directory, and — since Stage 2 —
    logged in as a seeded admin (payments endpoints require a role)."""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path))
    monkeypatch.setenv("SESSION_SECRET", "test-session-secret-not-for-production")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id.apps.googleusercontent.com")
    monkeypatch.setenv("AIRTABLE_WRITE_TOKEN", "test-write-token")
    # Decision flow: hooks auth + push pool. Outbound trigger stays DISABLED
    # (no N8N_PILOT_WEBHOOK_URL) so tests never attempt network calls.
    monkeypatch.setenv("PILOT_HOOK_SECRET", "test-pilot-secret")
    monkeypatch.setenv("PILOT_PUSH_EMAILS", "admin@test.local,mgr@test.local")
    monkeypatch.delenv("N8N_PILOT_WEBHOOK_URL", raising=False)
    # Slab workflow endpoints likewise stay disabled unless a test opts in
    # (the dev container may carry local mock URLs in its env).
    monkeypatch.delenv("N8N_SLAB_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("N8N_SLAB_DECISION_URL", raising=False)
    get_settings.cache_clear()  # re-read env for this test

    app = create_app()

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db

    db_session.add(User(email=TEST_ADMIN_EMAIL, display_name="Test Admin", role="admin"))
    db_session.commit()

    # Import after env is patched so the token is signed with the test secret.
    from app.auth import SESSION_COOKIE, create_session_token

    # https base: the session cookie is Secure, so an http test client
    # would silently refuse to send it back.
    test_client = TestClient(app, base_url="https://testserver")
    test_client.cookies.set(SESSION_COOKIE, create_session_token(TEST_ADMIN_EMAIL, "admin"))
    yield test_client

    app.dependency_overrides.clear()
    get_settings.cache_clear()


def login_as(client_fixture, db_session, email: str, role: str):
    """Helper for role tests: seed a user and switch the client's session."""
    from app.auth import SESSION_COOKIE, create_session_token

    db_session.add(User(email=email, role=role))
    db_session.commit()
    client_fixture.cookies.set(SESSION_COOKIE, create_session_token(email, role))
    return client_fixture
