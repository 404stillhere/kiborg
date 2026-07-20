"""Тесты мыслящей части (mind.py) и площадки советников (advisors.py).
Прогон: из корня kiborg `python run_tests.py` (голый pytest из корня врёт — коллизия имён).
"""

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_CY = os.path.dirname(_HERE)
if _CY not in sys.path:
    sys.path.insert(0, _CY)

import advisors  # noqa: E402
import mind  # noqa: E402


class _Fake:
    """Советник-заглушка с фиксированной таблицей баллов (или None = воздержание)."""

    def __init__(self, name, table, escalate=False):
        self.name = name
        self._t = table
        self._e = escalate

    def opine(self, q, opts, ctx):
        return mind.opinion(self._t, escalate=self._e) if self._t is not None else None


OPTS = [{"id": "A", "title": "идея A"}, {"id": "B", "title": "идея B"}]


# --- веса и формула ---------------------------------------------------------


def test_weights_sum_to_one():
    assert abs(sum(mind.WEIGHTS.values()) - 1.0) < 1e-9


def test_weights_exact_values():
    # задано юзером 2026-07-13 — фиксируем, чтобы случайная правка не проскочила молча
    assert mind.WEIGHTS == {"ask_llm": 0.39, "orchestra": 0.20, "rank_ideas": 0.41}


def test_full_council_weighted_sum():
    council = [
        _Fake("ask_llm", {"A": 1.0, "B": 0.0}),
        _Fake("orchestra", {"A": 0.8, "B": 0.2}),
        _Fake("rank_ideas", {"A": 0.0, "B": 1.0}),
    ]
    v = mind.deliberate("q", OPTS, council)
    assert abs(v["scores"]["A"] - 0.55) < 1e-9  # 0.39*1 + 0.20*0.8
    assert abs(v["scores"]["B"] - 0.45) < 1e-9  # 0.20*0.2 + 0.41*1
    assert v["choice_id"] == "A"
    assert v["degraded"] is False
    assert set(v["live"]) == {"ask_llm", "orchestra", "rank_ideas"}


# --- деградация (автономность) ----------------------------------------------


def test_abstain_renormalizes_weights():
    # orchestra выпал: ask_llm 0.39, rank 0.41 -> нормированные 0.4875 / 0.5125
    council = [
        _Fake("ask_llm", {"A": 1.0, "B": 0.0}),
        _Fake("orchestra", None),
        _Fake("rank_ideas", {"A": 0.0, "B": 1.0}),
    ]
    v = mind.deliberate("q", OPTS, council)
    assert v["choice_id"] == "B"  # 0.5125 > 0.4875
    assert v["live"] == ["ask_llm", "rank_ideas"]
    assert any(a["name"] == "orchestra" for a in v["abstained"])
    assert abs(v["scores"]["A"] - 0.4875) < 1e-6
    assert abs(v["scores"]["B"] - 0.5125) < 1e-6


def test_all_abstain_is_degraded():
    v = mind.deliberate("q", OPTS, [_Fake("ask_llm", None), _Fake("rank_ideas", None)])
    assert v["degraded"] is True
    assert v["choice"] is None and v["choice_id"] is None


def test_advisor_crash_is_abstention_not_fatal():
    class _Boom:
        name = "ask_llm"

        def opine(self, q, o, c):
            raise RuntimeError("boom")

    council = [_Boom(), _Fake("rank_ideas", {"A": 1.0, "B": 0.0})]
    v = mind.deliberate("q", OPTS, council)  # упавший советник не роняет совещание
    assert v["choice_id"] == "A"
    assert v["live"] == ["rank_ideas"]
    assert any("error" in a["reason"] for a in v["abstained"])


def test_single_advisor_decides_alone():
    v = mind.deliberate("q", OPTS, [_Fake("rank_ideas", {"A": 0.2, "B": 0.9})])
    assert v["choice_id"] == "B"  # один живой -> его вес нормируется к 1


# --- частные случаи ---------------------------------------------------------


def test_missing_score_counts_as_zero():
    # ask_llm оценил только A; B без балла => 0
    council = [_Fake("ask_llm", {"A": 0.5}), _Fake("rank_ideas", {"A": 0.0, "B": 1.0})]
    v = mind.deliberate("q", OPTS, council)
    # A: 0.4875*0.5=0.244 ; B: 0.5125*1=0.5125 -> B
    assert v["choice_id"] == "B"


