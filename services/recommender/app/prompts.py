"""Prompt construction for the Beelieve Mistral-7B advisor.

This module is deliberately stdlib-only: ``finetune/dataset.py`` imports it so
that the fine-tuning data uses *exactly* the same system prompt and context
rendering as the serving path. Do not import serving dependencies here.

The context dict shape shared between ``app.db`` and ``finetune.dataset``::

    {
      "hive":      {"id", "name", "hive_type", "queen_year", "frames"},
      "apiary":    {"id", "name", "region", "latitude", "longitude"},
      "reading":   {"time", "temp_brood_c", "temp_ambient_c", "humidity_pct",
                    "weight_kg", "audio_db", "co2_ppm", "battery_v"} | None,
      "prediction":{"time", "model_version", "swarm_risk", "health_score",
                    "is_anomaly", "anomaly_kind", "anomaly_score"} | None,
      "alerts":    [{"time", "severity", "kind", "message"}, ...],
    }

Timestamps are ISO-8601 strings; missing numeric fields are ``None``.
"""
from __future__ import annotations

from typing import Any, Iterable

SUPPORTED_LOCALES: dict[str, str] = {
    "en": "English",
    "ru": "Russian (русский язык)",
    "kk": "Kazakh (қазақ тілі)",
}
DEFAULT_LOCALE = "en"

MAX_RECOMMENDATIONS = 3

SYSTEM_PROMPT_TEMPLATE = """\
You are Beelieve Advisor, an expert precision-beekeeping assistant fine-tuned on \
thousands of real hive interventions. You are given one hive's metadata, its latest \
telemetry snapshot, the latest ML prediction (swarm risk, health score, anomaly \
detection) and any alerts from the last 72 hours. Beekeeping context: continental \
climate (Kazakhstan and similar regions) with hot dry summers and severe winters.

Reply with 1 to {max_recs} recommendations, most urgent first, in EXACTLY this format:

RECOMMENDATION 1
PRIORITY: <integer 1-5; 1 = act immediately, 5 = informational>
TITLE: <at most 10 words, imperative>
BODY: <2-5 sentences; concrete, actionable, grounded in the data provided>

Rules:
- Keep the marker keywords RECOMMENDATION, PRIORITY, TITLE and BODY in English \
uppercase exactly as shown, each on its own line.
- Write the TITLE and BODY text in {language}.
- Never invent sensor values that are not in the input; if a value is missing, say so.
- Never recommend chemical treatments during an active honey flow without a withdrawal warning.
- Output nothing before the first RECOMMENDATION line and nothing after the last BODY."""

USER_PROMPT_INSTRUCTION = (
    "Analyse this hive and give your recommendations now, following the required format."
)


def build_system_prompt(locale: str) -> str:
    """System prompt with the strict output format and locale instruction."""
    language = SUPPORTED_LOCALES.get(locale, SUPPORTED_LOCALES[DEFAULT_LOCALE])
    return SYSTEM_PROMPT_TEMPLATE.format(max_recs=MAX_RECOMMENDATIONS, language=language)


def _fmt(value: Any, unit: str = "", digits: int | None = None) -> str:
    if value is None:
        return "n/a"
    if digits is not None and isinstance(value, (int, float)):
        value = f"{value:.{digits}f}"
    return f"{value}{unit}"


def render_context(ctx: dict[str, Any]) -> str:
    """Render the shared context dict into the advisor's user-prompt block."""
    hive: dict[str, Any] = ctx.get("hive") or {}
    apiary: dict[str, Any] = ctx.get("apiary") or {}
    reading: dict[str, Any] | None = ctx.get("reading")
    prediction: dict[str, Any] | None = ctx.get("prediction")
    alerts: Iterable[dict[str, Any]] = ctx.get("alerts") or []

    lines: list[str] = ["### Hive"]
    lines.append(
        f"id: {_fmt(hive.get('id'))} | name: {_fmt(hive.get('name'))}"
        f" | type: {_fmt(hive.get('hive_type'))}"
        f" | queen year: {_fmt(hive.get('queen_year'))}"
        f" | frames: {_fmt(hive.get('frames'))}"
    )
    lines.append(
        f"apiary: {_fmt(apiary.get('name'))} ({_fmt(apiary.get('id'))})"
        f" | region: {_fmt(apiary.get('region'))}"
        f" | lat: {_fmt(apiary.get('latitude'))} | lon: {_fmt(apiary.get('longitude'))}"
    )

    lines.append("")
    if reading:
        lines.append(f"### Latest telemetry ({_fmt(reading.get('time'))})")
        lines.append(
            f"brood temp: {_fmt(reading.get('temp_brood_c'), ' C', 1)}"
            f" | ambient temp: {_fmt(reading.get('temp_ambient_c'), ' C', 1)}"
            f" | humidity: {_fmt(reading.get('humidity_pct'), ' %', 1)}"
        )
        lines.append(
            f"weight: {_fmt(reading.get('weight_kg'), ' kg', 2)}"
            f" | audio: {_fmt(reading.get('audio_db'), ' dB', 1)}"
            f" | CO2: {_fmt(reading.get('co2_ppm'), ' ppm', 0)}"
            f" | battery: {_fmt(reading.get('battery_v'), ' V', 2)}"
        )
    else:
        lines.append("### Latest telemetry")
        lines.append("no sensor readings available")

    lines.append("")
    if prediction:
        lines.append(
            f"### Latest ML prediction ({_fmt(prediction.get('time'))},"
            f" model {_fmt(prediction.get('model_version'))})"
        )
        anomaly = (
            f"{prediction.get('anomaly_kind') or 'none'}"
            f" (score {_fmt(prediction.get('anomaly_score'), '', 2)})"
            if prediction.get("is_anomaly")
            else "none"
        )
        lines.append(
            f"swarm risk: {_fmt(prediction.get('swarm_risk'), '', 2)}"
            f" | health score: {_fmt(prediction.get('health_score'), '', 2)}"
            f" | anomaly: {anomaly}"
        )
    else:
        lines.append("### Latest ML prediction")
        lines.append("no predictions available")

    lines.append("")
    lines.append("### Alerts in the last 72 h")
    alert_lines = [
        f"- [{a.get('severity', 'info')}] {a.get('kind', 'unknown')}"
        f" @ {_fmt(a.get('time'))}: {a.get('message', '')}".rstrip(": ")
        for a in alerts
    ]
    lines.extend(alert_lines or ["none"])
    return "\n".join(lines)


def build_user_prompt(ctx: dict[str, Any]) -> str:
    """Full user message: rendered context plus the task instruction."""
    return f"{render_context(ctx)}\n\n{USER_PROMPT_INSTRUCTION}"


def build_messages(ctx: dict[str, Any], locale: str) -> list[dict[str, str]]:
    """Chat messages for HF ``chat_completion`` (and for the training dataset)."""
    return [
        {"role": "system", "content": build_system_prompt(locale)},
        {"role": "user", "content": build_user_prompt(ctx)},
    ]


def format_recommendations(recs: Iterable[tuple[int, str, str]]) -> str:
    """Render (priority, title, body) triples in the strict output format.

    This is the canonical assistant-side format used both to build the
    fine-tuning targets and in tests as the parser round-trip reference.
    """
    blocks: list[str] = []
    for i, (priority, title, body) in enumerate(recs, start=1):
        blocks.append(
            f"RECOMMENDATION {i}\nPRIORITY: {priority}\nTITLE: {title}\nBODY: {body}"
        )
    return "\n\n".join(blocks)
