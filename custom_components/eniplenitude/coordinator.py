"""Data update coordinator: logs in, imports hourly statistics, refreshes the sensors."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .api import PlenitudeApi, PlenitudeAuthError, PlenitudeError
from .const import BACKFILL_DAYS, CONF_POD, CONF_PRICE, DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)


class PlenitudeCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Fetches one POD."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, name=DOMAIN, config_entry=entry, update_interval=UPDATE_INTERVAL
        )
        # Own session: login clears the cookie jar, must not touch HA's shared one.
        self.api = PlenitudeApi(
            async_create_clientsession(hass), entry.data[CONF_USERNAME], entry.data[CONF_PASSWORD]
        )
        self.pod: str = entry.data[CONF_POD]
        self.energy_statistic_id = f"{DOMAIN}:{self.pod.lower()}_energy"
        self.cost_statistic_id = f"{DOMAIN}:{self.pod.lower()}_cost"

    @property
    def price(self) -> float:
        """€/kWh from options, falling back to the value given at setup."""
        return self.config_entry.options.get(CONF_PRICE, self.config_entry.data[CONF_PRICE])

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self.api.async_login()
            supply = next(
                (s for s in await self.api.async_get_power_supplies() if s["pod"] == self.pod),
                None,
            )
            if supply is None:
                raise UpdateFailed(f"POD {self.pod} not found in the Plenitude account")

            if supply.get("last_data"):
                last_day = dt_util.as_local(dt_util.utc_from_timestamp(supply["last_data"])).date()
            else:
                last_day = dt_util.now().date() - timedelta(days=1)

            await self._async_import_hourly(supply, last_day)
            month_kwh = await self.api.async_get_month_total(supply, last_day)
        except PlenitudeAuthError as err:
            # Triggers the reauth flow.
            raise ConfigEntryAuthFailed(str(err)) from err
        except PlenitudeError as err:
            raise UpdateFailed(str(err)) from err

        return {
            "month_kwh": month_kwh,
            "last_data": last_day,
            "product": supply.get("product"),
            "offer_type": supply.get("offer_type"),
        }

    async def _async_last_stat(self, statistic_id: str) -> dict[str, Any] | None:
        last = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )
        return last[statistic_id][0] if last.get(statistic_id) else None

    async def _async_import_hourly(self, supply: dict[str, Any], last_day: date) -> None:
        """Append hourly points after the last imported hour to the external statistics.

        Only new hours are written and the running sums continue from the stored ones,
        so re-running never double counts (unlike re-importing whole periods with an offset).
        """
        last_energy = await self._async_last_stat(self.energy_statistic_id)
        if last_energy:
            energy_sum = last_energy["sum"] or 0.0
            last_cost = await self._async_last_stat(self.cost_statistic_id)
            cost_sum = (last_cost or {}).get("sum") or 0.0
            day = dt_util.as_local(dt_util.utc_from_timestamp(last_energy["start"])).date() + timedelta(days=1)
        else:
            energy_sum = cost_sum = 0.0
            day = last_day - timedelta(days=BACKFILL_DAYS - 1)

        price = self.price
        energy: list[StatisticData] = []
        cost: list[StatisticData] = []
        try:
            while day <= last_day:
                # shortcut: a day the portal answers NO_DATA for (e.g. DST change days) is
                # skipped for good; re-import by clearing the statistic if the portal fills it later.
                for epoch, kwh in await self.api.async_get_hourly(supply, day):
                    start = dt_util.utc_from_timestamp(epoch)
                    energy_sum += kwh
                    cost_sum += kwh * price
                    energy.append(StatisticData(start=start, sum=round(energy_sum, 3)))
                    cost.append(StatisticData(start=start, sum=round(cost_sum, 4)))
                day += timedelta(days=1)
        finally:
            # Save what was fetched even if a later day failed: the next run resumes after it.
            if energy:
                async_add_external_statistics(
                    self.hass,
                    _metadata(self.energy_statistic_id, f"Plenitude {self.pod} energia",
                              UnitOfEnergy.KILO_WATT_HOUR, EnergyConverter.UNIT_CLASS),
                    energy,
                )
                async_add_external_statistics(
                    self.hass,
                    _metadata(self.cost_statistic_id, f"Plenitude {self.pod} costo", "EUR", None),
                    cost,
                )
                _LOGGER.info("Imported %d hourly points for %s", len(energy), self.pod)


def _metadata(statistic_id: str, name: str, unit: str, unit_class: str | None) -> StatisticMetaData:
    return StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=name,
        source=DOMAIN,
        statistic_id=statistic_id,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )
