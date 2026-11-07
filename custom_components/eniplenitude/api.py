"""Client for the Eni Plenitude customer area.

Flow reverse-engineered from eniplenitude.com/my-eni (Nov 2026):

1. SAP Gigya (CDC) login: ``accounts.login`` -> login_token, ``accounts.getJWT`` -> id_token.
2. ``GET /serviceDAp/api/c360/getJwe`` with header ``JWT: <id_token>`` sets the portal
   session cookies (HttpOnly). Every later call is authenticated by those cookies only.
3. All ``/serviceDAp/api`` calls take ``logHash`` (random) and ``channel=PORTAL``.

This module only depends on aiohttp so it can be exercised outside Home Assistant
(see ``scripts/check_api.py``).
"""

from __future__ import annotations

import logging
import secrets
from datetime import date, timedelta
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

GIGYA_API_KEY = "4_QsDZw2Yu7M9yCRldyBrBhg"
GIGYA_URL = "https://accounts.eu1.gigya.com"
BASE_URL = "https://eniplenitude.com/serviceDAp"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
    ),
    "Origin": "https://eniplenitude.com",
    "Referer": "https://eniplenitude.com/my-eni/",
}
TIMEOUT = aiohttp.ClientTimeout(total=60)

GIGYA_INVALID_CREDENTIALS = (403042, 403047, 403120)
GIGYA_CAPTCHA_REQUIRED = (401020, 401021)

# "type" of analysis/consumptions, as used by the portal's "Monitoraggio energia" page
VIEW_DAY = "0"  # one day, hourly points
VIEW_MONTH = "2"  # one month, daily points
ACTIVE_ENERGY = "Energia attiva"


class PlenitudeError(Exception):
    """Portal unreachable or answered something unexpected."""


class PlenitudeAuthError(PlenitudeError):
    """Credentials rejected or session expired."""


class PlenitudeCaptchaError(PlenitudeAuthError):
    """Gigya wants a captcha: the user must log in once from a browser."""


def parse_active_energy(payload: dict[str, Any]) -> list[tuple[int, float]]:
    """Return ``[(epoch_start, kWh), ...]`` of the active-energy series.

    The portal answers ``status: NO_DATA`` for days not yet available (and for the
    DST-change days), which maps to an empty list.
    """
    if payload.get("status") != "DATA":
        return []
    for activity in payload.get("consumptionList") or []:
        if activity.get("activityName") == ACTIVE_ENERGY or activity.get("activityId") == 0:
            return [
                (int(point["startTime"]), float(point["consumption"]))
                for point in activity.get("data") or []
            ]
    raise PlenitudeError(f"'{ACTIVE_ENERGY}' series missing: has the portal changed?")


