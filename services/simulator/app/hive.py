"""Stochastic in-hive sensor model.

Each :class:`SimulatedHive` produces telemetry payloads matching the MQTT
contract in ``docs/ARCHITECTURE.md``, with realistic dynamics:

* brood temperature held near 34.5-35.5 C with a diurnal ambient influence,
* colony weight following a nectar-flow daily gain plus slow drift, with a
  midday "foragers out" dip,
* acoustics whose FFT band distribution shifts toward 400-600 Hz for scripted
  pre-swarm hives and toward low-band dominance for a queenless hive,
* CO2 higher at night when the cluster is tight and fanning stops,
* a slowly draining battery, and occasional missing fields to mimic faults.
"""

from __future__ import annotations

import enum
import math
import random
from datetime import datetime, timezone
from typing import Any, Final

BAND_NAMES: Final[tuple[str, ...]] = (
    "b100_200",
    "b200_300",
    "b300_400",
    "b400_500",
    "b500_600",
)

# Almaty is UTC+5; used only to phase the diurnal cycles realistically.
LOCAL_UTC_OFFSET_HOURS: Final[float] = 5.0

_BANDS_NORMAL: Final[tuple[float, ...]] = (0.30, 0.24, 0.20, 0.15, 0.11)
# Pre-swarm acoustics: energy migrates into the 400-600 Hz bands.
_BANDS_PRE_SWARM: Final[tuple[float, ...]] = (0.14, 0.15, 0.19, 0.28, 0.24)
# Queenless colonies show a low-frequency "roar".
_BANDS_QUEENLESS: Final[tuple[float, ...]] = (0.46, 0.28, 0.13, 0.08, 0.05)

# Probability an individual optional sensor field is missing in one reading.
_FIELD_DROPOUT_P: Final[float] = 0.02
# Probability the whole audio subsystem glitches out for one reading.
_AUDIO_GLITCH_P: Final[float] = 0.01


class HiveProfile(enum.Enum):
    NORMAL = "normal"
    PRE_SWARM = "pre_swarm"
    QUEENLESS = "queenless"


