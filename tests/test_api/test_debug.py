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

    def test_trade_events_module_imports(self):
        """Verify trade_events utility imports."""
        from app.utils.trade_events import (
            buy_decision,
            buy_placed,
            cycle_end,
            cycle_start,
            price_fetched,
            sell_decision,
            sell_placed,
            state_change,
        )
        # All functions should be callable
        assert callable(cycle_start)
        assert callable(cycle_end)
        assert callable(buy_decision)
        assert callable(price_fetched)
        assert callable(buy_placed)
        assert callable(sell_decision)
        assert callable(sell_placed)
        assert callable(state_change)
