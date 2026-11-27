"""Guardrails for the Supabase Auth email templates."""
from app.config import PROJECT_ROOT

EMAILS = PROJECT_ROOT / "supabase" / "emails"


def test_action_emails_do_not_promise_the_removed_consent_step():
    for name in ("confirm_signup.html", "invite.html"):
        html = (EMAILS / name).read_text(encoding="utf-8").lower()

        assert "review the privacy" not in html
        assert "agreements" not in html
        assert "writing workspace" in html


def test_email_readme_has_no_stale_consent_claim():
    readme = (EMAILS / "README.md").read_text(encoding="utf-8")

    assert "consent updates" not in readme