class SimulatedHive:
    """One hive's persistent state plus a ``sample()`` telemetry generator."""

    def __init__(
        self,
        hive_id: str,
        apiary_id: str,
        *,
        profile: HiveProfile = HiveProfile.NORMAL,
        rng: random.Random | None = None,
        fw: str = "1.4.2",
        swarm_ramp_days: float = 2.0,
    ) -> None:
        self.hive_id = hive_id
        self.apiary_id = apiary_id
        self.profile = profile
        self.fw = fw
        self._rng = rng if rng is not None else random.Random()
        r = self._rng

        # Per-hive constants (colonies differ).
        self._ambient_mean_c = r.gauss(23.0, 1.5)
        self._ambient_amplitude_c = r.uniform(6.0, 9.0)
        self._base_weight_kg = r.uniform(32.0, 48.0)
        self._nectar_flow_kg_day = r.uniform(0.4, 1.2)
        self._audio_base_db = r.uniform(44.0, 50.0)

        # Slowly evolving state.
        self._stored_kg = 0.0  # accumulated nectar-flow gain / consumption
        self._battery_v = r.uniform(4.05, 4.20)
        self._battery_drain_v_day = r.uniform(0.004, 0.008)
        self._swarm_progress = 0.0  # 0 -> 1 over swarm_ramp_days for PRE_SWARM
        self._swarm_ramp_days = max(swarm_ramp_days, 0.1)
        self._last_sample_at: datetime | None = None

    # ── public API ───────────────────────────────────────────────────

    def sample(self, now: datetime | None = None) -> dict[str, Any]:
        """Generate one telemetry payload (contract-shaped JSON-able dict)."""
        if now is None:
            now = datetime.now(timezone.utc)
        elif now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        dt_days = self._advance_clock(now)
        local_h = (
            now.hour + now.minute / 60.0 + now.second / 3600.0 + LOCAL_UTC_OFFSET_HOURS
        ) % 24.0
        daylight = _daylight_factor(local_h)

        ambient_c = self._ambient(local_h)
        brood_c = self._brood_temp(ambient_c)
        weight_kg = self._weight(local_h, daylight, dt_days)
        humidity = self._humidity(ambient_c)
        audio_db, audio_bands = self._audio(daylight)
        co2_ppm = self._co2(daylight)
        battery_v = self._battery(dt_days)

        payload: dict[str, Any] = {
            "hive_id": self.hive_id,
            "apiary_id": self.apiary_id,
            "ts": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "temp_brood_c": round(brood_c, 2),
            "temp_ambient_c": round(ambient_c, 2),
            "humidity_pct": round(humidity, 1),
            "weight_kg": round(weight_kg, 2),
            "audio_db": round(audio_db, 1),
            "audio_bands": audio_bands,
            "co2_ppm": round(co2_ppm),
            "battery_v": round(battery_v, 3),
            "fw": self.fw,
        }
        self._apply_sensor_faults(payload)
        return payload

    # ── time keeping ─────────────────────────────────────────────────

    def _advance_clock(self, now: datetime) -> float:
        """Return elapsed days since the previous sample (0 on the first)."""
        if self._last_sample_at is None:
            dt_days = 0.0
        else:
            dt_days = max((now - self._last_sample_at).total_seconds(), 0.0) / 86_400.0
        self._last_sample_at = now
        if self.profile is HiveProfile.PRE_SWARM:
            self._swarm_progress = min(
                1.0, self._swarm_progress + dt_days / self._swarm_ramp_days
            )
        return dt_days

    # ── physical models ──────────────────────────────────────────────

    def _ambient(self, local_h: float) -> float:
        """Diurnal sine peaking mid-afternoon (~15:00 local)."""
        diurnal = math.sin(2.0 * math.pi * (local_h - 9.0) / 24.0)
        return (
            self._ambient_mean_c
            + self._ambient_amplitude_c * diurnal
            + self._rng.gauss(0.0, 0.6)
        )

    def _brood_temp(self, ambient_c: float) -> float:
        """Healthy colonies thermoregulate tightly; queenless ones do not."""
        if self.profile is HiveProfile.QUEENLESS:
            target = 34.2 + 0.10 * (ambient_c - 25.0)
            noise_sd = 0.35
        else:
            target = 35.0 + 0.03 * (ambient_c - 25.0)
            noise_sd = 0.12 + 0.10 * self._swarm_progress
        target = _clamp(target, 33.5, 35.6)
        if self.profile is not HiveProfile.QUEENLESS:
            target = _clamp(target, 34.5, 35.5)
        return target + self._rng.gauss(0.0, noise_sd)

    def _weight(self, local_h: float, daylight: float, dt_days: float) -> float:
        """Nectar-flow gain by day, consumption by night, midday forager dip."""
        taper = max(0.2, 1.0 - self._stored_kg / 30.0)  # flow slows as supers fill
        flow = self._nectar_flow_kg_day * taper
        if self.profile is HiveProfile.PRE_SWARM:
            flow *= 1.0 - 0.6 * self._swarm_progress  # foraging stalls pre-swarm
        gain_per_day = flow * 2.2 * daylight - 0.15 * (1.0 - daylight)
        if self.profile is HiveProfile.QUEENLESS:
            gain_per_day -= 0.25  # dwindling colony slowly loses mass
        self._stored_kg += gain_per_day * dt_days
        self._stored_kg = _clamp(self._stored_kg, -10.0, 35.0)

        forager_dip = -0.6 * _bell(local_h, center=12.0, width=3.0)
        return (
            self._base_weight_kg
            + self._stored_kg
            + forager_dip
            + self._rng.gauss(0.0, 0.03)
        )

    def _humidity(self, ambient_c: float) -> float:
        base = 60.0 - 0.8 * (ambient_c - 24.0)
        if self.profile is HiveProfile.QUEENLESS:
            base += 4.0  # poorer fanning / ventilation
        return _clamp(base + self._rng.gauss(0.0, 2.0), 30.0, 95.0)

    def _audio(self, daylight: float) -> tuple[float, dict[str, float]]:
        """Overall level plus the normalized FFT band distribution."""
        level = self._audio_base_db + 6.0 * daylight + self._rng.gauss(0.0, 1.5)
        if self.profile is HiveProfile.PRE_SWARM:
            level += 5.0 * self._swarm_progress
            if self._rng.random() < 0.05:  # occasional queen "piping"
                level += 6.0
            weights = _blend(_BANDS_NORMAL, _BANDS_PRE_SWARM, self._swarm_progress)
        elif self.profile is HiveProfile.QUEENLESS:
            level -= 3.0 + self._rng.gauss(0.0, 1.0)
            weights = list(_BANDS_QUEENLESS)
        else:
            weights = list(_BANDS_NORMAL)

        noisy = [w * math.exp(self._rng.gauss(0.0, 0.08)) for w in weights]
        total = sum(noisy)
        bands = {name: round(w / total, 3) for name, w in zip(BAND_NAMES, noisy)}
        return _clamp(level, 30.0, 90.0), bands

    def _co2(self, daylight: float) -> float:
        base = 3800.0 + 900.0 * (1.0 - daylight)  # clustered + no fanning at night
        if self.profile is HiveProfile.QUEENLESS:
            base -= 800.0  # less brood metabolism
        return _clamp(base + self._rng.gauss(0.0, 180.0), 400.0, 12_000.0)

    def _battery(self, dt_days: float) -> float:
        self._battery_v -= self._battery_drain_v_day * dt_days
        self._battery_v = max(self._battery_v, 3.20)
        return self._battery_v + self._rng.gauss(0.0, 0.004)

    # ── sensor faults ────────────────────────────────────────────────

    def _apply_sensor_faults(self, payload: dict[str, Any]) -> None:
        """Occasionally drop optional fields; required identity fields never drop."""
        r = self._rng
        if r.random() < _AUDIO_GLITCH_P:
            payload.pop("audio_db", None)
            payload.pop("audio_bands", None)
        for field in (
            "temp_brood_c",
            "temp_ambient_c",
            "humidity_pct",
            "weight_kg",
            "audio_db",
            "audio_bands",
            "co2_ppm",
            "battery_v",
        ):
            if field in payload and r.random() < _FIELD_DROPOUT_P:
                del payload[field]


# ── helpers ──────────────────────────────────────────────────────────


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _daylight_factor(local_h: float) -> float:
    """0 at night, ~1 around midday; smooth half-sine between 06:00 and 21:00."""
    sunrise, sunset = 6.0, 21.0
    if local_h <= sunrise or local_h >= sunset:
        return 0.0
    return math.sin(math.pi * (local_h - sunrise) / (sunset - sunrise))


def _bell(x: float, *, center: float, width: float) -> float:
    """Gaussian bump used for the transient midday forager dip."""
    return math.exp(-((x - center) ** 2) / (2.0 * width**2))


def _blend(a: tuple[float, ...], b: tuple[float, ...], t: float) -> list[float]:
    return [ai * (1.0 - t) + bi * t for ai, bi in zip(a, b)]
