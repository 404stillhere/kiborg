"""Тест органов ультра-режима: pick_cross_sample (отбор) + fuse_ideas (слияние).

Отбор — чистая функция без LLM: детерминизм по seed, «ровно 1 на источник», честный
отказ при <2 ядерных источников (self не считается), мягкое избегание used_ids.
Слияние — вратарь каркаса (роли/механизм/ярлыки/коллаж) + ремонтный вызов; провал =
честный skip БЕЗ stub-заглушки (в одно-карточном режиме болванка хуже пустоты).
Обоснование дизайна — council 2026-08-15 (.brain/councils/2026-08-15_ultra-idea-fusion/).
"""

import json
import os
import sys
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from organs import fuse_ideas, pick_cross_sample  # noqa: E402


def item(src, i, title="заголовок материала"):
    return {"source": src, "id": "%s-%d" % (src, i), "title": title, "url": "https://x/%s/%d" % (src, i)}


def good_card(sources=("reddit", "github")):
    roles = ["кто_и_работа", "механизм", "поверхность", "ограничение", "сигнал"]
    return {
        "digest": [{"source": s, "id": "%s-1" % s, "суть": "суть"} for s in sources],
        "frame": "инди-разработчик закрывает работу «понять, стоит ли делать фичу»",
        "title": "Ночной дайджест спроса по своим же репозиториям",
        "why": "потому что",
        "effort": "средне",
        "source_ids": ["%s-1" % s for s in sources],
        "verification": "спросить 5 человек",
        "fusion": [
            {
                "source": s,
                "id": "%s-1" % s,
                "role": roles[n],
                "took": "берём приём такой-то и применяем",
                "load_bearing": True,
                "collapse": "сломается сбор входа",
            }
            for n, s in enumerate(sources)
        ],
    }


