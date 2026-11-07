"""Parser tests. Loads api.py by path so Home Assistant is not needed (only aiohttp)."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "plenitude_api", Path(__file__).parents[1] / "custom_components/eniplenitude/api.py"
)
api = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(api)

# Shape of analysis/consumptions (type "0") as returned by the portal, values shortened.
DAY = {
    "status": "DATA",
    "view": "FIFTEEN_DAILY",
    "consumptionList": [
        {"activityId": 0, "activityName": "Energia attiva", "consumption": 0.22, "data": [
            {"consumption": 0.095, "name": "00:00", "startTime": 1793487600, "endTime": 0},
            {"consumption": 0.125, "name": "01:00", "startTime": 1793491200, "endTime": 0},
        ]},
        {"activityId": 1, "activityName": "Refrigerare", "consumption": 1.5, "data": [
            {"consumption": 1.5, "name": "00:00", "startTime": 1793487600, "endTime": 0},
        ]},
    ],
}


def test_parse_active_energy():
    assert api.parse_active_energy(DAY) == [(1793487600, 0.095), (1793491200, 0.125)]


def test_parse_no_data():
    assert api.parse_active_energy({"status": "NO_DATA", "consumptionList": []}) == []


def test_parse_format_changed():
    with pytest.raises(api.PlenitudeError):
        api.parse_active_energy({"status": "DATA", "consumptionList": [{"activityName": "X", "activityId": 9}]})
