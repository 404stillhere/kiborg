"""Тесты копилки идей (автономный режим — сбор БЕЗ потолка, с дедупом).

Фиксируем:
  1. НЕТ ПОТОЛКА: в копилку влезает больше 3 идей (в отличие от инбокса cap=3).
  2. ДЕДУП: повтор того же заголовка не пишется дважды; разные идеи — обе проходят.
  3. ПЕРСИСТЕНТНОСТЬ: копилка переживает пересоздание (читается из .jsonl), .md пишется.
  4. ОРГАН stash_sink: consumes ideas_safe, produces delivered; терминал графа = delivered.
  5. HARVEST-конвейер: build_harvest_organs даёт цепочку до stash-стока, инбокс НЕ трогается.
Пишем во временную папку — реальную копилку не трогаем.
"""
import json
import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import brain  # noqa: E402
import stash as stash_mod  # noqa: E402
import stash_sink  # noqa: E402
import wiring  # noqa: E402


class TestStash(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="stash_")
        self.jsonl = os.path.join(self.tmp, "idea_stash.jsonl")
        self.md = os.path.join(self.tmp, "idea_stash.md")

    def _new(self):
        return stash_mod.Stash(jsonl=self.jsonl, md=self.md)

    def test_no_cap_accepts_many(self):
        st = self._new()
        # заведомо РАЗНЫЕ по значимым словам заголовки (иначе дедуп справедливо схлопнет)
        titles = ["Трекер сна", "Финансовый помощник", "Генератор музыки", "Погодный дашборд",
                  "Рецепты из холодильника", "Планировщик тренировок", "Читалка книг",
                  "Карта велодорожек", "Ассистент переговоров", "Симулятор колоний"]
        for t in titles:
            self.assertTrue(st.add({"title": t, "why": "w"}))
        st.save()
        self.assertEqual(len(st.ideas), 10)  # потолка нет — влезло 10 (инбокс бы взял 3)

    def test_dedup_same_title(self):
        st = self._new()
        self.assertTrue(st.add({"title": "Трекер сна по фазам", "why": "a"}))
        self.assertFalse(st.add({"title": "Трекер сна по фазам", "why": "b"}))  # точный повтор
        self.assertTrue(st.add({"title": "Трекер финансов по категориям", "why": "c"}))  # другое — ок
        self.assertEqual(len(st.ideas), 2)

    def test_persistence_and_md(self):
        st = self._new()
        st.add({"title": "Персистентная идея", "why": "живёт на диске", "effort": "средне", "brain": "llm"})
        st.save()
        self.assertTrue(os.path.exists(self.jsonl))
        self.assertTrue(os.path.exists(self.md))
        # новая копилка читает старое и дедупит его
        st2 = self._new()
        self.assertEqual(len(st2.ideas), 1)
        self.assertFalse(st2.add({"title": "Персистентная идея", "why": "повтор"}))
        md = open(self.md, encoding="utf-8").read()
        self.assertIn("Персистентная идея", md)
        self.assertIn("★ ИИ", md)  # brain=llm помечен звездой

    def test_stashed_at_stamped(self):
        st = self._new()
        st.add({"title": "Штамп времени", "why": "w"})
        self.assertIn("stashed_at", st.ideas[0])

    def test_atomic_save_no_temp_left(self):
        # атомарная запись: после save нет .tmp-хвостов; повторный save поверх работает
        st = self._new()
        st.add({"title": "Первая", "why": "a"})
        st.save()
        st.add({"title": "Вторая", "why": "b"})
        st.save()  # перезапись поверх существующего
        self.assertFalse(os.path.exists(self.jsonl + ".tmp"))
        self.assertFalse(os.path.exists(self.md + ".tmp"))
        st2 = self._new()  # оба сохранения на диске, файл цел
        self.assertEqual(len(st2.ideas), 2)

    def test_empty_title_not_polluting_seen(self):
        # пустой/служебный заголовок не копится в _seen (как в store) — не засоряет дедуп
        st = self._new()
        st.add({"title": "", "why": "пустой"})
        self.assertEqual(st._seen, [])  # пустая сигнатура не добавлена

    def test_load_skips_junk_lines(self):
        # копилка на диске с пустой + битой строкой ДОЛЖНА грузиться, пропустив мусор
        # (целостность: частичная запись/ручная правка не должна ронять загрузку)
        with open(self.jsonl, "w", encoding="utf-8") as f:
            f.write(json.dumps({"title": "Валидная идея", "brain": "llm"}) + "\n")
            f.write("\n")                  # пустая строка — пропустить
            f.write("{битый json\n")       # мусор — пропустить, не упасть
            f.write(json.dumps({"title": "Вторая валидная"}) + "\n")
        st = self._new()
        self.assertEqual([i["title"] for i in st.ideas], ["Валидная идея", "Вторая валидная"])

    def test_dedup_jaccard_near_duplicate(self):
        # НЕ точный повтор, но похожий по значимым словам (Jaccard >= порога) — тоже отсекается.
        # (тесты выше бьют только ТОЧНЫЙ повтор; это покрывает саму Jaccard-ветку дедупа.)
        st = self._new()
        self.assertTrue(st.add({"title": "Трекер сна по фазам глубокого отдыха", "why": "a"}))
        # те же значимые слова + одно лишнее -> множества РАЗНЫЕ (не точный повтор), пересечение высокое
        self.assertFalse(st.add({"title": "Трекер сна по фазам глубокого отдыха робота", "why": "b"}))
        self.assertTrue(st.add({"title": "Генератор музыки из текста", "why": "c"}))  # другое — проходит
        self.assertEqual(len(st.ideas), 2)

    def test_add_rejects_non_dict(self):
        # add() принимает только словарь; строка/None отвергаются без падения
        st = self._new()
        self.assertFalse(st.add("не словарь"))
        self.assertFalse(st.add(None))
        self.assertEqual(len(st.ideas), 0)

    def test_dedup_survives_empty_sig_from_disk(self):
        # идея с пустым заголовком, загруженная с диска, кладёт пустую сигнатуру в _seen;
        # дедуп следующей реальной идеи её проходит (не падает на пустом множестве)
        with open(self.jsonl, "w", encoding="utf-8") as f:
            f.write(json.dumps({"title": "", "why": "пусто"}) + "\n")  # -> _seen получит ""
        st = self._new()
        self.assertTrue(st.add({"title": "Настоящая идея про погоду", "why": "w"}))

    def test_sink_contract(self):
        organs = wiring.build_organs()
        # sink органа нет в обычной сборке — он только в harvest
        self.assertNotIn("stash_ideas", [o.name for o in organs])
        harvest = wiring.build_harvest_organs()
        sink = next(o for o in harvest if o.name == "stash_ideas")
        self.assertEqual(sink.role, "sink")
        self.assertEqual(sink.consumes, ["ideas_safe"])
        self.assertEqual(sink.produces, ["delivered"])

    def test_harvest_terminal_and_no_inbox(self):
        harvest = wiring.build_harvest_organs()
        names = [o.name for o in harvest]
        # цепочка идей есть (вкл. редактор читаемости), инбокс-доставка (deliver) и finish — НЕТ
        self.assertEqual(set(names),
                         {"collect_source", "ideate", "rank_ideas", "readability_gate",
                          "scrub_secrets", "stash_ideas"})
        self.assertNotIn("deliver", names)
        self.assertNotIn("finish_sink", names)
        # мозг доводит «идеи» до терминала delivered через stash-сток
        self.assertEqual(brain.infer_deliverable("приноси свежие идеи в копилку", harvest), "delivered")

    def test_sink_run_writes_stash(self):
        # монкипатчим пути копилки на временные — не трогаем реальную
        orig_j, orig_m = stash_mod.JSONL_PATH, stash_mod.MD_PATH
        stash_mod.JSONL_PATH, stash_mod.MD_PATH = self.jsonl, self.md
        try:
            res = stash_sink.run({"ideas_safe": [
                {"title": "Умный будильник по фазам сна", "why": "w"},
                {"title": "Агрегатор рецептов из остатков", "why": "w"},
                {"title": "Умный будильник по фазам сна", "why": "дубль"},  # дедуп отсечёт
            ]}, {})
            self.assertEqual(res["delivered"], 2)
            self.assertEqual(res["stash_total"], 2)
            self.assertTrue(os.path.exists(self.jsonl))
        finally:
            stash_mod.JSONL_PATH, stash_mod.MD_PATH = orig_j, orig_m

    def test_sink_drops_stub_in_llm_mode(self):
        # ключ есть (llm_mode) -> болванки (brain=stub) НЕ копим (осечка парса), llm-идеи копим
        orig_j, orig_m = stash_mod.JSONL_PATH, stash_mod.MD_PATH
        stash_mod.JSONL_PATH, stash_mod.MD_PATH = self.jsonl, self.md
        try:
            env = {"content_llm": lambda p: "x"}  # признак llm-режима
            res = stash_sink.run({"ideas_safe": [
                {"title": "Реальная идея от модели", "why": "w", "brain": "llm"},
                {"title": "Идея по мотиву: какой-то заголовок HN", "why": "проверить", "brain": "stub"},
            ]}, env)
            self.assertEqual(res["delivered"], 1)       # только llm-идея
            self.assertEqual(res["dropped_stub"], 1)    # болванка отброшена
        finally:
            stash_mod.JSONL_PATH, stash_mod.MD_PATH = orig_j, orig_m

    def test_sink_keeps_stub_without_key(self):
        # ключа нет (stub-режим) -> болванки ожидаемы, копим как есть
        orig_j, orig_m = stash_mod.JSONL_PATH, stash_mod.MD_PATH
        stash_mod.JSONL_PATH, stash_mod.MD_PATH = self.jsonl, self.md
        try:
            res = stash_sink.run({"ideas_safe": [
                {"title": "Болванка без ключа", "why": "w", "brain": "stub"},
            ]}, {})  # env без llm -> stub-режим
            self.assertEqual(res["delivered"], 1)
            self.assertEqual(res["dropped_stub"], 0)
        finally:
            stash_mod.JSONL_PATH, stash_mod.MD_PATH = orig_j, orig_m


if __name__ == "__main__":
    unittest.main(verbosity=2)
