"""Build the instruction-tuning dataset for the Beelieve advisor.

Curated seed set (~40 hand-written beekeeper-advice examples across swarm
prevention, queenless remediation, varroa treatment timing, feeding, wintering
in a continental/Kazakhstan climate, ventilation/condensation and honey-flow
management, in en/ru/kk) is programmatically augmented — numbers jittered,
hive ids / apiaries / dates varied per season — into ~1k chat-format JSONL
examples (system/user/assistant), sharing the exact runtime prompt via
``app.prompts``.

Usage (from services/recommender):
    python -m finetune.dataset --n 1000 --out finetune/data/train.jsonl \
        --eval-out finetune/data/eval.jsonl --seed 42
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.prompts import build_system_prompt, build_user_prompt, format_recommendations

# ── seed model ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Seed:
    """One curated advice example.

    ``ctx`` holds flat context values (see ``_flat_defaults``); ``recs`` are
    (priority, title, body) templates whose bodies may reference flat context
    keys via ``str.format`` so augmentation keeps prose and telemetry in sync.
    """

    topic: str
    lang: str  # en | ru | kk
    season: str  # spring | summer | autumn | winter
    ctx: dict[str, Any]
    recs: tuple[tuple[int, str, str], ...]
    alerts: tuple[tuple[str, str, str], ...] = field(default_factory=tuple)
    # each alert: (severity, kind, message)


_REGIONS: list[tuple[str, str, str, float, float]] = [
    ("ALA", "Almaty region", "Almaty foothills apiary", 43.25, 76.95),
    ("VKO", "East Kazakhstan", "Katon-Karagay apiary", 49.17, 85.62),
    ("AST", "Akmola region", "Burabay apiary", 53.08, 70.30),
    ("SHY", "Turkistan region", "Sayram apiary", 42.30, 69.60),
    ("PAV", "Pavlodar region", "Bayanaul apiary", 50.80, 75.70),
    ("KAR", "Karaganda region", "Karkaraly apiary", 49.40, 75.45),
]

_SEASON_MONTHS: dict[str, list[int]] = {
    "spring": [4, 5],
    "summer": [6, 7, 8],
    "autumn": [9, 10],
    "winter": [11, 12, 1, 2, 3],
}

_HIVE_TYPES = ["dadant", "langstroth", "lezhak"]

# (mode, magnitude): "add" = uniform additive, "mul" = uniform multiplicative
_JITTER: dict[str, tuple[str, float]] = {
    "temp_brood_c": ("add", 0.5),
    "temp_ambient_c": ("add", 1.5),
    "humidity_pct": ("add", 3.0),
    "weight_kg": ("mul", 0.06),
    "audio_db": ("add", 2.0),
    "co2_ppm": ("mul", 0.08),
    "battery_v": ("add", 0.04),
    "swarm_risk": ("add", 0.04),
    "health_score": ("add", 0.04),
    "anomaly_score": ("add", 0.03),
}

_CLAMPS: dict[str, tuple[float, float]] = {
    "humidity_pct": (20.0, 98.0),
    "co2_ppm": (400.0, 12000.0),
    "battery_v": (3.0, 4.2),
    "swarm_risk": (0.0, 1.0),
    "health_score": (0.0, 1.0),
    "anomaly_score": (0.0, 1.0),
}

_ROUND: dict[str, int] = {
    "temp_brood_c": 1,
    "temp_ambient_c": 1,
    "humidity_pct": 1,
    "weight_kg": 2,
    "audio_db": 1,
    "co2_ppm": 0,
    "battery_v": 2,
    "swarm_risk": 2,
    "health_score": 2,
    "anomaly_score": 2,
}


def _flat_defaults() -> dict[str, Any]:
    return {
        "temp_brood_c": 34.6,
        "temp_ambient_c": 22.0,
        "humidity_pct": 55.0,
        "weight_kg": 38.0,
        "audio_db": 48.0,
        "co2_ppm": 2800,
        "battery_v": 3.9,
        "swarm_risk": 0.1,
        "health_score": 0.85,
        "is_anomaly": False,
        "anomaly_kind": "none",
        "anomaly_score": 0.0,
        "model_version": "lgbm-2026.08",
    }


# ── curated seeds (~40) ──────────────────────────────────────────────


def _s(
    topic: str,
    lang: str,
    season: str,
    ctx: dict[str, Any],
    recs: list[tuple[int, str, str]],
    alerts: list[tuple[str, str, str]] | None = None,
) -> Seed:
    return Seed(topic, lang, season, ctx, tuple(recs), tuple(alerts or []))


SEEDS: list[Seed] = [
    # ── swarm prevention ─────────────────────────────────────────────
    _s(
        "swarm", "en", "spring",
        {"swarm_risk": 0.82, "health_score": 0.78, "temp_brood_c": 35.4,
         "weight_kg": 34.0, "audio_db": 56.0, "humidity_pct": 62.0},
        [
            (1, "Inspect for queen cells and split if capped",
             "Swarm risk is {swarm_risk:.0%} with a hot, crowded brood nest at "
             "{temp_brood_c:.1f} C and rising colony noise. Inspect within 48 hours; "
             "if you find capped queen cells, make an artificial swarm: move the old "
             "queen with two brood frames and stores into a new box on the old stand."),
            (2, "Relieve congestion in the brood nest",
             "Add a super or rotate two empty drawn combs into the brood nest so the "
             "queen has room to lay. Remove any burr comb bridging the frames and make "
             "sure the entrance is fully open for this season."),
        ],
    ),
    _s(
        "swarm", "en", "spring",
        {"swarm_risk": 0.55, "health_score": 0.82, "weight_kg": 31.0,
         "temp_brood_c": 35.1, "humidity_pct": 58.0},
        [
            (2, "Give the colony room before swarm season peaks",
             "Swarm risk is moderate at {swarm_risk:.0%}. Stay ahead of it: add a super "
             "now, checkerboard one or two frames of foundation into the edge of the "
             "brood nest, and verify the queen is not honey-bound."),
            (3, "Schedule weekly queen-cell checks",
             "Until mid-June, check for queen cups with eggs every 7 days. Charged cups "
             "are your earliest reliable swarming signal and give you a full week to "
             "act before cells are capped."),
        ],
    ),
    _s(
        "swarm", "en", "summer",
        {"swarm_risk": 0.75, "health_score": 0.8, "weight_kg": 48.0,
         "temp_brood_c": 35.6, "temp_ambient_c": 30.0, "audio_db": 58.0},
        [
            (1, "Add super space immediately to protect the flow",
             "Swarm risk is {swarm_risk:.0%} during an active flow at {weight_kg:.1f} kg "
             "— losing a swarm now costs you the harvest. Add an empty super under the "
             "top one today and open up the brood nest with drawn comb."),
            (2, "Boost ventilation to cool the nest",
             "Ambient is {temp_ambient_c:.1f} C and bees fanning to cool the hive adds "
             "to swarm pressure. Stagger the boxes slightly or add a screened bottom "
             "board, and provide shade during the afternoon peak."),
        ],
    ),
    _s(
        "swarm", "en", "spring",
        {"swarm_risk": 0.88, "health_score": 0.74, "audio_db": 60.0,
         "temp_brood_c": 35.8, "weight_kg": 36.0},
        [
            (1, "Perform an artificial swarm today",
             "Swarm risk is {swarm_risk:.0%} and a swarm-imminent alert fired — the "
             "colony is likely days from leaving. Split it now: put the queen, two "
             "frames of open brood and stores on the old stand, move the parent hive "
             "aside, and leave one good queen cell in the parent."),
        ],
        alerts=[("critical", "swarm_imminent",
                 "Swarm risk 0.88 sustained for 6h; acoustic pattern pre-swarm")],
    ),
    _s(
        "swarm", "ru", "spring",
        {"swarm_risk": 0.8, "health_score": 0.76, "temp_brood_c": 35.5,
         "weight_kg": 33.0, "audio_db": 57.0},
        [
            (1, "Осмотрите семью и сделайте отводок при маточниках",
             "Риск роения {swarm_risk:.0%}, гнездо перегрето до {temp_brood_c:.1f} C, "
             "гул усиливается. Осмотрите семью в ближайшие сутки-двое: при печатных "
             "маточниках сделайте отводок со старой маткой на старом месте."),
            (2, "Расширьте гнездо",
             "Поставьте магазинную надставку и подставьте в край гнезда одну-две рамки "
             "суши, чтобы матке было куда сеять. Проверьте, не забито ли гнездо мёдом."),
        ],
    ),
    _s(
        "swarm", "kk", "spring",
        {"swarm_risk": 0.78, "health_score": 0.77, "temp_brood_c": 35.4,
         "weight_kg": 32.0, "audio_db": 56.0},
        [
            (1, "Ұяны тексеріп, аналық ұяшық болса бөлінді жасаңыз",
             "Үйірлену қаупі {swarm_risk:.0%}, ұя қызып тұр ({temp_brood_c:.1f} C). "
             "Бір-екі күнде ұяны тексеріңіз: жабық аналық ұяшықтар табылса, ескі "
             "аналықпен бөлінді жасап, ескі орнына қойыңыз."),
            (2, "Ұяға кеңістік беріңіз",
             "Қосымша корпус қойып, ұя шетіне бір-екі бос ұяшықты жақтау салыңыз. "
             "Аналыққа жұмыртқалайтын орын жеткілікті екенін тексеріңіз."),
        ],
    ),
    # ── queenless remediation ────────────────────────────────────────
    _s(
        "queenless", "en", "summer",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.91,
         "health_score": 0.62, "swarm_risk": 0.08, "audio_db": 44.0,
         "temp_brood_c": 33.9},
        [
            (1, "Confirm queen status within 24 hours",
             "The acoustic anomaly score of {anomaly_score:.2f} matches a queenless "
             "roar. Open the hive and look for eggs and day-old larvae; their absence "
             "plus emergency cells confirms queenlessness. Note whether the bees are "
             "unusually agitated at the entrance."),
            (2, "Give a test frame if no eggs are found",
             "If you find no eggs, insert a frame of eggs and young larvae from a "
             "strong colony. Queen cells started on it within 3 days confirm the loss; "
             "then either let them requeen or introduce a caged mated queen for speed."),
        ],
    ),
    _s(
        "queenless", "en", "spring",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.84,
         "health_score": 0.55, "temp_brood_c": 32.6, "weight_kg": 24.0},
        [
            (1, "Requeen or combine this weak colony now",
             "Queenless signature (score {anomaly_score:.2f}) plus brood temperature "
             "sliding to {temp_brood_c:.1f} C suggests brood is no longer being "
             "maintained. In spring a weak queenless colony rarely recovers alone: "
             "introduce a mated queen, or newspaper-combine it with a queenright nuc."),
            (3, "Watch for laying workers",
             "If the colony has been queenless more than two weeks you may see "
             "multiple eggs per cell on cell walls. Laying-worker colonies reject new "
             "queens; combining with a strong queenright colony is then the reliable fix."),
        ],
    ),
    _s(
        "queenless", "en", "autumn",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.88,
         "health_score": 0.5, "temp_brood_c": 30.5, "weight_kg": 20.0,
         "temp_ambient_c": 9.0},
        [
            (1, "Combine with a queenright colony before winter",
             "A queenless colony this late in autumn cannot raise and mate a new queen "
             "before the continental winter. Newspaper-combine it with your strongest "
             "queenright colony within the week so the bees and stores are not lost."),
        ],
        alerts=[("critical", "queenless",
                 "Acoustic queenless signature persisting 48h")],
    ),
    _s(
        "queenless", "en", "summer",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.79,
         "health_score": 0.68, "audio_db": 43.0, "swarm_risk": 0.05},
        [
            (1, "Inspect brood frames for eggs today",
             "The low-frequency queenless hum has persisted and foraging traffic looks "
             "reduced. Check for eggs today: a recently emerged virgin queen can also "
             "produce this pattern, so if you find no eggs but see a torn-open queen "
             "cell, give the colony 10-14 days for her to mate before intervening."),
            (4, "Recheck acoustics after intervention",
             "Whatever you find, keep the sensor node running: the band profile should "
             "normalise within 48 hours of a laying queen being present, which gives "
             "you remote confirmation without reopening the hive."),
        ],
        alerts=[("warning", "queenless", "Low-band acoustic ratio elevated for 24h")],
    ),
    _s(
        "queenless", "ru", "summer",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.9,
         "health_score": 0.6, "audio_db": 45.0},
        [
            (1, "Проверьте наличие матки в течение суток",
             "Акустическая аномалия {anomaly_score:.2f} характерна для безматочной "
             "семьи. Осмотрите гнездо: ищите яйца и однодневных личинок, свищевые "
             "маточники. Если яиц нет — дайте контрольную рамку с яйцами из сильной "
             "семьи."),
            (2, "Подсадите плодную матку при подтверждении",
             "Если безматочность подтвердится, подсадите плодную матку в клеточке — "
             "летом семья быстро слабеет без расплода. Свищевые маточники при этом "
             "сорвите."),
        ],
    ),
    _s(
        "queenless", "kk", "summer",
        {"is_anomaly": True, "anomaly_kind": "queenless_acoustic", "anomaly_score": 0.89,
         "health_score": 0.6, "audio_db": 45.0},
        [
            (1, "Бір тәулік ішінде аналықты тексеріңіз",
             "Дыбыстық аномалия ({anomaly_score:.2f}) аналықсыз ұяға тән. Ұяны ашып, "
             "жұмыртқа мен жас личинкаларды іздеңіз. Жұмыртқа болмаса, күшті ұядан "
             "жұмыртқалы бақылау жақтауын салыңыз."),
            (2, "Расталса, жаңа аналық қосыңыз",
             "Аналықсыздық расталса, торшамен ұрықтанған аналық қосыңыз — жазда ұя "
             "ұрықсыз тез әлсірейді."),
        ],
    ),
    # ── varroa treatment timing ──────────────────────────────────────
    _s(
        "varroa", "en", "autumn",
        {"health_score": 0.55, "swarm_risk": 0.02, "weight_kg": 26.0,
         "temp_ambient_c": 14.0},
        [
            (2, "Run a varroa wash and treat after harvest",
             "Health score has drifted down to {health_score:.2f} in early autumn, the "
             "classic varroa window. Take an alcohol wash or sugar-roll sample of ~300 "
             "bees; above 3 mites per 100 bees, treat now that supers are off — the "
             "winter bees being raised this month must emerge mite-free."),
            (3, "Plan a broodless follow-up treatment",
             "Whatever you use now, schedule an oxalic-acid dribble or vaporisation in "
             "late November when the colony is broodless — it catches the phoretic "
             "mites that autumn treatments under the cappings miss."),
        ],
    ),
    _s(
        "varroa", "en", "summer",
        {"health_score": 0.62, "swarm_risk": 0.2, "weight_kg": 46.0},
        [
            (2, "Monitor mites without contaminating the flow",
             "Health score {health_score:.2f} mid-flow warrants a mite check, but "
             "supers are on: do not apply amitraz or other chemical strips now — "
             "residues end up in the honey. Use a sticky-board count or sugar roll "
             "instead."),
            (3, "Use drone-brood removal as interim control",
             "If counts are elevated, cut out capped drone comb every 10-14 days — "
             "mites preferentially breed there. Hold full treatment until the supers "
             "come off, then treat immediately."),
        ],
    ),
    _s(
        "varroa", "en", "autumn",
        {"health_score": 0.45, "swarm_risk": 0.02, "weight_kg": 24.0,
         "temp_ambient_c": 12.0},
        [
            (1, "Treat for varroa immediately",
             "Health score {health_score:.2f} in autumn strongly suggests a damaging "
             "mite load — expect deformed-wing virus in the winter bees if untreated. "
             "Apply an approved treatment today (formic if daytime temps allow, "
             "amitraz strips otherwise) and verify the drop on a sticky board."),
            (2, "Assess whether the colony can still winter",
             "After treatment, judge colony strength: fewer than 6 frames of bees in "
             "October winters poorly in a continental climate. Consider combining "
             "with a stronger colony rather than losing both bees and stores."),
        ],
    ),
    _s(
        "varroa", "en", "winter",
        {"health_score": 0.7, "temp_ambient_c": -2.0, "temp_brood_c": 21.0,
         "weight_kg": 28.0, "co2_ppm": 3200},
        [
            (3, "Do the broodless oxalic treatment now",
             "The cluster is broodless (brood-nest temperature {temp_brood_c:.1f} C, "
             "well below incubation) — the ideal moment for a single oxalic-acid "
             "dribble or vaporisation, hitting every mite while exposed. Pick a calm "
             "day near 0 C and work quickly to avoid chilling the cluster."),
        ],
    ),
    _s(
        "varroa", "ru", "autumn",
        {"health_score": 0.52, "weight_kg": 25.0, "temp_ambient_c": 13.0},
        [
            (2, "Сделайте смыв на клеща и обработайте семью",
             "Оценка здоровья снизилась до {health_score:.2f} — типичная картина "
             "варроатоза в начале осени. Сделайте смыв ~300 пчёл; при заклещённости "
             "выше 3% обработайте сразу после снятия магазинов: сейчас выводятся "
             "зимние пчёлы."),
            (3, "Запланируйте обработку по безрасплодному клубу",
             "В конце ноября, когда расплода не будет, проведите обработку щавелевой "
             "кислотой — она добьёт клещей, недоступных под печаткой осенью."),
        ],
    ),
    _s(
        "varroa", "kk", "autumn",
        {"health_score": 0.52, "weight_kg": 25.0, "temp_ambient_c": 13.0},
        [
            (2, "Кенеге талдау жасап, ұяны өңдеңіз",
             "Денсаулық бағасы {health_score:.2f} дейін төмендеді — күз басындағы "
             "варроа белгісі. Шамамен 300 арадан сынама алыңыз; кене 3%-дан асса, "
             "корпустар алынған соң бірден өңдеу жүргізіңіз: қазір қыстайтын аралар "
             "өсіп жатыр."),
        ],
    ),
    # ── feeding ──────────────────────────────────────────────────────
    _s(
        "feeding", "en", "autumn",
        {"weight_kg": 14.0, "health_score": 0.72, "temp_ambient_c": 12.0},
        [
            (1, "Feed heavy syrup now to reach winter weight",
             "Hive weight is only {weight_kg:.1f} kg — far short of the 18-25 kg of "
             "stores a continental winter demands. Feed 2:1 sugar syrup in large "
             "batches every evening while daytime temperatures still exceed 10 C, so "
             "the bees can invert and cap it before the cold."),
            (3, "Reduce the entrance while feeding",
             "Autumn feeding invites robbing. Narrow the entrance to 2-3 cm, feed at "
             "dusk only, and never spill syrup near the hives."),
        ],
    ),
    _s(
        "feeding", "en", "spring",
        {"weight_kg": 16.0, "health_score": 0.68, "temp_ambient_c": 12.0,
         "temp_brood_c": 34.2},
        [
            (2, "Start stimulative feeding to drive buildup",
             "Stores are low at {weight_kg:.1f} kg just as brood rearing accelerates — "
             "a spring colony can starve within days during a cold snap. Feed 1:1 "
             "syrup in small regular doses and add a pollen patty if natural pollen "
             "is not yet coming in."),
            (4, "Track weight daily until the first flow",
             "Watch the telemetry: steady daily gains mean forage has started and "
             "feeding can stop; renewed losses mean keep feeding."),
        ],
    ),
    _s(
        "feeding", "en", "winter",
        {"weight_kg": 9.0, "temp_ambient_c": -12.0, "temp_brood_c": 24.0,
         "humidity_pct": 60.0},
        [
            (1, "Emergency-feed candy directly over the cluster",
             "Hive weight {weight_kg:.1f} kg in midwinter means the colony is at "
             "starvation risk. Do not feed syrup in the cold — place fondant or candy "
             "boards directly above the cluster on a mild day, working fast and "
             "keeping the hive open under a minute."),
        ],
        alerts=[("warning", "weight_drop", "Weight below winter-store threshold")],
    ),
    _s(
        "feeding", "ru", "autumn",
        {"weight_kg": 15.0, "temp_ambient_c": 11.0},
        [
            (1, "Закормите семью в зиму",
             "Вес улья всего {weight_kg:.1f} кг при норме 18-25 кг для зимовки в "
             "континентальном климате. Давайте сироп 2:1 большими порциями по вечерам, "
             "пока днём держится выше 10 C, чтобы пчёлы успели переработать и "
             "запечатать корм."),
            (3, "Сократите леток на время подкормки",
             "Осенняя подкормка провоцирует воровство: сузьте леток до 2-3 см и "
             "давайте сироп только в сумерках."),
        ],
    ),
    _s(
        "feeding", "kk", "spring",
        {"weight_kg": 16.0, "temp_ambient_c": 12.0},
        [
            (2, "Ұяның дамуы үшін ынталандырма азықтандыру бастаңыз",
             "Азық қоры бар болғаны {weight_kg:.1f} кг, ал ұрық өсіру қарқын алуда — "
             "суық қайтқанда көктемгі ұя бірнеше күнде аштыққа ұшырауы мүмкін. 1:1 "
             "шәрбатты аз-аздан үнемі беріп тұрыңыз, табиғи тозаң болмаса ақуызды "
             "қосымша қосыңыз."),
        ],
    ),
    # ── wintering (continental / Kazakhstan) ─────────────────────────
    _s(
        "wintering", "en", "autumn",
        {"weight_kg": 22.0, "temp_ambient_c": 6.0, "health_score": 0.8},
        [
            (2, "Finish continental winter preparation",
             "Stores are adequate at {weight_kg:.1f} kg; now weatherproof the hive for "
             "-30 C spells: fit mouse guards, add top insulation (leaving a small "
             "upper vent), tilt the hive slightly forward, and set up a windbreak on "
             "the prevailing north side."),
            (4, "Decide on outdoor wintering vs omshanik",
             "In your region, colonies under 8 frames of bees winter more reliably "
             "moved into an omshanik (winter shed) at a stable 0-4 C; strong colonies "
             "can stay outdoors with insulation."),
        ],
    ),
    _s(
        "wintering", "en", "winter",
        {"weight_kg": 24.0, "temp_ambient_c": -18.0, "temp_brood_c": 22.0,
         "humidity_pct": 55.0, "co2_ppm": 3800, "health_score": 0.82},
        [
            (5, "Cluster is wintering normally — do not disturb",
             "All signals are healthy: cluster core near {temp_brood_c:.1f} C, weight "
             "loss on trend, quiet steady hum. Do not open the hive; keep monitoring "
             "remotely and only clear snow from the entrance if it seals over."),
        ],
    ),
    _s(
        "wintering", "en", "winter",
        {"humidity_pct": 84.0, "temp_ambient_c": -5.0, "temp_brood_c": 23.0,
         "weight_kg": 23.0, "co2_ppm": 4600},
        [
            (2, "Fix condensation before it wets the cluster",
             "In-hive humidity at {humidity_pct:.0f} % risks condensation dripping "
             "from the cold inner cover onto the cluster — wet bees die at these "
             "temperatures. Add a moisture quilt or dry insulation above the inner "
             "cover and crack a small upper vent so damp air escapes."),
            (4, "Check the bottom board on the next mild day",
             "On a day above -5 C, quickly slide out or inspect the bottom tray: "
             "excessive wet debris confirms poor airflow, a scatter of dry cappings "
             "is normal feeding sign."),
        ],
    ),
    _s(
        "wintering", "en", "winter",
        {"temp_ambient_c": -27.0, "temp_brood_c": 20.5, "weight_kg": 21.0,
         "humidity_pct": 58.0, "audio_db": 34.0},
        [
            (4, "Deep-frost spell: check entrances, nothing else",
             "At {temp_ambient_c:.1f} C the cluster is tight and the low steady hum is "
             "normal. Visit only to ensure entrances are not iced or snowed shut and "
             "the windbreak is intact. Any opening of the hive now would cost the "
             "colony dearly in stores and bees."),
        ],
    ),
    _s(
        "wintering", "ru", "winter",
        {"humidity_pct": 82.0, "temp_ambient_c": -8.0, "temp_brood_c": 23.0,
         "weight_kg": 22.0},
        [
            (2, "Устраните сырость в зимующем улье",
             "Влажность в улье {humidity_pct:.0f} % — конденсат с холодного потолка "
             "может капать на клуб, а мокрые пчёлы зимой погибают. Положите влаго- "
             "поглощающую подушку над потолком и приоткройте верхний леток для "
             "вытяжки."),
        ],
    ),
    _s(
        "wintering", "kk", "winter",
        {"temp_ambient_c": -20.0, "temp_brood_c": 21.5, "weight_kg": 22.0,
         "audio_db": 33.0},
        [
            (5, "Ұя қалыпты қыстап жатыр — мазаламаңыз",
             "Барлық көрсеткіштер қалыпты: шоғыр температурасы {temp_brood_c:.1f} C, "
             "салмақ біркелкі азаюда, гуіл тыныш. Ұяны ашпаңыз; тек кіру тесігін "
             "қар мен мұздан тазартып тұрыңыз."),
        ],
    ),
    # ── ventilation / condensation ───────────────────────────────────
    _s(
        "ventilation", "en", "summer",
        {"co2_ppm": 5800, "humidity_pct": 78.0, "temp_brood_c": 35.9,
         "temp_ambient_c": 29.0, "audio_db": 57.0},
        [
            (2, "Open up airflow — the hive cannot breathe",
             "CO2 at {co2_ppm:.0f} ppm with {humidity_pct:.0f} % humidity means the "
             "colony is struggling to ventilate; fanning bees are being pulled away "
             "from foraging and nectar is curing slowly. Open the entrance to full "
             "width, add an upper entrance, and stagger supers a few millimetres."),
            (3, "Reduce afternoon heat load",
             "Shade the hive during peak afternoon sun or paint the roof white. A "
             "water source within 100 m cuts the workforce diverted to hauling water "
             "for evaporative cooling."),
        ],
    ),
    _s(
        "ventilation", "en", "summer",
        {"temp_ambient_c": 38.0, "temp_brood_c": 36.9, "humidity_pct": 45.0,
         "swarm_risk": 0.3, "audio_db": 59.0},
        [
            (1, "Protect the brood from heat collapse",
             "Brood is at {temp_brood_c:.1f} C with ambient {temp_ambient_c:.1f} C — "
             "above 37 C brood starts dying and combs can soften. Act today: full "
             "shade, maximum ventilation top and bottom, and a dripping water source "
             "nearby. Heavy bearding on the front is the colony buying itself cooling "
             "space; do not force the bees back in."),
        ],
        alerts=[("warning", "temp_anomaly", "Brood temperature above safe band")],
    ),
    _s(
        "ventilation", "en", "autumn",
        {"humidity_pct": 83.0, "temp_ambient_c": 8.0, "co2_ppm": 4400,
         "temp_brood_c": 33.5},
        [
            (3, "Dry the hive out before cold sets in",
             "Humidity of {humidity_pct:.0f} % in autumn is a mould and nosema risk "
             "once the cluster forms. Add an upper entrance or vent shim, make sure "
             "the hive tilts forward so rain drains off the bottom board, and clear "
             "vegetation blocking under-hive airflow."),
        ],
    ),
    _s(
        "ventilation", "ru", "summer",
        {"co2_ppm": 5500, "humidity_pct": 76.0, "temp_ambient_c": 30.0},
        [
            (3, "Улучшите воздухообмен улья",
             "CO2 {co2_ppm:.0f} ppm при влажности {humidity_pct:.0f} % — семья с "
             "трудом вентилирует гнездо, нектар сохнет медленно. Откройте леток на "
             "всю ширину, добавьте верхний леток, притените улей в полуденные часы."),
        ],
    ),
    _s(
        "ventilation", "kk", "autumn",
        {"humidity_pct": 81.0, "temp_ambient_c": 7.0, "co2_ppm": 4300},
        [
            (3, "Суық түспей тұрып ұяны құрғатыңыз",
             "Ылғалдылық {humidity_pct:.0f} % — күзде бұл зең мен нозематоз қаупін "
             "тудырады. Жоғарғы кіру тесігін ашыңыз, ұяның алға қарай сәл еңіс "
             "тұрғанын тексеріңіз, астыңғы желдетуді бөгейтін шөпті алып тастаңыз."),
        ],
    ),
    # ── honey-flow management ────────────────────────────────────────
    _s(
        "honeyflow", "en", "summer",
        {"weight_kg": 55.0, "health_score": 0.88, "swarm_risk": 0.25,
         "temp_ambient_c": 27.0},
        [
            (2, "Stay ahead of the flow with super space",
             "The scale shows strong gains to {weight_kg:.1f} kg — a major flow is on. "
             "Add the next super before the current one is 70 % full; bees ripen "
             "nectar faster with room to spread it. Under-supering (new box below the "
             "filling one) keeps the bees moving up."),
            (4, "Plan extraction while combs are warm",
             "Pull and extract frames that are at least 75 % capped. Extracting soon "
             "after removal, while honey is warm, halves the work and returns wet "
             "combs for the bees to refill."),
        ],
    ),
    _s(
        "honeyflow", "en", "summer",
        {"weight_kg": 52.0, "health_score": 0.85, "swarm_risk": 0.1},
        [
            (2, "The flow has ended — harvest and guard",
             "Weight has plateaued after a strong run to {weight_kg:.1f} kg. Take off "
             "the capped supers within the week; the longer they sit, the higher the "
             "robbing pressure. Immediately narrow entrances across the apiary — the "
             "dearth after a flow is peak robbing season."),
            (3, "Leave the colony its share",
             "Do not strip the brood boxes: leave the colony 10-12 kg now so autumn "
             "feeding starts from a healthy base, not from emergency."),
        ],
    ),
    _s(
        "honeyflow", "en", "summer",
        {"weight_kg": 47.0, "health_score": 0.9, "swarm_risk": 0.15,
         "temp_brood_c": 35.0},
        [
            (5, "Flow proceeding well — minimise interference",
             "Gains are steady, brood at a healthy {temp_brood_c:.1f} C, no anomalies. "
             "Every full inspection during a flow costs the colony roughly a day of "
             "foraging — limit yourself to hefting supers and reading the telemetry "
             "until the flow tapers."),
        ],
    ),
    _s(
        "honeyflow", "en", "summer",
        {"weight_kg": 41.0, "is_anomaly": True, "anomaly_kind": "sudden_weight_drop",
         "anomaly_score": 0.76, "health_score": 0.7, "audio_db": 56.0},
        [
            (2, "Check for robbing after the sharp weight drop",
             "A sudden weight drop during the post-flow dearth points to robbing (or "
             "a late swarm). Watch the entrance for fighting and zigzag flight; if "
             "robbing is confirmed, narrow the entrance to one bee-width, and wet-"
             "sheet the front in severe cases. Verify the queen is still present."),
        ],
        alerts=[("warning", "weight_drop", "Weight fell 2.4 kg in 3 h")],
    ),
    _s(
        "honeyflow", "ru", "summer",
        {"weight_kg": 54.0, "health_score": 0.86, "swarm_risk": 0.2},
        [
            (2, "Расширяйте магазины с опережением взятка",
             "Привесы сильные, вес достиг {weight_kg:.1f} кг — идёт главный взяток. "
             "Ставьте следующую надставку, когда текущая заполнена на 70 %: пчёлам "
             "нужно место для складывания и просушки напрыска."),
            (4, "Откачивайте запечатанные рамки вовремя",
             "Отбирайте рамки, запечатанные не менее чем на три четверти, и качайте "
             "их тёплыми — сразу после отбора."),
        ],
    ),
    _s(
        "honeyflow", "kk", "summer",
        {"weight_kg": 53.0, "health_score": 0.86, "swarm_risk": 0.2},
        [
            (2, "Бал жинау кезінде корпусты алдын ала қойыңыз",
             "Салмақ {weight_kg:.1f} кг-ға жетті — негізгі бал жинау жүріп жатыр. "
             "Ағымдағы корпус 70 %-ға толғанда келесісін қойыңыз: араларға нектарды "
             "жайып кептіруге орын керек."),
            (4, "Піскен балды уақытында алыңыз",
             "Кемінде төрттен үші жабылған жақтауларды алып, жылы кезінде тартыңыз."),
        ],
    ),
]


# ── context assembly ─────────────────────────────────────────────────


def _random_timestamp(rng: random.Random, season: str) -> datetime:
    month = rng.choice(_SEASON_MONTHS[season])
    year = rng.choice([2025, 2026])
    day = rng.randint(1, 28)
    hour = rng.randint(5, 21)
    return datetime(year, month, day, hour, rng.randint(0, 59), tzinfo=timezone.utc)


def _jitter_value(rng: random.Random, key: str, value: float) -> float:
    mode, magnitude = _JITTER[key]
    if mode == "add":
        value = value + rng.uniform(-magnitude, magnitude)
    else:
        value = value * (1.0 + rng.uniform(-magnitude, magnitude))
    lo, hi = _CLAMPS.get(key, (float("-inf"), float("inf")))
    value = min(hi, max(lo, value))
    digits = _ROUND.get(key, 2)
    return int(round(value)) if digits == 0 else round(value, digits)


def build_flat_context(seed: Seed, rng: random.Random) -> dict[str, Any]:
    """Merge seed overrides over defaults and jitter every numeric field."""
    flat = _flat_defaults()
    flat.update(seed.ctx)
    for key in _JITTER:
        if flat.get(key) is not None:
            flat[key] = _jitter_value(rng, key, float(flat[key]))

    code, region, apiary_name, lat, lon = rng.choice(_REGIONS)
    number = rng.randint(1, 240)
    ts = _random_timestamp(rng, seed.season)
    flat.update(
        {
            "hive_id": f"KZ-{code}-{number:04d}",
            "hive_name": f"Hive {number}",
            "hive_type": rng.choice(_HIVE_TYPES),
            "queen_year": ts.year - rng.randint(0, 2),
            "frames": rng.choice([10, 12, 14, 16, 20]),
            "apiary_id": f"apiary-{code.lower()}-{rng.randint(1, 4):02d}",
            "apiary_name": apiary_name,
            "region": region,
            "latitude": round(lat + rng.uniform(-0.4, 0.4), 3),
            "longitude": round(lon + rng.uniform(-0.4, 0.4), 3),
            "ts": ts,
        }
    )
    return flat


def to_nested_context(flat: dict[str, Any], seed: Seed) -> dict[str, Any]:
    """Convert a flat context into the shared dict shape from app.prompts."""
    ts: datetime = flat["ts"]
    iso = ts.isoformat().replace("+00:00", "Z")
    pred_ts = (ts + timedelta(seconds=5)).isoformat().replace("+00:00", "Z")
    alerts = [
        {
            "time": (ts - timedelta(hours=i * 6 + 1)).isoformat().replace("+00:00", "Z"),
            "severity": severity,
            "kind": kind,
            "message": message,
        }
        for i, (severity, kind, message) in enumerate(seed.alerts)
    ]
    return {
        "hive": {
            "id": flat["hive_id"],
            "name": flat["hive_name"],
            "hive_type": flat["hive_type"],
            "queen_year": flat["queen_year"],
            "frames": flat["frames"],
        },
        "apiary": {
            "id": flat["apiary_id"],
            "name": flat["apiary_name"],
            "region": flat["region"],
            "latitude": flat["latitude"],
            "longitude": flat["longitude"],
        },
        "reading": {
            "time": iso,
            "temp_brood_c": flat["temp_brood_c"],
            "temp_ambient_c": flat["temp_ambient_c"],
            "humidity_pct": flat["humidity_pct"],
            "weight_kg": flat["weight_kg"],
            "audio_db": flat["audio_db"],
            "co2_ppm": flat["co2_ppm"],
            "battery_v": flat["battery_v"],
        },
        "prediction": {
            "time": pred_ts,
            "model_version": flat["model_version"],
            "swarm_risk": flat["swarm_risk"],
            "health_score": flat["health_score"],
            "is_anomaly": flat["is_anomaly"],
            "anomaly_kind": flat["anomaly_kind"],
            "anomaly_score": flat["anomaly_score"],
        },
        "alerts": alerts,
    }


def render_example(seed: Seed, rng: random.Random) -> dict[str, Any]:
    """One augmented chat-format training example."""
    flat = build_flat_context(seed, rng)
    ctx = to_nested_context(flat, seed)
    recs = [
        (priority, title.format(**flat), body.format(**flat))
        for priority, title, body in seed.recs
    ]
    return {
        "messages": [
            {"role": "system", "content": build_system_prompt(seed.lang)},
            {"role": "user", "content": build_user_prompt(ctx)},
            {"role": "assistant", "content": format_recommendations(recs)},
        ],
        "meta": {"topic": seed.topic, "lang": seed.lang, "season": seed.season},
    }


def build_dataset(
    n: int, seed: int
) -> list[dict[str, Any]]:
    """~n examples: every curated seed once, then augmented variations."""
    rng = random.Random(seed)
    examples = [render_example(s, rng) for s in SEEDS]
    while len(examples) < n:
        examples.append(render_example(rng.choice(SEEDS), rng))
    rng.shuffle(examples)
    return examples


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the advisor SFT dataset.")
    parser.add_argument("--n", type=int, default=1000, help="total examples")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out", type=Path, default=Path(__file__).parent / "data" / "train.jsonl"
    )
    parser.add_argument(
        "--eval-out", type=Path, default=Path(__file__).parent / "data" / "eval.jsonl"
    )
    parser.add_argument(
        "--eval-fraction", type=float, default=0.05, help="held-out share"
    )
    args = parser.parse_args()

    examples = build_dataset(args.n, args.seed)
    n_eval = max(1, int(len(examples) * args.eval_fraction))
    eval_rows, train_rows = examples[:n_eval], examples[n_eval:]
    _write_jsonl(args.out, train_rows)
    _write_jsonl(args.eval_out, eval_rows)

    by_lang: dict[str, int] = {}
    for row in examples:
        by_lang[row["meta"]["lang"]] = by_lang.get(row["meta"]["lang"], 0) + 1
    print(
        f"wrote {len(train_rows)} train -> {args.out}\n"
        f"wrote {len(eval_rows)} eval  -> {args.eval_out}\n"
        f"languages: {by_lang}"
    )


if __name__ == "__main__":
    main()
