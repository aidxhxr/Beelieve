"""Pure-logic tests for the model-output parser."""
from __future__ import annotations

from app.parse import ParsedRecommendation, parse_recommendations
from app.prompts import format_recommendations


def test_canonical_format_round_trip() -> None:
    recs = [
        (1, "Split the colony", "Swarm risk is critical. Split today."),
        (3, "Improve ventilation", "Open the entrance fully and add an upper vent."),
    ]
    parsed = parse_recommendations(format_recommendations(recs))
    assert [(r.priority, r.title, r.body) for r in parsed] == recs


def test_single_recommendation() -> None:
    text = (
        "RECOMMENDATION 1\n"
        "PRIORITY: 2\n"
        "TITLE: Feed heavy syrup\n"
        "BODY: Stores are low. Feed 2:1 syrup every evening."
    )
    parsed = parse_recommendations(text)
    assert len(parsed) == 1
    assert parsed[0] == ParsedRecommendation(
        priority=2,
        title="Feed heavy syrup",
        body="Stores are low. Feed 2:1 syrup every evening.",
    )


def test_tolerates_case_dashes_and_markdown() -> None:
    text = (
        "## Recommendation 1\n"
        "**priority - 1**\n"
        "**Title:** Inspect for queen cells\n"
        "**body** – Swarm risk is high. Inspect within 48 hours.\n\n"
        "### 2.\n"
        "Priority: 4\n"
        "title: Recharge the battery\n"
        "Body: Battery is at 3.4 V. Swap it on the next visit."
    )
    parsed = parse_recommendations(text)
    assert len(parsed) == 2
    assert parsed[0].priority == 1
    assert parsed[0].title == "Inspect for queen cells"
    assert "48 hours" in parsed[0].body
    assert parsed[1].priority == 4
    assert parsed[1].title == "Recharge the battery"
    assert "RECOMMENDATION" not in parsed[0].body.upper()


def test_missing_recommendation_headers() -> None:
    text = (
        "PRIORITY: 1\nTITLE: Act now\nBODY: Do the thing immediately.\n"
        "PRIORITY: 5\nTITLE: FYI\nBODY: Just informational."
    )
    parsed = parse_recommendations(text)
    assert [r.priority for r in parsed] == [1, 5]
    assert parsed[0].body == "Do the thing immediately."


def test_priority_clamped_to_range() -> None:
    text = (
        "RECOMMENDATION 1\nPRIORITY: 9\nTITLE: Too urgent\nBODY: Clamp me down.\n\n"
        "RECOMMENDATION 2\nPRIORITY: 0\nTITLE: Too low\nBODY: Clamp me up."
    )
    parsed = parse_recommendations(text)
    assert [r.priority for r in parsed] == [5, 1]


def test_caps_at_three_recommendations() -> None:
    blocks = [
        f"RECOMMENDATION {i}\nPRIORITY: 3\nTITLE: Item {i}\nBODY: Body {i}."
        for i in range(1, 6)
    ]
    parsed = parse_recommendations("\n\n".join(blocks))
    assert len(parsed) == 3


def test_deduplicates_titles() -> None:
    text = (
        "RECOMMENDATION 1\nPRIORITY: 2\nTITLE: Same thing\nBODY: First body.\n\n"
        "RECOMMENDATION 2\nPRIORITY: 2\nTITLE: same THING\nBODY: Duplicate body."
    )
    parsed = parse_recommendations(text)
    assert len(parsed) == 1
    assert parsed[0].body == "First body."


def test_missing_title_derives_from_body() -> None:
    text = "PRIORITY: 2\nBODY: Check the queen today. She may have failed."
    parsed = parse_recommendations(text)
    assert len(parsed) == 1
    assert parsed[0].title == "Check the queen today"
    assert parsed[0].body.startswith("Check the queen today.")


def test_missing_body_uses_title() -> None:
    text = "PRIORITY: 4\nTITLE: Clear snow from the entrance"
    parsed = parse_recommendations(text)
    assert len(parsed) == 1
    assert parsed[0].body == "Clear snow from the entrance"


def test_ignores_preamble_text() -> None:
    text = (
        "Here are my recommendations for this hive:\n\n"
        "RECOMMENDATION 1\nPRIORITY: 1\nTITLE: Requeen now\nBODY: The hive is queenless."
    )
    parsed = parse_recommendations(text)
    assert len(parsed) == 1
    assert parsed[0].title == "Requeen now"


def test_multiline_body_is_joined() -> None:
    text = (
        "RECOMMENDATION 1\nPRIORITY: 3\nTITLE: Ventilate\n"
        "BODY: Open the entrance.\nAdd an upper vent.\nShade the roof."
    )
    parsed = parse_recommendations(text)
    assert parsed[0].body == "Open the entrance. Add an upper vent. Shade the roof."


def test_cyrillic_content() -> None:
    text = (
        "RECOMMENDATION 1\nPRIORITY: 1\nTITLE: Проверьте матку\n"
        "BODY: Семья может быть без матки. Осмотрите гнездо."
    )
    parsed = parse_recommendations(text)
    assert parsed[0].title == "Проверьте матку"
    assert "гнездо" in parsed[0].body


def test_garbage_and_empty_inputs() -> None:
    assert parse_recommendations(None) == []
    assert parse_recommendations("") == []
    assert parse_recommendations("   \n\n  ") == []
    assert parse_recommendations("The bees seem fine, keep watching them.") == []
