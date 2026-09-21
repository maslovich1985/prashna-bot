"""Логика прашны: определение бхавы вопроса, текстовое представление карты,
предварительные астрологические факторы «да/нет» для последующей интерпретации LLM.
"""

from __future__ import annotations

from . import constants as C
from .chart import PrashnaChart, dms, houses_between


def detect_house_scored(question: str) -> tuple[int, int]:
    """Дом вопроса и его балл: сумма длин совпавших ключевых слов.

    Балл нужен проверке валидности (§5.5.1): ноль означает, что ни одно слово не
    совпало и дом 1 взят как фолбэк, а не найден. Без этого бот выдаёт уверенное
    толкование по случайному дому.
    """
    q = question.lower()
    scores: dict[int, int] = {}
    for house, words in C.HOUSE_KEYWORDS.items():
        for w in words:
            if w in q:
                scores[house] = scores.get(house, 0) + len(w)
    if not scores:
        return 1, 0
    return max(scores.items(), key=lambda kv: kv[1])


def detect_house(question: str) -> int:
    """Определяет дом (бхаву), к которому относится вопрос, по ключевым словам."""
    return detect_house_scored(question)[0]


def _benefic_malefic(name: str) -> str:
    return "благодетель" if name in C.BENEFICS else "вредитель"


