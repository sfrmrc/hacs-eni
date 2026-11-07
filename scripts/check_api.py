"""Try the Plenitude login and data calls outside Home Assistant.

    uv run --with aiohttp python scripts/check_api.py you@example.com

Asks for the password, prints the POD list, the last day of hourly data and the month total.
"""

import asyncio
import getpass
import importlib.util
import logging
import sys
from datetime import datetime
from pathlib import Path

import aiohttp

_spec = importlib.util.spec_from_file_location(
    "plenitude_api", Path(__file__).parents[1] / "custom_components/eniplenitude/api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)


async def main(email: str) -> None:
    async with aiohttp.ClientSession() as session:
        client = api.PlenitudeApi(session, email, getpass.getpass("Password Plenitude: "))
        await client.async_login()
        print("Login OK")
        for supply in await client.async_get_power_supplies():
            last_day = datetime.fromtimestamp(supply["last_data"]).date()
            hours = await client.async_get_hourly(supply, last_day)
            month = await client.async_get_month_total(supply, last_day)
            print(f"POD {supply['pod']} ({supply['product']}, {supply['offer_type']})")
            print(f"  ultimo giorno {last_day}: {len(hours)} ore, {sum(k for _, k in hours):.3f} kWh")
            print(f"  mese: {month} kWh")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG if "-v" in sys.argv else logging.INFO)
    asyncio.run(main(sys.argv[1]))
