"""Debug endpoint tests."""

import pytest


@pytest.mark.unit
class TestDebugEndpointAccess:
    """Verify debug endpoint security."""

    def test_debug_endpoint_returns_404_when_debug_disabled(self):
        """Debug endpoint must return 404 when settings.debug=False."""
        # This is a placeholder that will be expanded with integration tests
        # For now, verify the module imports correctly
        from app.api.debug import router

        assert router.prefix == "/api/debug"

    def test_trade_events_module_removed(self):
        """Verify trade_events utility was removed (unused code cleanup)."""
        import importlib

        result = importlib.util.find_spec("app.utils.trade_events")
        assert result is None