def judgment_factors(chart: PrashnaChart) -> list[str]:
    """Классические факторы прашны, влияющие на ответ «да/нет»."""
    out: list[str] = []
    asc = chart.asc_sign
    qh = chart.question_house
    qh_sign = (asc + qh - 1) % 12
    lagna_lord = C.SIGN_LORDS[asc]
    qh_lord = C.SIGN_LORDS[qh_sign]
    ll = chart.planets[lagna_lord]
    ql = chart.planets[qh_lord]
    moon = chart.planets["Луна"]

    out.append(
        f"Лагна: {C.SIGNS[asc]} {dms(chart.asc_deg)} — качество знака: {C.SIGN_QUALITY[asc]}."
    )
    if chart.asc_deg < 3:
        out.append(
            "Лагна в самом начале знака — дело ещё не созрело / рано судить, "
            "вопрос преждевременный."
        )
    elif chart.asc_deg > 27:
        out.append(
            "Лагна в конце знака — дело уже фактически решено или уходит из рук, "
            "ситуация на исходе."
        )

    out.append(
        f"Владыка лагны {lagna_lord} — {ll.house}-й дом, {ll.dignity}"
        + (", ретроградный" if ll.retro else "")
        + (", сожжён" if ll.combust else "")
        + "."
    )
    out.append(
        f"Владыка {qh}-го дома (дома вопроса) {qh_lord} — {ql.house}-й дом, {ql.dignity}"
        + (", ретроградный" if ql.retro else "")
        + (", сожжён" if ql.combust else "")
        + "."
    )

    # Связь лагнеша и владыки дома вопроса
    if lagna_lord == qh_lord:
        out.append(
            "Владыка лагны и владыка дома вопроса — одна планета: "
            "сильная связь вопрошающего с предметом вопроса (благоприятно)."
        )
    else:
        same_sign = ll.sign == ql.sign
        if same_sign:
            out.append(
                "Владыка лагны и владыка дома вопроса в соединении — "
                "прямая связь, результат вероятен."
            )
        elif houses_between(ll.sign, ql.sign) == 7:
            out.append(
                "Владыка лагны и владыка дома вопроса в оппозиции (7/7) — "
                "связь есть, но через противостояние/переговоры."
            )
        elif ll.sign == ql.sign:
            out.append("Планеты связаны.")
        else:
            out.append(
                "Прямой связи (соединение/оппозиция) между владыкой лагны "
                "и владыкой дома вопроса нет — "
                "событие требует дополнительных усилий."
            )

    # Кендры/триконы и дустхана
    for label, p in (("Владыка лагны", ll), (f"Владыка {qh}-го дома", ql), ("Луна", moon)):
        if p.house in (1, 4, 7, 10):
            out.append(f"{label} в кендре ({p.house}) — сила и устойчивость результата.")
        elif p.house in (5, 9):
            out.append(f"{label} в триконе ({p.house}) — удача и поддержка.")
        elif p.house in (6, 8, 12):
            out.append(f"{label} в дустхане ({p.house}) — препятствия, потери, задержки.")

    # Луна как ум вопрошающего
    out.append(
        f"Луна: {C.SIGNS[moon.sign]}, {moon.house}-й дом, "
        f"накшатра {C.NAKSHATRAS[moon.nakshatra]} "
        f"(владыка {moon.nak_lord}), {moon.dignity}."
    )
    moon_aspected_by = [
        n
        for n, items in chart.aspects.items()
        if any(f"({C.SIGNS[moon.sign]})" in it for it in items)
    ]
    if moon_aspected_by:
        ben = [n for n in moon_aspected_by if n in C.BENEFICS]
        mal = [n for n in moon_aspected_by if n in C.MALEFICS]
        if ben:
            out.append(
                "Луну аспектируют благодетели: "
                + ", ".join(ben)
                + " — поддержка, благоприятный исход."
            )
        if mal:
            out.append("Луну аспектируют вредители: " + ", ".join(mal) + " — беспокойство, помехи.")

    # Занятость дома вопроса
    occupants = [n for n, p in chart.planets.items() if p.house == qh]
    if occupants:
        ben = [n for n in occupants if n in C.BENEFICS]
        mal = [n for n in occupants if n in C.MALEFICS]
        out.append(
            f"В {qh}-м доме находятся: "
            + ", ".join(occupants)
            + (f"; благодетели: {', '.join(ben)}" if ben else "")
            + (f"; вредители: {', '.join(mal)}" if mal else "")
            + "."
        )
    else:
        out.append(f"{qh}-й дом пуст — судим по его владыке и аспектам.")

    # Аруда
    al_house = houses_between(chart.asc_sign, chart.aruda_lagna)
    out.append(
        f"Аруда лагна (AL): {C.SIGNS[chart.aruda_lagna]} — {al_house}-й дом от лагны "
        "(как ситуация выглядит со стороны)."
    )
    qa_house = houses_between(chart.asc_sign, chart.question_house_aruda)
    out.append(
        f"Аруда {qh}-го дома (A{qh}): {C.SIGNS[chart.question_house_aruda]} — "
        f"{qa_house}-й дом от лагны."
    )

    # 8-й и 12-й от дома вопроса — разрушение результата
    for n, p in chart.planets.items():
        rel = houses_between(qh_sign, (chart.asc_sign + p.house - 1) % 12)
        if rel in (8, 12) and n in C.MALEFICS:
            out.append(
                f"{n} (вредитель) в {rel}-м доме от дома вопроса — "
                f"фактор разрушения/утечки результата."
            )

    # Даша
    out.append(
        f"Вимшоттари на момент вопроса: {chart.dasha['строка']} "
        f"(антар-даша до {chart.dasha['антар_до']})."
    )
    for lord_name, role in ((chart.dasha["маха"], "маха"), (chart.dasha["антар"], "антар")):
        p = chart.planets.get(lord_name)
        if p:
            out.append(f"Владыка {role}-даши {lord_name} — {p.house}-й дом, {p.dignity}.")

    # Панчанга
    out.append("Панчанга: " + "; ".join(f"{k} — {v}" for k, v in chart.panchanga.items()) + ".")
    return out


