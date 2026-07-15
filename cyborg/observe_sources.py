"""Наблюдательный обход органа-источника — рассказывает работу от ПЕРВОГО ЛИЦА.

Зачем: чтобы человек ВИДЕЛ работу киборга, а не только итоговые цифры. Обходит источники
по одному и печатает живьём: зашёл в паблик → прочитал пост → подумал (новое / уже видел).
«Подумал» на уровне источника = решение дедупа (seen_items): свежее беру, виденное мимо.
Настоящее «думаю над ИДЕЕЙ» — это уже СЛЕДУЮЩИЙ орган (ideate), тут только источники.

Свободная зона (НЕ ядро): зовёт idea_engine/organs/collect_source КАК ЕСТЬ, ничего в нём не
меняет; seen_items.load() читает без мутации; в копилку не пишет — чистое наблюдение.

Вывод — построчно в stdout с flush, чтобы пульт (serve._start_proc стримит stdout в живой
.console) показывал строки по мере появления. Запуск: кнопкой «👁 Наблюдать» в пульте
(/api/observe) или из CLI: python observe_sources.py
"""
import os
import sys
import time

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../kiborg/cyborg
_KIBORG = os.path.dirname(_HERE)                             # .../kiborg
sys.path.insert(0, _HERE)                                    # cyborg (harvest, seen_items)
sys.path.insert(0, os.path.join(_KIBORG, "idea_engine"))    # organs.collect_source

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from organs import collect_source  # noqa: E402
import harvest  # noqa: E402
import seen_items  # noqa: E402
from organs_vendored import scrub_secrets  # noqa: E402  (заголовок файла может нести секрет — чистим до показа)

# как называть источник человеку + чем «заходит» туда киборг
WHERE = {
    "hn":          ("Hacker News",           "открываю топ-ленту"),
    "reddit":      ("Reddit r/SideProject",  "стучусь в паблик"),
    "lobsters":    ("Lobsters",              "открываю горячее"),
    "gh_trending": ("GitHub Trending",       "смотрю, что в тренде"),
    "telegram":    ("Telegram",              "захожу в паблик"),
    "files":       ("Папки-источник",        "открываю файлы в папках"),
}
ORDER = ["hn", "reddit", "lobsters", "gh_trending", "telegram", "files"]

_ITEM_PAUSE = 0.28   # пауза между постами — чтобы в пульте строки шли живым потоком, не пачкой
_STEP_PAUSE = 0.35


def say(s="", pause=0.0):
    print(s, flush=True)
    if pause:
        time.sleep(pause)


def main():
    base_env = harvest._harvest_env()
    seen = seen_items.load()  # снимок «уже видел» ОДИН раз, БЕЗ мутации
    tg_channels = base_env.get("telegram_channels") or []

    say("=" * 60)
    say("🤖  Киборг просыпается. Задача: принести свежее сырьё для идей.", _STEP_PAUSE)
    say("    Обхожу источники по одному и рассказываю, что вижу.", _STEP_PAUSE)
    say("")

    # обходим только АКТИВНЫЕ источники (_active_sources через env: включённые ленты + files),
    # а не все зашитые — ORDER задаёт лишь приятный порядок вывода. Юзер выключил ленту тумблером
    # в пульте → наблюдатель молчит про неё.
    active = [s for s in ORDER if s in (base_env.get("sources") or ORDER)]
    grand_read = grand_fresh = 0
    files_paths = base_env.get("files_paths") or []
    for name in active:
        human, verb = WHERE[name]
        tail = ""
        if name == "telegram" and tg_channels:
            tail = f" ({', '.join(tg_channels)})"
        elif name == "files" and files_paths:
            tail = f" ({', '.join(files_paths)})"
        say(f"┌─ {human}{tail}")
        say(f"│  🚪 {verb}…", _STEP_PAUSE)

        env = dict(base_env)
        env.update(source=name, sources=None, n=6, timeout=7)
        t0 = time.time()
        try:
            out = collect_source.run({}, env)
        except Exception as e:
            say(f"│  🔴 сорвался: {type(e).__name__}: {str(e)[:80]}")
            say("│  ⏭  пропускаю\n")
            continue
        dt = time.time() - t0

        if out.get("degraded"):
            why = str(out.get("degraded_reason", "нет ответа"))[:80]
            say(f"│  🔴 не пустили / пусто — {why}")
            say("│  ⏭  пропускаю\n")
            continue

        read = fresh = 0
        for it in out.get("items", []):
            read += 1
            # заголовок файла может содержать секрет (фильтр _files неполон) — чистим ДО показа
            # в консоли пульта (тот же класс, что защита в wiring._run_collect от утечки в промпт)
            title = scrub_secrets.scrub_text((it.get("title") or "").strip())[:72]
            say(f"│  📖 прочитал: «{title}»", _ITEM_PAUSE)
            key = seen_items._item_key(it)
            if key is not None and key in seen:
                say("│     💭 …это уже видел раньше — мимо", _ITEM_PAUSE / 2)
            else:
                fresh += 1
                say("│     💭 …новое! забираю в копилку сырья", _ITEM_PAUSE / 2)
        grand_read += read
        grand_fresh += fresh
        say(f"│  ✅ {human}: прочитал {read}, из них новых {fresh}  ({dt:.1f}с)\n")

    say("└" + "─" * 59)
    say(f"🏁  Обход закончен: прочитал {grand_read} постов, новых (не видел) {grand_fresh}.")
    say("    Свежее дальше подхватил бы орган «придумай идею» (в наблюдении не зову).")
    say("=" * 60)


if __name__ == "__main__":
    main()
