"""detect_house: по одной-двум формулировкам на каждый дом плюс фолбэк."""

from __future__ import annotations

import pytest

from app.astro.prashna import detect_house

CASES = [
    ("стоит ли мне менять что-то в жизни", 1),
    ("вернут ли мне долг вернуть обещали", 2),
    ("получу ли я зарплату в этом месяце", 2),
    ("помирюсь ли я с сестрой", 3),
    ("состоится ли поездка в мае", 3),
    ("куплю ли я квартиру в этом году", 4),
    ("сдам ли я экзамен", 5),
    ("будет ли у нас ребенок", 5),
    ("выиграю ли я суд", 6),
    ("вылечится ли моя болезнь", 6),
    ("состоится ли свадьба", 7),
    ("подпишем ли мы договор с клиентом", 7),
    ("получу ли я наследство", 8),
    ("дадут ли мне визу", 9),
    ("получу ли я оффер на новую работу", 10),
    ("будет ли повышение в должности", 10),
    ("вырастет ли мой доход", 11),
    ("исполнится ли мое желание", 11),
    ("получится ли релокация", 12),
    ("стоит ли соглашаться на эмиграцию", 12),
]


@pytest.mark.parametrize(("question", "house"), CASES)
def test_detect_house(question: str, house: int) -> None:
    assert detect_house(question) == house


def test_fallback_to_first_house() -> None:
    assert detect_house("ну что там вообще") == 1


def test_case_insensitive() -> None:
    assert detect_house("ПОЛУЧУ ЛИ Я ОФФЕР НА НОВУЮ РАБОТУ") == 10


def test_empty_question() -> None:
    assert detect_house("") == 1
