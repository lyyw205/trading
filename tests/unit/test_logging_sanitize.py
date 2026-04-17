"""Log sanitization tests."""
from __future__ import annotations

import pytest

from app.utils.logging import _sanitize_msg


@pytest.mark.unit
class TestSanitizeMsg:
    def test_masks_api_key(self):
        assert "***" in _sanitize_msg("api_key=ABCD1234XYZ")
        assert "ABCD1234XYZ" not in _sanitize_msg("api_key=ABCD1234XYZ")

    def test_masks_password(self):
        assert "***" in _sanitize_msg("password=secret123abc")
        assert "secret123abc" not in _sanitize_msg("password=secret123abc")

    def test_masks_authorization(self):
        # Regex captures the value up to whitespace; use a single-token bearer value
        result = _sanitize_msg("authorization=tok12345abcdef")
        assert "tok12345abcdef" not in result
        assert "***" in result

    def test_preserves_safe_text(self):
        safe = "user logged in successfully at 2026-04-17"
        assert _sanitize_msg(safe) == safe

    def test_case_insensitive(self):
        result = _sanitize_msg("API_KEY=xyz123abc Api_Secret=def456ghi")
        assert "xyz123abc" not in result
        assert "def456ghi" not in result