def test_tie_break_by_order_when_all_equal():
    # итоги A и B равны (все дали поровну) -> tie-break: старший голос тоже равен -> порядок, A первый
    council = [_Fake("ask_llm", {"A": 1.0, "B": 1.0}), _Fake("rank_ideas", {"A": 1.0, "B": 1.0})]
    v = mind.deliberate("q", OPTS, council)
    assert v["choice_id"] == "A"


def test_tie_break_prefers_senior_advisor():
    # итоги равны, но старший по весу (rank_ideas) склоняет к B -> берём B, не порядок
    # A: ask 0.4875*1 + rank 0.5125*0 = 0.4875 ; B: ask 0.4875*0 + rank 0.5125*0.951 = 0.4875
    council = [_Fake("ask_llm", {"A": 1.0, "B": 0.0}), _Fake("rank_ideas", {"A": 0.0, "B": 0.4875 / 0.5125})]
    v = mind.deliberate("q", OPTS, council)
    assert abs(v["scores"]["A"] - v["scores"]["B"]) < 1e-9  # ничья по итогу
    assert v["choice_id"] == "B"  # rank_ideas (старший) дал B больше


def test_scores_clamped_to_unit_range():
    op = mind.opinion({"A": 5.0, "B": -3.0, "C": "nan?"})
    assert op["scores"]["A"] == 1.0 and op["scores"]["B"] == 0.0
    assert "C" not in op["scores"]  # непарсибельный балл отброшен


# --- живучесть площадки (баги, найденные скептиком 2026-07-13) ---------------


def test_unknown_advisor_name_abstains_not_crashes():
    # советник с именем не из WEIGHTS не должен ронять цикл (KeyError) — воздержание
    council = [_Fake("mystery", {"A": 1.0, "B": 0.0}), _Fake("rank_ideas", {"A": 0.0, "B": 1.0})]
    v = mind.deliberate("q", OPTS, council)
    assert v["choice_id"] == "B"  # решил живой известный советник
    assert v["live"] == ["rank_ideas"]
    assert any(a["name"] == "mystery" for a in v["abstained"])


def test_all_advisors_unknown_is_degraded_not_crash():
    v = mind.deliberate("q", OPTS, [_Fake("mystery", {"A": 1.0})])
    assert v["degraded"] is True and v["choice"] is None


def test_duplicate_ids_collapsed_no_double_count():
    # дубль id не должен учитываться дважды и перевешивать честного лидера
    opts = [{"id": "A", "title": "a1"}, {"id": "A", "title": "a2"}, {"id": "B", "title": "b"}]
    council = [_Fake("ask_llm", {"A": 1.0, "B": 0.0}), _Fake("rank_ideas", {"A": 0.0, "B": 1.0})]
    v = mind.deliberate("q", opts, council)
    assert v["choice_id"] == "B"  # 0.5125 > 0.4875, дубль A не раздут
    assert abs(v["scores"]["A"] - 0.4875) < 1e-6


def test_nan_score_is_dropped_not_winning():
    op = mind.opinion({"A": float("nan"), "B": 0.5})
    assert "A" not in op["scores"]  # NaN отброшен, не просочился через клэмп
    council = [_Fake("ask_llm", {"A": float("nan"), "B": 0.5}), _Fake("rank_ideas", {"A": 0.0, "B": 1.0})]
    v = mind.deliberate("q", OPTS, council)
    assert v["choice_id"] == "B"  # B выигрывает, NaN-A не перебивает


def test_empty_options_is_degraded_not_crash():
    v = mind.deliberate("q", [], [_Fake("rank_ideas", {"A": 1.0})])
    assert v["degraded"] is True and v["choice"] is None


def test_single_option_returned_as_choice():
    v = mind.deliberate("q", [{"id": "solo", "title": "x"}], [_Fake("rank_ideas", {"solo": 1.0})])
    assert v["choice_id"] == "solo"


# --- иерархия think(): арбитр всегда, интуиция зовёт совет (виденье юзера 2026-07-13) ---


def _hier(rank, intu, orch, escalate=False):
    return [_Fake("rank_ideas", rank), _Fake("ask_llm", intu, escalate=escalate), _Fake("orchestra", orch)]


def test_think_confident_intuition_does_not_wake_council():
    # интуиция уверена (escalate=False) -> orchestra НЕ голосует
    v = mind.think("q", OPTS, _hier({"A": 0.0, "B": 1.0}, {"A": 1.0, "B": 0.0}, {"A": 1.0, "B": 0.0}, escalate=False))
    assert v["council_woken"] is False
    assert set(v["live"]) == {"rank_ideas", "ask_llm"}
    # A: 0.4875 ; B: 0.5125 -> B (арбитр весомее)
    assert v["choice_id"] == "B"


