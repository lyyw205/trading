"""Unit test conftest — no DB, no I/O."""
import pytest


# Ensure no DB fixtures leak into unit tests
@pytest.fixture(autouse=True)
def _no_db_in_unit_tests(request):
    """Guard: unit tests must not use DB fixtures."""
    if "db_session" in request.fixturenames:
        pytest.fail("Unit tests must not use db_session fixture. Use @pytest.mark.integration.")