class PlenitudeApi:
    """Thin async client. Use a dedicated ClientSession: login clears its cookie jar."""

    def __init__(self, session: aiohttp.ClientSession, username: str, password: str) -> None:
        self._session = session
        self._username = username
        self._password = password

    async def async_login(self) -> None:
        """Open a fresh portal session."""
        self._session.cookie_jar.clear()
        # Sets the gmid cookies: without them getJWT answers 403005 "Unauthorized user".
        await self._gigya(
            "accounts.webSdkBootstrap", pageURL="https://eniplenitude.com/my-eni/", sdk="js_latest"
        )
        login = await self._gigya(
            "accounts.login",
            loginID=self._username,
            password=self._password,
            targetEnv="jssdk",
        )
        session_info = login.get("sessionInfo") or {}
        token = session_info.get("login_token") or session_info.get("cookieValue")
        if not token:
            raise PlenitudeError("Gigya login returned no session token")
        jwt = (await self._gigya("accounts.getJWT", login_token=token)).get("id_token")
        if not jwt:
            raise PlenitudeError("Gigya returned no id_token")

        # Exchange the Gigya JWT for the portal session cookies.
        jwe = await self._request("GET", "/api/c360/getJwe", headers={"JWT": jwt})
        if not jwe.get("url"):
            raise PlenitudeAuthError("Portal refused the Gigya token (getJwe)")
        _LOGGER.debug("Logged in to Eni Plenitude as %s", self._username)

    async def async_get_power_supplies(self) -> list[dict[str, Any]]:
        """Return every electricity supply (POD) of the account with the ids needed for consumption calls."""
        init = await self._request("GET", "/api/c360/init")
        client_code = init["codiceCliente"]
        accounts = await self._request("GET", f"/api/contiServices/conti/{init['codiceContoDefault']}")

        supplies = []
        for account in accounts.get("conto") or []:
            account_id = account["codice"]
            listing = await self._request(
                "GET", f"/api/contiServices/conti/{account_id}/lista-forniture"
            )
            for supply in listing.get("fornitura") or []:
                if supply.get("tipo") != "POWER" or not supply.get("pod"):
                    continue
                pod = supply["pod"]
                profile = await self._request(
                    "POST",
                    f"/api/profilo-energetico-services/{account_id}/users/{client_code}/supplies",
                    json={"pod": pod, "pdr": ""},
                )
                light = next(
                    (
                        p
                        for p in profile.get("fornitureClienteMidori4FE") or []
                        if p.get("clientType") == "LIGHT"
                    ),
                    None,
                )
                if light is None:
                    _LOGGER.warning("POD %s has no consumption profile on the portal, skipped", pod)
                    continue
                supplies.append(
                    {
                        "pod": pod,
                        "account": account_id,
                        "client_code": str(light["clientCode"]),
                        "contract_id": str(light["contractId"]),
                        "house_id": str(light["houseId"]),
                        # epoch of the local midnight of the last day with complete data
                        "last_data": light.get("serviceLastData"),
                        "product": supply.get("prodotto"),
                        "offer_type": supply.get("tipologiaOfferta"),
                    }
                )
        return supplies

    async def async_get_hourly(self, supply: dict[str, Any], day: date) -> list[tuple[int, float]]:
        """Hourly kWh of one local day."""
        return parse_active_energy(await self._consumptions(supply, day, day, VIEW_DAY))

    async def async_get_month_total(self, supply: dict[str, Any], month: date) -> float:
        """Total kWh of the month containing ``month``, from the portal's monthly view."""
        first = month.replace(day=1)
        next_first = (first + timedelta(days=32)).replace(day=1)
        # Same range the portal sends: day 2 -> first of next month (longer ranges give HTTP 400).
        payload = await self._consumptions(supply, first.replace(day=2), next_first, VIEW_MONTH)
        return round(sum(kwh for _, kwh in parse_active_energy(payload)), 3)

    async def _consumptions(
        self, supply: dict[str, Any], start: date, end: date, view: str
    ) -> dict[str, Any]:
        return await self._request(
            "POST",
            f"/api/profilo-energetico-services/{supply['account']}/analysis/consumptions",
            json={
                "clientCode": supply["client_code"],
                "contractId": supply["contract_id"],
                "houseId": supply["house_id"],
                "startTime": f"{start.isoformat()}T00:00:00.000Z",
                "endTime": f"{end.isoformat()}T00:00:00.000Z",
                "type": view,
            },
        )

    async def _gigya(self, method: str, **data: str) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{GIGYA_URL}/{method}",
                data={"apiKey": GIGYA_API_KEY, "format": "json", **data},
                headers=HEADERS,
                timeout=TIMEOUT,
            ) as resp:
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PlenitudeError(f"Gigya {method} failed: {err}") from err

        code = body.get("errorCode", -1)
        if code == 0:
            return body
        message = f"Gigya {method}: {code} {body.get('errorDetails') or body.get('errorMessage')}"
        if code in GIGYA_CAPTCHA_REQUIRED:
            raise PlenitudeCaptchaError(message)
        if code in GIGYA_INVALID_CREDENTIALS:
            raise PlenitudeAuthError(message)
        raise PlenitudeError(message)

    async def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        params = {"logHash": secrets.token_hex(8), "channel": "PORTAL"}
        try:
            async with self._session.request(
                method,
                f"{BASE_URL}{path}",
                params=params,
                json=json,
                headers={**HEADERS, **(headers or {})},
                timeout=TIMEOUT,
            ) as resp:
                if resp.status in (401, 403):
                    raise PlenitudeAuthError(f"{path}: HTTP {resp.status}, session not valid")
                if resp.status >= 400:
                    raise PlenitudeError(f"{path}: HTTP {resp.status} {await resp.text()}"[:300])
                body = await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise PlenitudeError(f"{path} failed: {err}") from err
        if not isinstance(body, dict):
            raise PlenitudeError(f"{path}: unexpected response {body!r}"[:300])
        return body