def test_think_unsure_intuition_wakes_council():
    # интуиция не уверена (escalate=True) -> orchestra будят, голосуют все трое
    v = mind.think("q", OPTS, _hier({"A": 0.0, "B": 1.0}, {"A": 0.55, "B": 0.45}, {"A": 1.0, "B": 0.0}, escalate=True))
    assert v["council_woken"] is True
    assert set(v["live"]) == {"rank_ideas", "ask_llm", "orchestra"}


def test_think_dead_intuition_never_wakes_council():
    # интуиция воздержалась -> некому эскалировать -> совет спит, решает арбитр
    v = mind.think("q", OPTS, _hier({"A": 1.0, "B": 0.0}, None, {"A": 0.0, "B": 1.0}))
    assert v["council_woken"] is False
    assert v["live"] == ["rank_ideas"]
    assert v["choice_id"] == "A"


def test_think_arbiter_always_votes():
    # даже если интуиции нет в совете вообще — арбитр судит
    v = mind.think("q", OPTS, [_Fake("rank_ideas", {"A": 1.0, "B": 0.0})])
    assert v["live"] == ["rank_ideas"] and v["choice_id"] == "A"


def test_think_no_options_degraded():
    v = mind.think("q", [], _hier({"A": 1.0}, {"A": 1.0}, {"A": 1.0}))
    assert v["degraded"] is True and v["council_woken"] is False


def test_think_all_abstain_degraded():
    v = mind.think("q", OPTS, _hier(None, None, None))
    assert v["degraded"] is True and v["choice"] is None


# --- escalate-эвристика интуиции (advisors) ----------------------------------


def test_intuition_escalates_on_close_scores(monkeypatch):
    # два близких балла -> интуиция поднимает флаг «зовите совет»
    adv = advisors.AskLlmAdvisor()
    monkeypatch.setattr(adv, "_ask", lambda chain, prompt, budget: '{"scores":{"A":60,"B":55}}')
    op = adv.opine("q", OPTS, {"llm_chain": [{"id": "x"}]})
    assert op is not None and op["escalate"] is True


def test_intuition_confident_on_far_scores(monkeypatch):
    adv = advisors.AskLlmAdvisor()
    monkeypatch.setattr(adv, "_ask", lambda chain, prompt, budget: '{"scores":{"A":90,"B":20}}')
    op = adv.opine("q", OPTS, {"llm_chain": [{"id": "x"}]})
    assert op is not None and op["escalate"] is False


# --- площадка (advisors) ----------------------------------------------------


def test_build_council_shape_and_order():
    c = advisors.build_council()
    assert [a.name for a in c] == ["ask_llm", "orchestra", "rank_ideas"]


def test_ask_llm_abstains_without_chain():
    # нет llm_chain в контексте -> интуиция воздерживается (не лезет в сеть)
    assert advisors.AskLlmAdvisor().opine("q", OPTS, {}) is None


def test_orchestra_abstains_when_off():
    # нет context['orchestra'] -> совет выключен, воздержание
    assert advisors.OrchestraAdvisor().opine("q", OPTS, {}) is None


def test_rank_ideas_advisor_ranks_by_order_offline():
    # без llm rank_ideas берёт порядок -> первый вариант получает балл 1.0
    op = advisors.RankIdeasAdvisor().opine("q", OPTS, {})
    assert op is not None
    assert op["scores"]["A"] == 1.0 and op["scores"]["B"] == 0.0


def test_rank_ideas_advisor_abstains_on_non_idea_options():
    # варианты без title/text (служебная развилка) — арбитр не к месту, воздержание
    op = advisors.RankIdeasAdvisor().opine("q", [{"id": 1}, {"id": 2}], {})
    assert op is None


def test_council_offline_only_arbiter_alive():
    # интеграция: пустой контекст -> жив только rank_ideas, выбор детерминирован
    v = mind.deliberate("какая лучше?", OPTS, advisors.build_council(), {})
    assert v["live"] == ["rank_ideas"]
    assert v["degraded"] is False
    assert v["choice_id"] == "A"


