"""Cross-twin state exchange must behave the same in one process as over Ditto.

The in-process client exists so a composite twin can run its members together
without a broker. What it has to preserve is the contract the twins rely on:
reads that miss raise, writes create, and a twin can write into a sibling's
Thing as well as its own.
"""

from __future__ import annotations

import pytest

from dyon.core.config import TwinConfig
from dyon.services.ditto import (
    InProcessDittoClient,
    ThingNotFoundError,
    ThingRegistry,
    shared_registry,
)


def _config(asset_id: str, asset_type: str = "unit") -> TwinConfig:
    return TwinConfig(asset_id=asset_id, asset_type=asset_type, asset_name=asset_id)


@pytest.fixture
def registry() -> ThingRegistry:
    return ThingRegistry()


@pytest.fixture
def salesman(registry) -> InProcessDittoClient:
    return InProcessDittoClient(_config("salesman_001", "salesman"), registry)


@pytest.fixture
def customer(registry) -> InProcessDittoClient:
    return InProcessDittoClient(_config("customer_001", "customer"), registry)


@pytest.mark.asyncio
async def test_registering_a_thing_seeds_the_standard_features(salesman):
    await salesman.create_thing()
    thing = await salesman.get_thing()
    assert thing["attributes"]["asset_type"] == "salesman"
    assert thing["features"]["telemetry"]["properties"] == {}
    assert thing["features"]["health"]["properties"]["operational_state"] == "running"


@pytest.mark.asyncio
async def test_own_feature_round_trip(salesman):
    await salesman.create_thing()
    await salesman.update_feature("telemetry", {"sentiment_score": 0.4})
    assert await salesman.get_feature("telemetry") == {"sentiment_score": 0.4}


@pytest.mark.asyncio
async def test_updates_merge_rather_than_replace(salesman):
    await salesman.create_thing()
    await salesman.update_feature("telemetry", {"sentiment_score": 0.4})
    await salesman.update_feature("telemetry", {"objection_count": 2.0})
    assert await salesman.get_feature("telemetry") == {
        "sentiment_score": 0.4,
        "objection_count": 2.0,
    }


@pytest.mark.asyncio
async def test_one_twin_reads_another(salesman, customer):
    await customer.create_thing()
    await customer.update_feature("telemetry", {"intent_score": 0.81})
    read = await salesman.get_thing_feature(customer.thing_id, "telemetry")
    assert read["intent_score"] == 0.81


@pytest.mark.asyncio
async def test_one_twin_writes_into_another(salesman, customer):
    # The salesman twin pushes its observations of the customer into the
    # customer twin's Thing; the customer twin then reads them as input.
    await customer.create_thing()
    await salesman.update_thing_feature(
        customer.thing_id, "telemetry", {"sentiment_score": 0.2}
    )
    assert (await customer.get_feature("telemetry"))["sentiment_score"] == 0.2


@pytest.mark.asyncio
async def test_write_to_an_unregistered_thing_creates_it(salesman, registry):
    # Ordering between twins starting up must not lose a write.
    await salesman.update_thing_feature(
        "org.example:product_001", "telemetry", {"price_usd": 2499.0}
    )
    assert "org.example:product_001" in registry.ids()
    assert (
        await salesman.get_thing_feature("org.example:product_001", "telemetry")
    )["price_usd"] == 2499.0


@pytest.mark.asyncio
async def test_reading_an_absent_thing_raises(salesman):
    # The salesman twin catches this and falls back to catalogue defaults, the
    # same path it takes when the HTTP client 404s.
    with pytest.raises(ThingNotFoundError):
        await salesman.get_thing_feature("org.example:nobody", "telemetry")


@pytest.mark.asyncio
async def test_reading_an_absent_feature_raises(salesman):
    await salesman.create_thing()
    with pytest.raises(ThingNotFoundError):
        await salesman.get_feature("nonexistent")


@pytest.mark.asyncio
async def test_re_registering_preserves_live_telemetry(salesman):
    await salesman.create_thing()
    await salesman.update_feature("telemetry", {"sentiment_score": 0.9})
    await salesman.create_thing()  # services layer restarted
    assert (await salesman.get_feature("telemetry"))["sentiment_score"] == 0.9


@pytest.mark.asyncio
async def test_reads_are_copies_not_references(salesman):
    await salesman.create_thing()
    await salesman.update_feature("telemetry", {"sentiment_score": 0.5})
    mutated = await salesman.get_feature("telemetry")
    mutated["sentiment_score"] = 99.0
    assert (await salesman.get_feature("telemetry"))["sentiment_score"] == 0.5


@pytest.mark.asyncio
async def test_registries_are_isolated_from_one_another():
    first, second = ThingRegistry(), ThingRegistry()
    client = InProcessDittoClient(_config("a_001"), first)
    await client.create_thing()
    assert first.ids() and second.ids() == []


@pytest.mark.asyncio
async def test_snapshot_exposes_the_whole_system(salesman, customer, registry):
    await salesman.create_thing()
    await customer.create_thing()
    await customer.update_feature("telemetry", {"intent_score": 0.3})
    snapshot = registry.snapshot()
    assert set(snapshot) == {salesman.thing_id, customer.thing_id}
    assert snapshot[customer.thing_id]["features"]["telemetry"]["properties"] == {
        "intent_score": 0.3
    }


def test_shared_registry_is_one_object():
    assert shared_registry() is shared_registry()


@pytest.mark.asyncio
async def test_lifecycle_calls_are_accepted(salesman):
    # A services layer written against the HTTP client calls these on every
    # start and stop; they must be no-ops rather than errors.
    await salesman.wait_for_ready()
    await salesman.create_policy()
    await salesman.aclose()


@pytest.mark.asyncio
async def test_services_layer_syncs_through_the_in_process_client():
    # The real check: DittoSyncService, unmodified, drives this client.
    from dyon.core.events import EventBus
    from dyon.data import InMemoryCacheAdapter, InMemoryTimeSeriesAdapter
    from dyon.services.ditto import DittoSyncService

    from dyon.core.config import SensorFieldSpec

    config = TwinConfig(
        asset_id="pump_001",
        asset_type="pump",
        asset_name="Pump",
        sensor_fields=[SensorFieldSpec(name="temp_c", nominal=20.0)],
    )
    registry = ThingRegistry()
    client = InProcessDittoClient(config, registry)
    ts = InMemoryTimeSeriesAdapter(config)
    cache = InMemoryCacheAdapter(config)
    ts.write_point("asset_telemetry", {"temp_c": 41.5})
    cache.set_state("running")

    service = DittoSyncService(
        config, EventBus(), ts_store=ts, cache=cache, ditto_client=client
    )
    await service.initialise()
    await service.sync_once()

    assert (await client.get_feature("telemetry"))["temp_c"] == 41.5
    assert (await client.get_feature("health"))["operational_state"] == "running"
