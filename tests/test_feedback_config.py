"""Ensure a beta/public build never ships with an empty feedback destination."""

from __future__ import annotations

import re
from pathlib import Path

FEEDBACK_JS = Path(__file__).resolve().parents[1] / "frontend" / "feedback.js"


def test_feedback_destination_is_configured() -> None:
    text = FEEDBACK_JS.read_text(encoding="utf-8")
    email_match = re.search(r'^export const FEEDBACK_EMAIL = "([^"]*)";$', text, flags=re.MULTILINE)
    form_match = re.search(r'^export const FEEDBACK_FORM_URL = "([^"]*)";$', text, flags=re.MULTILINE)
    assert email_match is not None and form_match is not None
    email = email_match.group(1).strip()
    form_url = form_match.group(1).strip()
    assert email or form_url, (
        "Both FEEDBACK_EMAIL and FEEDBACK_FORM_URL are empty in frontend/feedback.js."
    )
