"""Unit tests for the sign-in method change email (email_provider.security_email_body). No backend needed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from email_provider import security_email_body  # noqa: E402


@pytest.mark.parametrize("event", ["linked", "signin_linked", "unlinked"])
def test_every_event_has_a_subject_and_advice(event):
    subject, html, text = security_email_body("Ada", event, "google", "ada@gmail.com")
    assert "Google" in subject and "Hi Ada," in text and "If this wasn't you" in text
    assert ("ada@gmail.com" in text) == (event != "unlinked")


def test_user_and_provider_values_are_escaped_in_html():
    _s, html, _t = security_email_body("<b>Eve</b>", "linked", "google", "x<script>@evil.example")
    assert "<b>Eve</b>" not in html and "&lt;b&gt;Eve&lt;/b&gt;" in html
    assert "<script>" not in html


def test_no_name_and_no_provider_email():
    _s, _h, text = security_email_body("", "linked", "apple", None)
    assert text.startswith("Hi,") and "Apple account was linked" in text
