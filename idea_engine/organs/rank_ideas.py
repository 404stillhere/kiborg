"""Орган: rank_ideas — судья идей. Из пула кандидатов оставляет ЛУЧШИЕ.

Зачем: генератор (ideate) выдаёт МНОГО идей (напр. 6), но в очередь должны попасть не
первые попавшиеся, а отобранные. Принцип из auto-improve: генератор НЕ судит сам себя —
отдельный ОЦЕНОЧНЫЙ проход. Судья здесь — та же модель, но отдельным вызовом с рубрикой
(оригинальность / польза / выполнимость), в духе tournament-скоринга rank_prompt_variants
из реестра, но под идеи-карточки.

Контракт: run(inputs, env) -> {"ideas_best": [top-k]}.
  env["keep"] — сколько оставить (default 3); env["llm"] — судья callable(prompt)->str.
Без судьи или при непарсибельном ответе — фолбэк: первые keep (детерминированно, идеи не теряем).
"""

import json
import re

RUBRIC = (
    "Ты строгий отборщик идей. Ниже {n} идей-кандидатов (проекты/скиллы/аддоны).\n"
    "Мысленно оцени каждую: оригинальность (вне очевидного) + практическая польза +\n"
    "выполнимость в одиночку. Верни ОДНУ строку JSON и ничего больше:\n"
    '{{"top":[i,j,k]}} — 0-based индексы {keep} ЛУЧШИХ, от лучшей к худшей.\n'
    "Идеи:\n{items}\n"
)


def _pick(raw, n, keep):
    """Индексы top из ответа судьи. Пусто/мусор -> None (наверху фолбэк)."""
    raw = (raw or "").strip()
    idxs = None
    for cand in [raw] + raw.splitlines():
        cand = cand.strip()
        if '"top"' not in cand:
            continue
        try:
            o = json.loads(cand)
            if isinstance(o.get("top"), list):
                idxs = o["top"]
                break
        except Exception:
            continue
    if idxs is None:  # последний шанс — выдрать числа из top:[...]
        m = re.search(r'"top"\s*:\s*\[([0-9,\s]+)\]', raw)
        if m:
            idxs = [int(x) for x in re.findall(r"\d+", m.group(1))]
    if not idxs:
        return None
    seen, out = set(), []
    for i in idxs:
        if isinstance(i, int) and 0 <= i < n and i not in seen:
            seen.add(i)
            out.append(i)
    return out[:keep] or None


def run(inputs, env):
    env = env or {}
    ideas = list((inputs or {}).get("ideas") or [])
    keep = int(env.get("keep", 3))
    if len(ideas) <= keep:
        return {"ideas_best": ideas}  # отбирать не из чего — отдаём как есть
    llm = env.get("llm")
    if callable(llm):
        items = "\n".join(f"{i}. {d.get('title', '')} — {d.get('why', '')[:100]}" for i, d in enumerate(ideas))
        prompt = RUBRIC.format(n=len(ideas), keep=keep, items=items)
        direction = (env.get("direction") or "").strip()
        if direction:  # при прочих равных — идеи В НАПРАВЛЕНИИ выше
            prompt = (
                f"Отбираешь под НАПРАВЛЕНИЕ «{direction}»: при прочих равных идея, "
                f"бьющая в «{direction}», предпочтительнее.\n" + prompt
            )
        rejected = [r for r in (env.get("rejected") or []) if r]
        if rejected:  # похожее на УЖЕ ОТКЛОНЁННОЕ юзером — в топ не брать
            rej = "\n".join("- " + str(r) for r in rejected)
            prompt = "НЕ бери в топ идеи, похожие на эти УЖЕ ОТКЛОНЁННЫЕ (юзер их забраковал):\n" f"{rej}\n" + prompt
        idxs = _pick(llm(prompt), len(ideas), keep)
        if idxs:
            picked = set(idxs)
            chosen = list(idxs)
            # судья вернул МЕНЬШЕ keep -> добираем по порядку, идеи НЕ теряем (обещали топ-keep)
            for i in range(len(ideas)):
                if len(chosen) >= keep:
                    break
                if i not in picked:
                    chosen.append(i)
            return {"ideas_best": [dict(ideas[i], judged=("llm" if i in picked else "fill")) for i in chosen[:keep]]}
    # фолбэк: первые keep (нет судьи / не распарсили) — идеи не теряем
    return {"ideas_best": [dict(d, judged="fallback") for d in ideas[:keep]]}


if __name__ == "__main__":
    pool = [{"title": f"Идея {i}", "why": "почему"} for i in range(6)]
    print("no-llm фолбэк:", [d["title"] for d in run({"ideas": pool}, {"keep": 3})["ideas_best"]])
    fake = lambda p: '{"top":[4,1,2]}'
    print("с судьёй:", [d["title"] for d in run({"ideas": pool}, {"keep": 3, "llm": fake})["ideas_best"]])
