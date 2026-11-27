"""Guardrails for the Render deployment blueprint."""
from app.config import PROJECT_ROOT

REPO_ROOT = PROJECT_ROOT.parent


def test_render_pins_the_same_python_version_as_dotfile():
    blueprint = (REPO_ROOT / "render.yaml").read_text(encoding="utf-8")
    # utf-8-sig tolerates a BOM if the file is ever recreated on Windows.
    local_version = (
        (PROJECT_ROOT / ".python-version")
        .read_text(encoding="utf-8-sig")
        .strip()
    )

    assert "key: PYTHON_VERSION" in blueprint
    assert f"value: {local_version}" in blueprint
