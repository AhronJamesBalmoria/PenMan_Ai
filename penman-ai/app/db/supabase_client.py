from typing import Any

from starlette.concurrency import run_in_threadpool

from app.config import settings
from supabase import Client, create_client


async def execute_async(query: Any) -> Any:
    """Run a PostgREST query builder without blocking the event loop.

    Building the chain (``table(...).select(...).eq(...)``) does no I/O, so it
    is safe on the loop; only ``.execute()`` performs the network call. The
    supabase-py client is synchronous, so the call is handed to a worker thread
    instead of stalling every other request behind it.
    """
    return await run_in_threadpool(query.execute)


def get_supabase() -> Client:
    """Return a fresh Supabase client (anon key).

    A new client is created per call on purpose: the auth client keeps session
    state on the instance, so sharing one across requests leaks state between
    concurrent users.
    """
    return create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)


def get_supabase_with_token(access_token: str) -> Client:
    """Return a new Supabase client that talks to PostgREST as this user.

    Only the PostgREST bearer token is set. ``auth.set_session()`` would issue
    an extra ``/user`` request per call, which is exactly the round-trip we are
    trying to avoid.
    """
    client = create_client(settings.SUPABASE_URL, settings.SUPABASE_ANON_KEY)
    client.postgrest.auth(access_token)
    return client
