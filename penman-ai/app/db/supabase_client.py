from supabase import Client, create_client

from app.config import settings


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
