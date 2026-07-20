"""Роутер — КЛЮЧЕВОЙ инсайт совета: НЕ отдавать мозгу все органы разом (иначе паралич
выбора, галлюцинации, переполнение контекста). По цели отбирает k наиболее релевантных
органов — счёт по пересечению слов цели с именем/назначением/тегами. Детерминированно,
без LLM (в проде роль роутера может взять дешёвая модель или эмбеддинги).
"""

import re


def _tok(s):
    return set(re.findall(r"[a-zа-яё0-9]+", (s or "").lower()))


def score(goal_tokens, organ):
    hay = " ".join([organ.name, organ.purpose, " ".join(organ.tags), " ".join(organ.produces)])
    return len(goal_tokens & _tok(hay))


def route(goal, organs, k=5):
    gt = _tok(goal)
    ranked = sorted(organs, key=lambda o: score(gt, o), reverse=True)
    hit = [o for o in ranked if score(gt, o) > 0]
    if not hit:
        # 0 совпадений по словам — ранжировать нечем; отдаём мозгу ВСЁ, а не произвольные k
        return list(organs)
    return hit[:k]
