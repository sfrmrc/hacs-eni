"""Constants for the Eni Plenitude integration."""

from datetime import timedelta

DOMAIN = "eniplenitude"

CONF_POD = "pod"
CONF_PRICE = "price_per_kwh"

DEFAULT_PRICE = 0.25  # €/kWh, all-in average; the user should set their own
BACKFILL_DAYS = 60  # hourly history imported on first setup (portal keeps ~4 months)
UPDATE_INTERVAL = timedelta(hours=12)  # portal data lags 2-3 days, more polling is useless
