from types import SimpleNamespace

import pytest

from run_smc import research_only


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_production_blocks_order_layer_changes(method):
    request = SimpleNamespace(url=SimpleNamespace(path="/api/trading/live/toggle"), method=method)

    async def downstream(_):
        pytest.fail("Execution endpoint reached")

    response = await research_only(request, downstream)
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_production_keeps_scanner_control_available():
    request = SimpleNamespace(url=SimpleNamespace(path="/api/engine/premium/toggle"), method="POST")
    marker = object()

    async def downstream(_):
        return marker

    assert await research_only(request, downstream) is marker