def render_chart_text(chart: PrashnaChart) -> str:
    """Человекочитаемая карта для пользователя и для передачи в LLM."""
    lines: list[str] = []
    lines.append(f"Место: {chart.place} ({chart.lat:.4f}, {chart.lon:.4f}), {chart.tz_name}")
    lines.append(
        f"Время вопроса: {chart.when_local.strftime('%d.%m.%Y %H:%M:%S')} (местное) / "
        f"{chart.when_utc.strftime('%H:%M:%S')} UTC"
    )
    lines.append(f"Аянамша: {chart.ayanamsa_value:.4f}°, дома — целый знак")
    lines.append("")
    lines.append(
        f"Лагна: {C.SIGNS[chart.asc_sign]} ({C.SIGNS_SANSKRIT[chart.asc_sign]}) "
        f"{dms(chart.asc_deg)}, накшатра {C.NAKSHATRAS[chart.asc_nak]} "
        f"пада {chart.asc_pada}, "
        f"владыка лагны — {C.SIGN_LORDS[chart.asc_sign]}"
    )
    lines.append("")
    lines.append("Планеты (D1):")
    for name in C.PLANETS:
        p = chart.planets[name]
        flags = []
        if p.retro:
            flags.append("R")
        if p.combust:
            flags.append("сожж.")
        flag = f" [{', '.join(flags)}]" if flags else ""
        lines.append(
            f"  {name:9s} {C.SIGNS[p.sign]:10s} {dms(p.deg):>10s}  дом {p.house:>2d}  "
            f"{C.NAKSHATRAS[p.nakshatra]}-{p.pada} (вл. {p.nak_lord})  {p.dignity}{flag}"
        )
    lines.append("")
    lines.append("Дома (целый знак) и их владыки:")
    for h in range(1, 13):
        hs = (chart.asc_sign + h - 1) % 12
        occ = [n for n, p in chart.planets.items() if p.house == h]
        lines.append(
            f"  {h:>2d} {C.SIGNS[hs]:10s} влад. {chart.house_lords[h]}"
            + (f" | занимают: {', '.join(occ)}" if occ else "")
        )
    lines.append("")
    lines.append("Аспекты (граха дришти по знакам):")
    for name in C.PLANETS:
        lines.append(f"  {name}: " + "; ".join(chart.aspects[name]))
    lines.append("")
    lines.append("Варги:")
    header = "  " + "".join(f"{k:>6s}" for k in ["D1", "D2", "D3", "D4", "D7", "D9", "D10", "D12"])
    lines.append(f"  {'':10s}" + header)
    for key in ["Лагна", *C.PLANETS]:
        row = f"  {key:10s}"
        for dv in ["D1", "D2", "D3", "D4", "D7", "D9", "D10", "D12"]:
            row += f"{chart.vargas[dv][key][:5]:>6s}"
        lines.append(row)
    lines.append("")
    lines.append(f"Аруда лагна: {C.SIGNS[chart.aruda_lagna]}")
    lines.append(f"Дом вопроса: {chart.question_house} — {C.HOUSE_MEANINGS[chart.question_house]}")
    lines.append(f"Аруда дома вопроса: {C.SIGNS[chart.question_house_aruda]}")
    lines.append(f"Вимшоттари даша: {chart.dasha['строка']}")
    lines.append("Панчанга: " + "; ".join(f"{k}: {v}" for k, v in chart.panchanga.items()))
    return "\n".join(lines)


def render_short(chart: PrashnaChart) -> str:
    """Компактная сводка для сообщения в Telegram."""
    p = chart.planets
    rows = []
    for name in C.PLANETS:
        pp = p[name]
        mark = "R" if pp.retro and name not in ("Раху", "Кету") else ""
        rows.append(
            f"{C.PLANET_SHORT[name]}{mark} {C.SIGNS[pp.sign][:3]} {int(pp.deg):02d}° д{pp.house}"
        )
    return (
        f"<b>Лагна:</b> {C.SIGNS[chart.asc_sign]} {dms(chart.asc_deg)} "
        f"({C.NAKSHATRAS[chart.asc_nak]}-{chart.asc_pada})\n"
        f"<b>Дом вопроса:</b> {chart.question_house} — {C.HOUSE_MEANINGS[chart.question_house]}\n"
        f"<b>Планеты:</b> " + " · ".join(rows) + "\n"
        f"<b>Даша:</b> {chart.dasha['строка']}\n"
        f"<b>Панчанга:</b> {chart.panchanga['титхи']}, "
        f"накшатра Луны {chart.panchanga['накшатра_луны']}"
    )
