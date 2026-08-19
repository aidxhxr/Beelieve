"""Tests for the stochastic colony model (no network, seeded RNG)."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

import pytest
from app.hive import BAND_NAMES, HiveProfile, SimulatedHive

# 07:00 UTC = midday-ish in Almaty (UTC+5): daylight, foragers out.
MIDDAY_UTC = datetime(2026, 6, 15, 7, 0, 0, tzinfo=UTC)

CONTRACT_KEYS = {
    "hive_id",
    "apiary_id",
    "ts",
    "temp_brood_c",
    "temp_ambient_c",
    "humidity_pct",
    "weight_kg",
    "audio_db",
    "audio_bands",
    "co2_ppm",
    "battery_v",
    "fw",
}

OPTIONAL_SENSOR_KEYS = CONTRACT_KEYS - {"hive_id", "apiary_id", "ts", "fw"}


def make_hive(
    profile: HiveProfile = HiveProfile.NORMAL,
    seed: int = 42,
    **kwargs: Any,
) -> SimulatedHive:
    return SimulatedHive(
        "KZ-ALA-0001",
        "apiary-almaty-01",
        profile=profile,
        rng=random.Random(seed),
        **kwargs,
    )


def sample_with_field(
    hive: SimulatedHive, at: datetime, field: str, tries: int = 50
) -> dict[str, Any]:
    """Sample until the (randomly droppable) field is present."""
    for i in range(tries):
        payload = hive.sample(at + timedelta(seconds=i))
        if field in payload:
            return payload
    raise AssertionError(f"field {field!r} never present in {tries} samples")


def collect_bands(
    hive: SimulatedHive, start: datetime, n: int = 25
) -> list[dict[str, float]]:
    out = []
    for i in range(n * 3):
        payload = hive.sample(start + timedelta(seconds=10 * i))
        if "audio_bands" in payload:
            out.append(payload["audio_bands"])
        if len(out) == n:
            break
    assert len(out) == n
    return out


class TestContractShape:
    def test_identity_fields_always_present(self) -> None:
        hive = make_hive()
        for i in range(500):
            payload = hive.sample(MIDDAY_UTC + timedelta(seconds=10 * i))
            assert payload["hive_id"] == "KZ-ALA-0001"
            assert payload["apiary_id"] == "apiary-almaty-01"
            assert "ts" in payload and "fw" in payload
            assert set(payload) <= CONTRACT_KEYS

    def test_ts_is_iso8601_utc_with_z(self) -> None:
        payload = make_hive().sample(MIDDAY_UTC)
        assert payload["ts"].endswith("Z")
        parsed = datetime.fromisoformat(payload["ts"].replace("Z", "+00:00"))
        assert parsed == MIDDAY_UTC

    def test_audio_bands_normalized(self) -> None:
        for bands in collect_bands(make_hive(), MIDDAY_UTC, n=10):
            assert set(bands) == set(BAND_NAMES)
            assert sum(bands.values()) == pytest.approx(1.0, abs=0.01)
            assert all(v >= 0.0 for v in bands.values())

    def test_occasional_sensor_dropouts(self) -> None:
        hive = make_hive(seed=7)
        dropped = 0
        for i in range(2000):
            payload = hive.sample(MIDDAY_UTC + timedelta(seconds=10 * i))
            if OPTIONAL_SENSOR_KEYS - set(payload):
                dropped += 1
        # ~15% of readings should be missing at least one optional field,
        # but the vast majority must still be complete.
        assert 50 < dropped < 1000


class TestDynamics:
    def test_brood_temp_is_regulated(self) -> None:
        hive = make_hive()
        temps = [
            sample_with_field(hive, MIDDAY_UTC + timedelta(minutes=i), "temp_brood_c")[
                "temp_brood_c"
            ]
            for i in range(50)
        ]
        assert all(33.5 <= t <= 36.5 for t in temps)
        assert 34.4 <= mean(temps) <= 35.7

    def test_weight_gains_during_nectar_flow(self) -> None:
        hive = make_hive(seed=3)
        start = datetime(2026, 6, 15, 0, 0, 0, tzinfo=UTC)
        daily: list[list[float]] = [[], [], []]
        for hour in range(72):
            payload = hive.sample(start + timedelta(hours=hour))
            if "weight_kg" in payload:
                daily[hour // 24].append(payload["weight_kg"])
        assert mean(daily[2]) > mean(daily[0])

    def test_battery_drains_over_days(self) -> None:
        hive = make_hive(seed=5)
        first = sample_with_field(hive, MIDDAY_UTC, "battery_v")["battery_v"]
        later = sample_with_field(
            hive, MIDDAY_UTC + timedelta(days=10), "battery_v"
        )["battery_v"]
        assert later < first - 0.02


class TestAcousticProfiles:
    @staticmethod
    def _low_high(bands: list[dict[str, float]]) -> tuple[float, float]:
        low = mean(b["b100_200"] + b["b200_300"] for b in bands)
        high = mean(b["b400_500"] + b["b500_600"] for b in bands)
        return low, high

    def test_normal_hive_is_low_band_weighted(self) -> None:
        low, high = self._low_high(collect_bands(make_hive(), MIDDAY_UTC))
        assert low > high

    def test_pre_swarm_energy_shifts_to_400_600hz(self) -> None:
        hive = make_hive(HiveProfile.PRE_SWARM, seed=11, swarm_ramp_days=0.5)
        # Establish the clock, then jump a day ahead so the ramp completes.
        hive.sample(MIDDAY_UTC)
        hive.sample(MIDDAY_UTC + timedelta(days=1))
        low, high = self._low_high(
            collect_bands(hive, MIDDAY_UTC + timedelta(days=1, minutes=1))
        )
        assert high > low  # energy has migrated into the 400-600 Hz bands

    def test_queenless_hive_shows_low_band_dominance(self) -> None:
        hive = make_hive(HiveProfile.QUEENLESS, seed=13)
        low, _high = self._low_high(collect_bands(hive, MIDDAY_UTC))
        assert low > 0.6


class TestScriptedFleet:
    def test_profile_assignment_and_topics(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.config import Settings
        from app.main import build_nodes, profile_for

        assert profile_for(3) is HiveProfile.PRE_SWARM
        assert profile_for(8) is HiveProfile.PRE_SWARM
        assert profile_for(5) is HiveProfile.QUEENLESS
        assert profile_for(1) is HiveProfile.NORMAL

        monkeypatch.setenv("SIM_NUM_HIVES", "8")
        monkeypatch.setenv("SIM_SEED", "1")
        nodes = build_nodes(Settings())
        assert [n.hive.hive_id for n in nodes] == [
            f"KZ-ALA-{i:04d}" for i in range(1, 9)
        ]
        node = nodes[0]
        assert node.telemetry_topic == "beelieve/apiary-almaty-01/KZ-ALA-0001/telemetry"
        assert node.status_topic == "beelieve/apiary-almaty-01/KZ-ALA-0001/status"
        profiles = [n.hive.profile for n in nodes]
        assert profiles.count(HiveProfile.PRE_SWARM) == 2
        assert profiles.count(HiveProfile.QUEENLESS) == 1
