"""Deterministic rule-based recommendations.

Used whenever the Hugging Face call fails or no real HF_API_KEY is configured,
so POST /recommendations always answers. Consumes the same context dict as the
prompt builder (see app/prompts.py) and emits the same ParsedRecommendation
shape as the model parser. Pure logic: no I/O, fully deterministic.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.parse import ParsedRecommendation
from app.prompts import DEFAULT_LOCALE, MAX_RECOMMENDATIONS

FALLBACK_MODEL_ID = "fallback-rules"

# (title, body) per locale per rule key. Bodies may use str.format placeholders
# that the corresponding rule always supplies.
_STRINGS: dict[str, dict[str, tuple[str, str]]] = {
    "queenless": {
        "en": (
            "Inspect for queenlessness within 24 hours",
            "Acoustic signature suggests the colony may be queenless "
            "(anomaly score {score:.2f}). Open the hive and look for eggs and "
            "young larvae; check for emergency queen cells. If no queen is "
            "found, introduce a mated queen or a frame of eggs from a strong "
            "colony so the bees can raise a new one.",
        ),
        "ru": (
            "Проверьте улей на безматочность в течение 24 часов",
            "Акустическая картина указывает на возможную потерю матки "
            "(оценка аномалии {score:.2f}). Откройте улей и проверьте наличие "
            "яиц и молодых личинок, осмотрите рамки на свищевые маточники. "
            "Если матки нет, подсадите плодную матку или дайте рамку с яйцами "
            "из сильной семьи.",
        ),
        "kk": (
            "24 сағат ішінде ұяны аналықсыздыққа тексеріңіз",
            "Дыбыстық белгілер бойынша ұя аналығынан айырылған болуы мүмкін "
            "(аномалия бағасы {score:.2f}). Ұяны ашып, жұмыртқа мен жас "
            "личинкалардың бар-жоғын тексеріңіз. Аналық табылмаса, жаңа аналық "
            "қосыңыз немесе күшті ұядан жұмыртқалы жақтау беріңіз.",
        ),
    },
    "swarm_high": {
        "en": (
            "Act now to prevent swarming",
            "Swarm risk is {risk:.0%}, which is critical. Inspect immediately "
            "for capped queen cells, add supers or empty frames to relieve "
            "congestion, and consider an artificial swarm (split) if queen "
            "cells are present. Ensure the queen has open comb to lay in.",
        ),
        "ru": (
            "Срочно примите меры против роения",
            "Риск роения {risk:.0%} — критический уровень. Немедленно "
            "осмотрите семью на печатные маточники, расширьте гнездо "
            "надставкой или пустыми рамками. При наличии маточников сделайте "
            "отводок. Убедитесь, что у матки есть свободные соты для засева.",
        ),
        "kk": (
            "Үйірленудің алдын алу үшін дереу шара қолданыңыз",
            "Үйірлену қаупі {risk:.0%} — өте жоғары деңгей. Ұяны дереу ашып, "
            "жабық аналық ұяшықтарды тексеріңіз, қосымша корпус немесе бос "
            "жақтаулар қойыңыз. Аналық ұяшықтар табылса, бөлінді жасаңыз.",
        ),
    },
    "swarm_medium": {
        "en": (
            "Reduce swarm pressure this week",
            "Swarm risk is elevated ({risk:.0%}). Give the colony more space: "
            "add a super or rotate in empty drawn comb, and improve "
            "ventilation. Re-check for queen cells at the next inspection.",
        ),
        "ru": (
            "Снизьте роевое настроение на этой неделе",
            "Риск роения повышен ({risk:.0%}). Дайте семье больше места: "
            "поставьте надставку или подставьте пустую сушь, улучшите "
            "вентиляцию. При следующем осмотре проверьте маточники.",
        ),
        "kk": (
            "Осы аптада үйірлену қаупін азайтыңыз",
            "Үйірлену қаупі жоғарылаған ({risk:.0%}). Ұяға кеңістік беріңіз: "
            "қосымша корпус немесе бос ұяшықты жақтау қойып, желдетуді "
            "жақсартыңыз. Келесі тексеруде аналық ұяшықтарды қараңыз.",
        ),
    },
    "brood_cold": {
        "en": (
            "Brood nest is too cold — check the colony",
            "Brood temperature is {temp:.1f} C, below the 34-36 C band the "
            "brood needs. The cluster may be too small for the volume or the "
            "queen may have stopped laying. Reduce the hive volume, close "
            "extra ventilation, and verify colony strength and stores.",
        ),
        "ru": (
            "Расплодное гнездо переохлаждено — проверьте семью",
            "Температура расплода {temp:.1f} C — ниже нормы 34-36 C. Возможно, "
            "клуб слишком мал для объёма улья или матка прекратила засев. "
            "Сократите гнездо, уменьшите вентиляцию, проверьте силу семьи и "
            "запасы корма.",
        ),
        "kk": (
            "Ұрық ұясы тым суық — ұяны тексеріңіз",
            "Ұрық температурасы {temp:.1f} C — қалыпты 34-36 C деңгейінен "
            "төмен. Ұя көлемін қысқартып, артық желдетуді жабыңыз, ұяның күші "
            "мен азық қорын тексеріңіз.",
        ),
    },
    "brood_hot": {
        "en": (
            "Brood nest is overheating — improve cooling",
            "Brood temperature is {temp:.1f} C, above the safe band. Provide "
            "shade, open the entrance fully, add ventilation, and make sure "
            "the bees have a nearby water source. Prolonged overheating kills "
            "brood and triggers bearding and swarming.",
        ),
        "ru": (
            "Гнездо перегревается — улучшите охлаждение",
            "Температура расплода {temp:.1f} C — выше безопасного диапазона. "
            "Притените улей, полностью откройте леток, усильте вентиляцию и "
            "обеспечьте пчёл водой рядом с пасекой. Длительный перегрев губит "
            "расплод и провоцирует роение.",
        ),
        "kk": (
            "Ұя қызып барады — салқындатуды жақсартыңыз",
            "Ұрық температурасы {temp:.1f} C — қауіпсіз деңгейден жоғары. "
            "Ұяға көлеңке жасаңыз, кіру тесігін толық ашыңыз, желдетуді "
            "күшейтіп, жақын жерде су көзін қамтамасыз етіңіз.",
        ),
    },
    "weight_drop": {
        "en": (
            "Investigate the sudden weight drop",
            "A sudden weight drop was detected. Rule out a swarm departure, "
            "robbing, or absconding: check the entrance for fighting bees, "
            "inspect for a missing queen and reduced bee mass, and narrow the "
            "entrance if robbing is under way.",
        ),
        "ru": (
            "Разберитесь с резкой потерей веса",
            "Зафиксировано резкое падение массы улья. Исключите слёт роя, "
            "воровство или слёт семьи: осмотрите леток на драки пчёл, "
            "проверьте наличие матки и силу семьи, при воровстве сократите "
            "леток.",
        ),
        "kk": (
            "Салмақтың күрт төмендеуін тексеріңіз",
            "Ұя салмағының күрт төмендеуі байқалды. Үйірдің ұшып кетуін, "
            "тонауды немесе ұяның кетуін тексеріңіз: кіру тесігіндегі "
            "төбелесті бақылап, аналық пен ұя күшін тексеріңіз, тонау болса "
            "кіру тесігін тарылтыңыз.",
        ),
    },
    "ventilation": {
        "en": (
            "Improve hive ventilation",
            "In-hive conditions point to poor air exchange (humidity "
            "{humidity} %, CO2 {co2} ppm). Open the entrance wider or add an "
            "upper entrance, and make sure condensation cannot drip onto the "
            "cluster. Chronic damp promotes chalkbrood and nosema.",
        ),
        "ru": (
            "Улучшите вентиляцию улья",
            "Показатели указывают на плохой воздухообмен (влажность "
            "{humidity} %, CO2 {co2} ppm). Расширьте леток или откройте "
            "верхний леток, исключите капёж конденсата на клуб. Постоянная "
            "сырость провоцирует аскосфероз и нозематоз.",
        ),
        "kk": (
            "Ұяның желдетуін жақсартыңыз",
            "Көрсеткіштер ауа алмасуының нашарлығын білдіреді (ылғалдылық "
            "{humidity} %, CO2 {co2} ppm). Кіру тесігін кеңейтіңіз немесе "
            "жоғарғы тесік ашыңыз, конденсат ұя шоғырына тамбауын қадағалаңыз.",
        ),
    },
    "health_low": {
        "en": (
            "Do a full health inspection",
            "The ML health score is low ({health:.2f}). Perform a full "
            "frame-by-frame inspection: brood pattern, stores, signs of "
            "varroa, deformed wings and dysentery. Take a varroa wash or "
            "sugar-roll sample and treat if the mite count exceeds threshold.",
        ),
        "ru": (
            "Проведите полный осмотр семьи",
            "ML-оценка здоровья низкая ({health:.2f}). Проведите полный "
            "порамочный осмотр: качество расплода, запасы корма, признаки "
            "варроатоза, деформированные крылья, понос. Сделайте смыв на "
            "клеща и обработайте при превышении порога.",
        ),
        "kk": (
            "Ұяны толық тексеруден өткізіңіз",
            "ML денсаулық бағасы төмен ({health:.2f}). Ұяны жақтау бойынша "
            "толық тексеріңіз: ұрық сапасы, азық қоры, варроа белгілері. "
            "Кене санын өлшеп, шектен асса емдеу жүргізіңіз.",
        ),
    },
    "sensor_offline": {
        "en": (
            "Restore the hive sensor node",
            "The sensor node has been reported offline, so current telemetry "
            "may be stale. Check the node's power and mounting, and verify "
            "gateway connectivity at the apiary on the next visit.",
        ),
        "ru": (
            "Восстановите работу датчика улья",
            "Сенсорный узел не выходит на связь, телеметрия может быть "
            "устаревшей. При следующем визите проверьте питание и крепление "
            "узла, а также связь шлюза на пасеке.",
        ),
        "kk": (
            "Ұя датчигінің жұмысын қалпына келтіріңіз",
            "Сенсорлық түйін желіден тыс деп хабарланды, телеметрия ескірген "
            "болуы мүмкін. Келесі барғанда түйіннің қуаты мен бекітілуін және "
            "шлюз байланысын тексеріңіз.",
        ),
    },
    "battery_low": {
        "en": (
            "Replace or recharge the sensor battery",
            "Sensor battery is at {battery:.2f} V and will die soon, causing "
            "telemetry gaps. Swap or recharge the battery on the next apiary "
            "visit and check the solar panel connection if fitted.",
        ),
        "ru": (
            "Замените или зарядите батарею датчика",
            "Напряжение батареи датчика {battery:.2f} В — скоро узел "
            "отключится и появятся пропуски в телеметрии. Замените или "
            "зарядите батарею при следующем визите, проверьте солнечную "
            "панель, если она установлена.",
        ),
        "kk": (
            "Датчик батареясын ауыстырыңыз немесе зарядтаңыз",
            "Датчик батареясының кернеуі {battery:.2f} В — жақында түйін "
            "өшіп, телеметрияда үзілістер болады. Келесі барғанда батареяны "
            "ауыстырыңыз немесе зарядтаңыз.",
        ),
    },
    "season_spring": {
        "en": (
            "Support spring buildup",
            "No urgent issues detected. Keep supporting spring buildup: "
            "ensure at least 8-10 kg of stores, add frames as the brood nest "
            "expands, and plan the first varroa monitoring of the season.",
        ),
        "ru": (
            "Поддерживайте весеннее развитие",
            "Срочных проблем не выявлено. Поддерживайте весеннее развитие: "
            "держите не менее 8-10 кг корма, расширяйте гнездо по мере роста "
            "расплода и запланируйте первый весенний контроль клеща.",
        ),
        "kk": (
            "Көктемгі дамуды қолдаңыз",
            "Шұғыл мәселе анықталған жоқ. Көктемгі дамуды қолдаңыз: кемінде "
            "8-10 кг азық қорын ұстаңыз, ұрық ұясы өскен сайын жақтау қосыңыз "
            "және маусымның алғашқы варроа бақылауын жоспарлаңыз.",
        ),
    },
    "season_summer": {
        "en": (
            "Manage the honey flow",
            "No urgent issues detected. During the flow, add supers before "
            "the bees need them, keep ventilation generous, and avoid "
            "unnecessary brood-nest inspections that interrupt foraging.",
        ),
        "ru": (
            "Управляйте медосбором",
            "Срочных проблем не выявлено. В медосбор ставьте надставки "
            "заранее, обеспечьте хорошую вентиляцию и не тревожьте гнездо "
            "лишними осмотрами, чтобы не срывать лёт пчёл.",
        ),
        "kk": (
            "Бал жинауды тиімді басқарыңыз",
            "Шұғыл мәселе анықталған жоқ. Бал жинау кезінде корпустарды "
            "алдын ала қойыңыз, желдетуді жеткілікті етіп, ұяны қажетсіз "
            "тексерулермен мазаламаңыз.",
        ),
    },
    "season_autumn": {
        "en": (
            "Prepare the colony for winter",
            "No urgent issues detected. Complete autumn preparation: finish "
            "varroa treatment after the last honey harvest, feed to 18-25 kg "
            "of winter stores for a continental winter, and reduce entrances "
            "against robbing and mice.",
        ),
        "ru": (
            "Готовьте семью к зимовке",
            "Срочных проблем не выявлено. Завершите осеннюю подготовку: "
            "обработайте от варроатоза после снятия мёда, докормите до "
            "18-25 кг зимних запасов для континентальной зимы, сократите "
            "летки от воровства и мышей.",
        ),
        "kk": (
            "Ұяны қыстауға дайындаңыз",
            "Шұғыл мәселе анықталған жоқ. Күзгі дайындықты аяқтаңыз: бал "
            "алынған соң варроаға қарсы өңдеу жүргізіңіз, қысқа 18-25 кг азық "
            "қорын жеткізіңіз, кіру тесіктерін тарылтыңыз.",
        ),
    },
    "season_winter": {
        "en": (
            "Monitor the wintering cluster remotely",
            "No urgent issues detected. Avoid opening the hive in winter; "
            "rely on telemetry. Watch for steady slow weight loss (normal "
            "0.5-1.5 kg/month), listen for the quiet cluster hum, and clear "
            "snow from entrances so the hive can breathe.",
        ),
        "ru": (
            "Наблюдайте за зимующим клубом дистанционно",
            "Срочных проблем не выявлено. Зимой улей не вскрывайте — "
            "полагайтесь на телеметрию. Норма убыли веса 0,5-1,5 кг/мес; "
            "следите за ровным гулом клуба и очищайте летки от снега для "
            "вентиляции.",
        ),
        "kk": (
            "Қыстап жатқан ұяны қашықтан бақылаңыз",
            "Шұғыл мәселе анықталған жоқ. Қыста ұяны ашпаңыз — телеметрияға "
            "сүйеніңіз. Салмақтың айына 0,5-1,5 кг азаюы қалыпты; кіру "
            "тесіктерін қардан тазартып тұрыңыз.",
        ),
    },
}


def _localized(key: str, locale: str, priority: int, **fmt: Any) -> ParsedRecommendation:
    strings = _STRINGS[key]
    title, body = strings.get(locale, strings[DEFAULT_LOCALE])
    return ParsedRecommendation(priority=priority, title=title, body=body.format(**fmt))


def _alert_kinds(ctx: dict[str, Any]) -> set[str]:
    return {a.get("kind", "") for a in ctx.get("alerts") or []}


def _season_key(now: datetime) -> str:
    month = now.month
    if month in (3, 4, 5):
        return "season_spring"
    if month in (6, 7, 8):
        return "season_summer"
    if month in (9, 10):
        return "season_autumn"
    return "season_winter"


def generate_fallback(
    ctx: dict[str, Any],
    locale: str,
    now: datetime | None = None,
) -> list[ParsedRecommendation]:
    """Deterministic recommendations from the shared context dict."""
    now = now or datetime.now(timezone.utc)
    reading: dict[str, Any] = ctx.get("reading") or {}
    prediction: dict[str, Any] = ctx.get("prediction") or {}
    alert_kinds = _alert_kinds(ctx)

    swarm_risk = prediction.get("swarm_risk")
    health = prediction.get("health_score")
    anomaly_kind = prediction.get("anomaly_kind") or "none"
    anomaly_score = prediction.get("anomaly_score") or 0.0
    is_anomaly = bool(prediction.get("is_anomaly"))
    temp_brood = reading.get("temp_brood_c")
    humidity = reading.get("humidity_pct")
    co2 = reading.get("co2_ppm")
    battery = reading.get("battery_v")

    recs: list[ParsedRecommendation] = []

    if (is_anomaly and anomaly_kind == "queenless_acoustic") or "queenless" in alert_kinds:
        recs.append(_localized("queenless", locale, 1, score=anomaly_score or 0.9))

    if (swarm_risk is not None and swarm_risk >= 0.7) or "swarm_imminent" in alert_kinds:
        recs.append(_localized("swarm_high", locale, 1, risk=swarm_risk or 0.7))
    elif swarm_risk is not None and swarm_risk >= 0.45:
        recs.append(_localized("swarm_medium", locale, 2, risk=swarm_risk))

    if temp_brood is not None and temp_brood < 32.0:
        recs.append(_localized("brood_cold", locale, 2, temp=temp_brood))
    elif temp_brood is not None and temp_brood > 36.5:
        recs.append(_localized("brood_hot", locale, 2, temp=temp_brood))

    if (is_anomaly and anomaly_kind == "sudden_weight_drop") or "weight_drop" in alert_kinds:
        recs.append(_localized("weight_drop", locale, 2))

    if (humidity is not None and humidity > 75.0) or (co2 is not None and co2 > 5000):
        recs.append(
            _localized(
                "ventilation",
                locale,
                3,
                humidity="n/a" if humidity is None else f"{humidity:.0f}",
                co2="n/a" if co2 is None else f"{co2:.0f}",
            )
        )

    if health is not None and health < 0.5:
        recs.append(_localized("health_low", locale, 2, health=health))
    elif health is not None and health < 0.65:
        recs.append(_localized("health_low", locale, 3, health=health))

    if "sensor_offline" in alert_kinds or (is_anomaly and anomaly_kind == "sensor_fault"):
        recs.append(_localized("sensor_offline", locale, 4))

    if (battery is not None and battery < 3.6) or "low_battery" in alert_kinds:
        recs.append(_localized("battery_low", locale, 4, battery=battery or 3.5))

    if not recs:
        recs.append(_localized(_season_key(now), locale, 5))

    recs.sort(key=lambda r: r.priority)
    return recs[:MAX_RECOMMENDATIONS]