def test_orchestra_score_verdicts_normalizes_and_fail_closed():
    # неизвестный/None вердикт -> 0.0 (fail-closed, как parse_review в organ.py), НЕ нейтральные
    # 0.5; регистр/пробелы нормализуются, чтобы мис-регистр одобрения не топился вниз.
    S = advisors.OrchestraAdvisor._score_verdicts
    assert S(["approve"]) == 1.0
    assert S(["APPROVE"]) == 1.0  # регистр нормализован
    assert S([" blocked "]) == 0.0  # пробелы обрезаны
    assert S(["changes_requested"]) == 0.5
    assert S(["reject"]) == 0.0  # неизвестный -> fail-closed 0.0 (раньше было бы 0.5)
    assert S([None]) == 0.0  # None -> fail-closed
    assert S(["approve", "blocked"]) == 0.5  # среднее двух
    assert S([]) == 0.0  # пусто -> 0.0, без деления на ноль


def test_arbiter_abstains_when_key_present_but_call_fails():
    # content_llm ПЕРЕДАН, но вернул мусор -> rank_ideas на fallback. Арбитр ВОЗДЕРЖИВАЕТСЯ (None),
    # а НЕ голосует порядком как «живой» голос совета (audit medium, часть-b): фолбэк при живом
    # ключе = сбой сети/парса, не суждение. mind увидит воздержание и не зачтёт вес 0.41 на мусоре.
    adv = advisors.RankIdeasAdvisor()
    opts = [{"id": "A", "title": "идея А"}, {"id": "B", "title": "идея Б"}, {"id": "C", "title": "идея В"}]
    op = adv.opine("отбери лучшую", opts, {"content_llm": lambda p: "не json мусор"})
    assert op is None


def test_arbiter_votes_offline_with_fallback_rationale():
    # БЕЗ ключа (offline) порядок-фолбэк — ШТАТНЫЙ детерминированный судья: арбитр ГОЛОСУЕТ
    # (не воздерживается), а rationale честно «fallback(порядок)».
    adv = advisors.RankIdeasAdvisor()
    opts = [{"id": "A", "title": "идея А"}, {"id": "B", "title": "идея Б"}, {"id": "C", "title": "идея В"}]
    op = adv.opine("отбери лучшую", opts, {})  # нет content_llm/llm
    assert op is not None
    assert "fallback(порядок)" in op["rationale"]
    assert "рубрика/llm" not in op["rationale"]


def test_intuition_imputes_mean_for_omitted_ids():
    # модель НЕ вернула балл для id "C" (ошибка форматирования) -> интуиция ставит СРЕДНЕЕ реальных
    # баллов, а НЕ жёсткий 0 (иначе mind._tally утопил бы вариант, что судья ценит — mind.py:84).
    adv = advisors.AskLlmAdvisor()
    adv._ask = lambda chain, prompt, budget: '{"scores":{"A":80,"B":40}}'  # id C пропущен
    opts = [{"id": "A", "title": "идея А"}, {"id": "B", "title": "идея Б"}, {"id": "C", "title": "идея В"}]
    op = adv.opine("оцени", opts, {"llm_chain": [{"id": "x"}]})
    assert op is not None
    sc = op["scores"]
    assert sc["A"] == 0.8 and sc["B"] == 0.4
    assert "C" in sc  # пропущенный НЕ потерян
    assert abs(sc["C"] - 0.6) < 1e-9  # среднее (0.8+0.4)/2, НЕ 0
    assert "2/3" in op["rationale"]  # честно: оценено 2 из 3


def test_intuition_no_imputation_when_all_rated():
    adv = advisors.AskLlmAdvisor()
    adv._ask = lambda chain, prompt, budget: '{"scores":{"A":80,"B":40,"C":60}}'
    opts = [{"id": "A", "title": "а"}, {"id": "B", "title": "б"}, {"id": "C", "title": "в"}]
    op = adv.opine("оцени", opts, {"llm_chain": [{"id": "x"}]})
    assert op["scores"] == {"A": 0.8, "B": 0.4, "C": 0.6}


def test_arbiter_rationale_honest_on_live():
    # валидный ответ судьи -> rationale честно «llm»
    adv = advisors.RankIdeasAdvisor()
    opts = [{"id": "A", "title": "идея А"}, {"id": "B", "title": "идея Б"}, {"id": "C", "title": "идея В"}]
    op = adv.opine("отбери лучшую", opts, {"content_llm": lambda p: '{"top":[2,0]}'})
    assert op is not None
    assert "рубрика/llm" in op["rationale"]
