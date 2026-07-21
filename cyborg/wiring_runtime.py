"""Общий хелпер для контентных органов (ideate/rank/readability).

Вынесено из монолита wiring.py: одна зона ответственности — выбрать живую модель
из env для генерации/судьи/редактора. Чистая функция, без побочных эффектов.
"""


def _content_llm(env):
    """Живая модель для контентных органов (ideate/rank): env['content_llm'], иначе общий env['llm'].
    Так генератор и судья идут на живой модели, даже когда мозг оставлен на детерминированном stub."""
    env = env if isinstance(env, dict) else {}
    llm = env.get("content_llm") or env.get("llm")
    return llm if callable(llm) else None
