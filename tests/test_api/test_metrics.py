"""Metrics API endpoint tests."""
import pytest


@pytest.mark.unit
class TestMetricsRouter:
    """Verify metrics router is correctly defined."""

    def test_metrics_module_imports(self):
        """Metrics module imports without error."""
        from app.api.metrics import router
        assert router is not None

    def test_metrics_router_has_metrics_route(self):
        """Router exposes a /metrics GET route."""
        from app.api.metrics import router
        routes = [r.path for r in router.routes]
        assert "/metrics" in routes

    def test_metrics_router_tags(self):
        """Router is tagged as monitoring."""
        from app.api.metrics import router
        assert "monitoring" in router.tags
