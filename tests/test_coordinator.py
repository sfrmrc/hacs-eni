"""Statistics import against a real recorder (needs pytest-homeassistant-custom-component)."""

from datetime import date, datetime, timedelta
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant.components.recorder.statistics import statistics_during_period  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402
from pytest_homeassistant_custom_component.components.recorder.common import (  # noqa: E402
    async_wait_recording_done,
)

from custom_components.eniplenitude.const import DOMAIN  # noqa: E402

ROME = ZoneInfo("Europe/Rome")


def hours(day: date) -> list[tuple[int, float]]:
    start = datetime(day.year, day.month, day.day, tzinfo=ROME)
    return [(int((start + timedelta(hours=h)).timestamp()), 0.5) for h in range(24)]


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):  # recorder before hass
    yield


async def test_backfill_then_resume(hass):
    await hass.config.async_set_time_zone("Europe/Rome")
    last = {"day": date(2026, 11, 2)}
    entry = MockConfigEntry(
        domain=DOMAIN, unique_id="IT001E000",
        data={"username": "u", "password": "p", "pod": "IT001E000", "price_per_kwh": 0.2},
    )
    entry.add_to_hass(hass)

    def supplies():
        midnight = datetime(last["day"].year, last["day"].month, last["day"].day, tzinfo=ROME)
        return [{"pod": "IT001E000", "account": "1", "client_code": "1", "contract_id": "1",
                 "house_id": "1", "last_data": int(midnight.timestamp()), "product": "X", "offer_type": "FISSA"}]

    with patch("custom_components.eniplenitude.coordinator.PlenitudeApi") as api_cls:
        api = api_cls.return_value
        api.async_login = AsyncMock()
        api.async_get_power_supplies = AsyncMock(side_effect=lambda: supplies())
        api.async_get_hourly = AsyncMock(side_effect=lambda s, d: hours(d))
        api.async_get_month_total = AsyncMock(return_value=24.0)

        assert await hass.config_entries.async_setup(entry.entry_id)
        await async_wait_recording_done(hass)
        assert api.async_get_hourly.await_count == 60  # BACKFILL_DAYS

        # Next run: one new day available -> only that day is fetched, sums continue.
        api.async_get_hourly.reset_mock()
        last["day"] = date(2026, 11, 3)
        await entry.runtime_data.async_refresh()
        await async_wait_recording_done(hass)
        assert [c.args[1] for c in api.async_get_hourly.await_args_list] == [date(2026, 11, 3)]

    stats = await hass.async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 1, 1, tzinfo=ROME), None,
        {"eniplenitude:it001e000_energy", "eniplenitude:it001e000_cost"}, "hour", None, {"sum"},
    )
    energy = stats["eniplenitude:it001e000_energy"]
    assert len(energy) == 61 * 24
    assert energy[-1]["sum"] == pytest.approx(61 * 24 * 0.5)
    assert stats["eniplenitude:it001e000_cost"][-1]["sum"] == pytest.approx(61 * 24 * 0.5 * 0.2)
    assert hass.states.get("sensor.plenitude_it001e000_monthly_consumption").state == "24.0"