class FakeLLM(object):
    """Отдаёт заготовленные ответы по очереди, пишет полученные промпты."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts = []

    def __call__(self, prompt):
        self.prompts.append(prompt)
        return self.responses.pop(0) if self.responses else "мусор"


# ------------------------------------------------------------------ отбор


class TestPickCrossSample(unittest.TestCase):
    def test_takes_exactly_one_per_source(self):
        items = [item("reddit", i) for i in range(5)] + [item("github", i) for i in range(3)]
        out = pick_cross_sample.run({"items": items, "seed": 1})
        self.assertEqual(len(out["picked"]), 2)
        self.assertEqual(out["sources_used"], ["github", "reddit"])

    def test_same_seed_same_pick(self):
        items = [item("reddit", i) for i in range(9)] + [item("hn", i) for i in range(9)]
        a = pick_cross_sample.run({"items": items, "seed": 42})["picked"]
        b = pick_cross_sample.run({"items": items, "seed": 42})["picked"]
        self.assertEqual([i["id"] for i in a], [i["id"] for i in b])

    def test_different_seed_eventually_differs(self):
        items = [item("reddit", i) for i in range(9)] + [item("hn", i) for i in range(9)]
        seen = {tuple(i["id"] for i in pick_cross_sample.run({"items": items, "seed": s})["picked"]) for s in range(10)}
        self.assertGreater(len(seen), 1)

    def test_skip_when_single_source(self):
        out = pick_cross_sample.run({"items": [item("reddit", i) for i in range(9)], "seed": 1})
        self.assertEqual(out["picked"], [])
        self.assertIn(">=2", out["skip"])

    def test_self_source_does_not_satisfy_minimum(self):
        items = [item("reddit", 1), item("self", 1)]
        self.assertIsNotNone(pick_cross_sample.run({"items": items, "seed": 1})["skip"])

    def test_degraded_sources_reported(self):
        items = [item("reddit", 1), item("github", 1)]
        out = pick_cross_sample.run(
            {"items": items, "seed": 1, "sources_expected": ["reddit", "github", "hn", "telegram"]}
        )
        self.assertIsNone(out["skip"])
        self.assertEqual(out["sources_missing"], ["hn", "telegram"])

    def test_oversample_gives_two_per_source(self):
        items = [item("reddit", i) for i in range(4)] + [item("hn", i) for i in range(4)]
        out = pick_cross_sample.run({"items": items, "seed": 3, "oversample": 2})
        self.assertEqual(len(out["picked"]), 4)

    def test_used_ids_avoided_but_not_fatal(self):
        # used_ids — «source:id», тот же формат, что seen_items (обёртка кормит без перекодирования)
        items = [item("reddit", 1), item("reddit", 2), item("hn", 1)]
        out = pick_cross_sample.run({"items": items, "seed": 5, "used_ids": ["reddit:reddit-1", "hn:hn-1"]})
        self.assertEqual([i["id"] for i in out["picked"] if i["source"] == "reddit"], ["reddit-2"])
        self.assertIn("hn:hn-1", out["reused_ids"])  # выбора не было — берём и честно метим

    def test_seed_required(self):
        with self.assertRaises(ValueError):
            pick_cross_sample.run({"items": [item("reddit", 1)]})

    def test_aux_self_excluded_from_pick_by_default(self):
        # живые прогоны: LLM сам выбрасывает self из fusion — не предлагаем вовсе
        items = [item("reddit", i) for i in range(3)] + [item("github", 1), item("self", 1)]
        out = pick_cross_sample.run({"items": items, "seed": 2})
        self.assertEqual(out["sources_used"], ["github", "reddit"])

    def test_include_aux_adds_self_to_pick(self):
        items = [item("reddit", i) for i in range(3)] + [item("github", 1), item("self", 1)]
        out = pick_cross_sample.run({"items": items, "seed": 2, "include_aux": True})
        self.assertIn("self", out["sources_used"])


# ------------------------------------------------------------------ слияние


class TestFuseIdeas(unittest.TestCase):
    def setUp(self):
        self.picked = [item("reddit", 1), item("github", 1)]

    def _run(self, llm, **kw):
        inputs = {"picked": self.picked, "seed": 7}
        inputs.update(kw)
        return fuse_ideas.run(inputs, {"llm": llm})

    def test_happy_path(self):
        out = self._run(FakeLLM(json.dumps(good_card())))
        self.assertEqual(len(out["ideas"]), 1)
        self.assertEqual(out["attempts"], 1)
        card = out["ideas"][0]
        self.assertEqual(card["mode"], "ultra")
        self.assertEqual(card["seed"], 7)
        self.assertEqual(card["brain"], "llm")  # контракт deliver: не-stub доходит до инбокса

    def test_prompt_contains_roles_and_ban(self):
        llm = FakeLLM(json.dumps(good_card()))
        self._run(llm)
        p = llm.prompts[0]
        self.assertIn("механизм", p)
        self.assertIn("ЗАПРЕЩЕНО", p)
        self.assertIn("reddit-1", p)
        self.assertIn("суть", p)  # регресс опечатки «suть» из council-черновика

    def test_duplicate_roles_rejected(self):
        card = good_card()
        card["fusion"][1]["role"] = card["fusion"][0]["role"]
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertEqual(out["ideas"], [])
        self.assertTrue(any("повторя" in v for v in out["violations"]))

    def test_repair_call_recovers(self):
        card = good_card()
        card["fusion"][0]["role"] = card["fusion"][1]["role"]
        llm = FakeLLM(json.dumps(card), json.dumps(good_card()))
        out = self._run(llm)
        self.assertEqual(len(out["ideas"]), 1)
        self.assertEqual(out["attempts"], 2)
        self.assertIn("ПЕРЕСТРОЙ", llm.prompts[1])

    def test_collage_title_rejected(self):
        card = good_card()
        card["title"] = "Телеграм-бот + CLI + VPN"
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertEqual(out["ideas"], [])

    # Вратарь «+» (council 2026-08-17): голый '+' в маркерах резал плюс-токены языков —
    # «Отладчик C++…» умирал молча и смещал выборку ультра-прогонов. Токены гасятся
    # до проверки маркеров; склейка «слово+слово» остаётся коллажем.
    def test_cpp_title_not_rejected(self):
        card = good_card()
        card["title"] = "Отладчик C++ прямо в редакторе"
        out = self._run(FakeLLM(json.dumps(card)))
        self.assertEqual(len(out["ideas"]), 1)

    def test_gpp_title_not_rejected(self):
        card = good_card()
        card["title"] = "g++ обёртка для сборки мелких проектов"
        out = self._run(FakeLLM(json.dumps(card)))
        self.assertEqual(len(out["ideas"]), 1)

    def test_notepadpp_title_not_rejected(self):
        card = good_card()
        card["title"] = "Тема для Notepad++ в стиле ретро-терминала"
        out = self._run(FakeLLM(json.dumps(card)))
        self.assertEqual(len(out["ideas"]), 1)

    def test_word_glue_title_rejected(self):
        card = good_card()
        card["title"] = "Notion+Obsidian синхронизатор заметок"
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertEqual(out["ideas"], [])
        self.assertTrue(any("склейк" in x for x in out["violations"]))

    def test_label_contribution_rejected(self):
        card = good_card()
        card["fusion"][0]["took"] = "VPN"  # ярлык, не механизм
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertTrue(any("ярлык" in v for v in out["violations"]))

    def test_non_load_bearing_rejected(self):
        card = good_card()
        card["fusion"][1]["load_bearing"] = False
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertTrue(any("несущий" in v for v in out["violations"]))

    def test_missing_source_in_fusion_rejected(self):
        card = good_card()
        card["fusion"] = card["fusion"][:1]
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertTrue(any("не все источники" in v for v in out["violations"]))

    def test_unparsable_gives_skip_not_stub(self):
        out = self._run(FakeLLM("извини, вот мои мысли...", "опять текст"))
        self.assertEqual(out["ideas"], [])
        self.assertIsNotNone(out["fusion_skip"])
        self.assertFalse(any(isinstance(i, dict) and i.get("brain") == "stub" for i in out["ideas"]))

    def test_no_llm_honest_skip(self):
        out = fuse_ideas.run({"picked": self.picked, "seed": 1}, {})
        self.assertEqual(out["ideas"], [])
        self.assertIn("мозга", out["fusion_skip"])

    def test_fenced_json_parsed(self):
        out = self._run(FakeLLM("```json\n" + json.dumps(good_card()) + "\n```"))
        self.assertEqual(len(out["ideas"]), 1)

    def test_trailing_comma_json_parsed(self):
        raw = json.dumps(good_card()).replace(
            '"verification": "спросить 5 человек"', '"verification": "спросить 5 человек",'
        )
        out = self._run(FakeLLM(raw, raw))
        self.assertEqual(len(out["ideas"]), 1)  # висячая запятая чинится, не роняя слияние

    def test_prose_draft_then_valid_json_wins(self):
        # модель «рассуждает» черновым объектом без обязательных полей, потом даёт финальный
        draft = '{"мысль": "надо бы соединить"}'
        out = self._run(FakeLLM("Разберём материалы.\n%s\nФинальный ответ:\n%s" % (draft, json.dumps(good_card()))))
        self.assertEqual(len(out["ideas"]), 1)  # вратарь выбирает первый ГОДНЫЙ объект, не первый попавшийся

    def test_failure_reports_raw_tail(self):
        out = self._run(FakeLLM("проза без json", "снова проза"))
        self.assertTrue(out.get("raw_tail"))  # хвост сырого ответа виден — калибровка не слепая

    def test_combo_hash_stable_and_order_independent(self):
        a = self._run(FakeLLM(json.dumps(good_card())))["combo_hash"]
        self.picked = list(reversed(self.picked))
        b = self._run(FakeLLM(json.dumps(good_card())))["combo_hash"]
        self.assertEqual(a, b)

    def test_direction_and_rejected_injected(self):
        llm = FakeLLM(json.dumps(good_card()))
        self._run(llm, direction="автоматизация рутины", rejected=["ещё один тудушник"])
        self.assertIn("автоматизация рутины", llm.prompts[0])
        self.assertIn("тудушник", llm.prompts[0])

    def test_skip_when_one_source(self):
        self.picked = [item("reddit", 1)]
        out = self._run(FakeLLM())
        self.assertEqual(out["fusion_skip"], "нужно >=2 источников")
        self.assertEqual(out["attempts"], 0)  # LLM не дёргали

    def test_freeform_roles_normalized_and_source_ids_autofilled(self):
        # живой прогон 2026-08-15: «интерфейс_взаимодействия» вместо «поверхность» + забытые
        # source_ids. Коерсия приводит номенклатуру, вратарь проверяет канонические слоты.
        self.picked = [item("reddit", 1), item("github", 1), item("hn", 1)]
        card = good_card(("reddit", "github", "hn"))
        card["fusion"][0]["role"] = "пользователь_инструмента"  # → кто_и_работа
        card["fusion"][1]["role"] = "контекстный_хранитель"  # → механизм
        card["fusion"][2]["role"] = "интерфейс_взаимодействия"  # → поверхность
        del card["source_ids"]  # модель забыла — автозаполним из fusion
        out = self._run(FakeLLM(json.dumps(card)))
        self.assertEqual(len(out["ideas"]), 1)
        f = out["ideas"][0]["fusion"]
        self.assertEqual([x["role"] for x in f], ["кто_и_работа", "механизм", "поверхность"])
        self.assertTrue(all(x.get("role_raw") for x in f))  # оригинал сохранён прозрачно
        self.assertEqual(set(out["ideas"][0]["source_ids"]), {"reddit-1", "github-1", "hn-1"})

    def test_unknown_distinct_role_accepted(self):
        # названия ролей — леса, не планка: непрозонированное «волшебство» проходит,
        # пока слоты различные и вклад несущий (анти-коллаж — в distinctness, не в словах)
        card = good_card()
        card["fusion"][0]["role"] = "волшебство"
        out = self._run(FakeLLM(json.dumps(card)))
        self.assertEqual(len(out["ideas"]), 1)
        self.assertEqual(out["ideas"][0]["fusion"][0]["role"], "волшебство")  # без алиаса — как есть

    def test_missing_role_rejected(self):
        card = good_card()
        card["fusion"][0]["role"] = ""
        out = self._run(FakeLLM(json.dumps(card), json.dumps(card)))
        self.assertEqual(out["ideas"], [])
        self.assertTrue(any("не указана роль" in v for v in out["violations"]))


class TestFusePairsMode(unittest.TestCase):
    """oversample>1: с источника дана ПАРА кандидатов, LLM выбирает одного."""

    def setUp(self):
        self.picked = [item("reddit", 1), item("reddit", 2), item("hn", 1), item("hn", 2)]

    def test_prompt_hints_pair_selection(self):
        llm = FakeLLM(json.dumps(good_card(("reddit", "hn"))))
        fuse_ideas.run({"picked": self.picked, "seed": 1, "select_from_pairs": True}, {"llm": llm})
        self.assertIn("ПАРА", llm.prompts[0])

    def test_pairs_mode_relaxes_full_id_coverage(self):
        # карточка цитирует только ПОЧЕРКНУТЫХ (по одному из пары) — это валидно в режиме пар
        llm = FakeLLM(json.dumps(good_card(("reddit", "hn"))))
        out = fuse_ideas.run({"picked": self.picked, "seed": 1, "select_from_pairs": True}, {"llm": llm})
        self.assertEqual(len(out["ideas"]), 1)

    def test_without_pairs_flag_full_coverage_required(self):
        # тот же ответ в режиме «ровно 1 на источник» обязан цитировать ВСЕ материалы
        llm = FakeLLM(json.dumps(good_card(("reddit", "hn"))), json.dumps(good_card(("reddit", "hn"))))
        out = fuse_ideas.run({"picked": self.picked, "seed": 1}, {"llm": llm})
        self.assertEqual(out["ideas"], [])
        self.assertTrue(any("не покрывает" in v for v in out["violations"]))


if __name__ == "__main__":
    unittest.main()
