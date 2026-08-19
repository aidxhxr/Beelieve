"""Pure-logic tests for prompt construction (and the fallback that shares its context)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.fallback import generate_fallback
from app.prompts import (
    DEFAULT_LOCALE,
    MAX_RECOMMENDATIONS,
    SUPPORTED_LOCALES,
    build_messages,
    build_system_prompt,
    build_user_prompt,
    render_context,
)


def full_context() -> dict[str, Any]:
    return {
        "hive": {
            "id": "KZ-ALA-0042",
            "name": "Hive 42",
            "hive_type": "dadant",
            "queen_year": 2025,
            "frames": 12,
        },
        "apiary": {
            "id": "apiary-almaty-01",
            "name": "Almaty foothills apiary",
            "region": "Almaty region",
            "latitude": 43.25,
            "longitude": 76.95,
        },
        "reading": {
            "time": "2026-08-18T12:00:00Z",
            "temp_brood_c": 34.8,
            "temp_ambient_c": 27.1,
            "humidity_pct": 58.2,
            "weight_kg": 42.35,
            "audio_db": 52.1,
            "co2_ppm": 4200,
            "battery_v": 3.91,
        },
        "prediction": {
            "time": "2026-08-18T12:00:05Z",
            "model_version": "lgbm-2026.08",
            "swarm_risk": 0.87,
            "health_score": 0.62,
            "is_anomaly": True,
            "anomaly_kind": "queenless_acoustic",
            "anomaly_score": 0.91,
        },
        "alerts": [
            {
                "time": "2026-08-18T09:00:00Z",
                "severity": "critical",
                "kind": "swarm_imminent",
                "message": "Swarm risk sustained above 0.85",
            }
        ],
    }


# ── system prompt ────────────────────────────────────────────────────


def test_system_prompt_contains_format_markers() -> None:
    prompt = build_system_prompt("en")
    for marker in ("RECOMMENDATION", "PRIORITY", "TITLE", "BODY"):
        assert marker in prompt
    assert str(MAX_RECOMMENDATIONS) in prompt


def test_system_prompt_locale_instruction() -> None:
    assert "English" in build_system_prompt("en")
    assert "русский" in build_system_prompt("ru")
    assert "қазақ" in build_system_prompt("kk")


def test_system_prompt_unknown_locale_falls_back_to_default() -> None:
    assert build_system_prompt("de") == build_system_prompt(DEFAULT_LOCALE)


def test_supported_locales() -> None:
    assert set(SUPPORTED_LOCALES) == {"en", "ru", "kk"}


# ── user prompt / context rendering ──────────────────────────────────


def test_render_context_includes_all_sections() -> None:
    rendered = render_context(full_context())
    assert "KZ-ALA-0042" in rendered
    assert "Almaty foothills apiary" in rendered
    assert "34.8 C" in rendered
    assert "42.35 kg" in rendered
    assert "4200 ppm" in rendered
    assert "swarm risk: 0.87" in rendered
    assert "queenless_acoustic" in rendered
    assert "swarm_imminent" in rendered
    assert "lgbm-2026.08" in rendered


def test_render_context_handles_missing_data() -> None:
    ctx = full_context()
    ctx["reading"] = None
    ctx["prediction"] = None
    ctx["alerts"] = []
    rendered = render_context(ctx)
    assert "no sensor readings available" in rendered
    assert "no predictions available" in rendered
    assert rendered.rstrip().endswith("none")


def test_render_context_handles_partial_reading() -> None:
    ctx = full_context()
    ctx["reading"]["weight_kg"] = None
    ctx["hive"]["queen_year"] = None
    rendered = render_context(ctx)
    assert "weight: n/a" in rendered
    assert "queen year: n/a" in rendered


def test_build_user_prompt_ends_with_instruction() -> None:
    prompt = build_user_prompt(full_context())
    assert prompt.startswith("### Hive")
    assert prompt.rstrip().endswith("following the required format.")


def test_build_messages_shape() -> None:
    messages = build_messages(full_context(), "ru")
    assert [m["role"] for m in messages] == ["system", "user"]
    assert "русский" in messages[0]["content"]
    assert "KZ-ALA-0042" in messages[1]["content"]


# ── fallback consumes the same context ───────────────────────────────


def test_fallback_flags_queenless_and_swarm_first() -> None:
    recs = generate_fallback(full_context(), "en")
    assert 1 <= len(recs) <= MAX_RECOMMENDATIONS
    assert recs[0].priority == 1
    titles = " ".join(r.title.lower() for r in recs)
    assert "queenless" in titles
    assert "swarm" in titles


def test_fallback_localized() -> None:
    recs_ru = generate_fallback(full_context(), "ru")
    recs_kk = generate_fallback(full_context(), "kk")
    assert any("улей" in r.title.lower() or "провер" in r.title.lower() for r in recs_ru)
    assert any("тексер" in r.title.lower() for r in recs_kk)


def test_fallback_healthy_hive_gets_seasonal_advice() -> None:
    ctx = full_context()
    ctx["prediction"].update(
        {"swarm_risk": 0.05, "health_score": 0.9, "is_anomaly": False,
         "anomaly_kind": "none", "anomaly_score": 0.0}
    )
    ctx["alerts"] = []
    winter = datetime(2026, 1, 15, tzinfo=UTC)
    recs = generate_fallback(ctx, "en", now=winter)
    assert len(recs) == 1
    assert recs[0].priority == 5
    assert "winter" in (recs[0].title + recs[0].body).lower()


def test_fallback_no_data_at_all_still_answers() -> None:
    ctx = {"hive": {"id": "X"}, "apiary": {}, "reading": None,
           "prediction": None, "alerts": []}
    recs = generate_fallback(ctx, "en", now=datetime(2026, 7, 1, tzinfo=UTC))
    assert len(recs) == 1
    assert recs[0].priority == 5


def test_fallback_deterministic() -> None:
    now = datetime(2026, 8, 18, tzinfo=UTC)
    assert generate_fallback(full_context(), "en", now=now) == generate_fallback(
        full_context(), "en", now=now
    )
