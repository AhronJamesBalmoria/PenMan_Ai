"""Test fixtures: isolate the app from real Supabase/Gemini credentials."""
import os
from datetime import UTC, datetime
from uuid import uuid4

# Set before importing app.config so load_dotenv() does not override these.
os.environ.update(
    {
        "APP_ENV": "test",
        "COOKIE_SECURE": "false",
        "SUPABASE_URL": "https://test.supabase.co",
        "SUPABASE_ANON_KEY": "test-anon-key",
        "SUPABASE_JWT_SECRET": "test-secret-key-for-unit-tests-0123456789",
        "GEMINI_API_KEY": "test-gemini-key",
    }
)

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.dependencies import get_current_user, get_user_supabase  # noqa: E402
from app.main import app  # noqa: E402

USER_ID = "11111111-1111-1111-1111-111111111111"


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, store, operation, payload=None):
        self.store = store
        self.operation = operation
        self.payload = payload or {}
        self.filters = {}
        self.single_row = False
        self.order_by = None

    def select(self, *columns, **kwargs):
        self.operation = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def order(self, key, desc=False):
        self.order_by = (key, desc)
        return self

    def single(self):
        self.single_row = True
        return self

    def _matches(self):
        return [
            row
            for row in self.store.values()
            if all(row.get(key) == value for key, value in self.filters.items())
        ]

    def execute(self):
        stamp = datetime.now(UTC).isoformat()

        if self.operation == "insert":
            row = dict(self.payload)
            row.setdefault("id", str(uuid4()))
            row["updated_at"] = stamp
            self.store[row["id"]] = row
            return FakeResponse([row])

        if self.operation == "update":
            rows = self._matches()
            for row in rows:
                row.update(self.payload)
                row["updated_at"] = stamp
            return FakeResponse(rows)

        if self.operation == "upsert":
            row = dict(self.payload)
            row.setdefault("id", str(uuid4()))
            row["updated_at"] = stamp
            existing = self.store.get(row["id"], {})
            existing.update(row)
            self.store[row["id"]] = existing
            return FakeResponse([existing])

        if self.operation == "delete":
            removed = self._matches()
            for row in removed:
                self.store.pop(row["id"], None)
            return FakeResponse(removed)

        if self.operation == "select":
            rows = [dict(row) for row in self._matches()]
            if self.order_by:
                key, desc = self.order_by
                rows.sort(key=lambda row: row.get(key) or "", reverse=desc)
            if self.single_row:
                return FakeResponse(rows[0] if rows else None)
            return FakeResponse(rows)

        return FakeResponse([])


class FakeTable:
    def __init__(self, store):
        self.store = store

    def select(self, *columns, **kwargs):
        return FakeQuery(self.store, "select")

    def insert(self, payload):
        return FakeQuery(self.store, "insert", payload)

    def update(self, payload):
        return FakeQuery(self.store, "update", payload)

    def upsert(self, payload):
        return FakeQuery(self.store, "upsert", payload)

    def delete(self):
        return FakeQuery(self.store, "delete")


class FakeSupabase:
    def __init__(self, drafts):
        self.drafts = drafts
        self.profiles = {}

    def table(self, name):
        if name == "profiles":
            return FakeTable(self.profiles)
        return FakeTable(self.drafts)


@pytest.fixture
def store():
    """Drafts table contents."""
    return {}


@pytest.fixture
def fake_db(store):
    return FakeSupabase(store)


@pytest.fixture
def client(fake_db):
    app.dependency_overrides[get_current_user] = lambda: {
        "id": USER_ID,
        "email": "writer@example.com",
        "access_token": "test-token",
    }
    app.dependency_overrides[get_user_supabase] = lambda: fake_db
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def anonymous_client():
    app.dependency_overrides.clear()
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client
