"""Тесты keychain — распределение ролей ask_llm (интуиция) / orchestra (совет).
Не зависят от реального llm_keys.env: используют временный файл. Прогон: `python run_tests.py`.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_CY = os.path.dirname(_HERE)
if _CY not in sys.path:
    sys.path.insert(0, _CY)

import keychain  # noqa: E402


def _keys_file(**pairs):
    fd, path = tempfile.mkstemp(suffix=".env")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("# test keys\n")
        for k, v in pairs.items():
            f.write(f"{k}={v}\n")
    return path


def test_intuition_is_hybrid_chain():
    # реш. юзера 2026-07-16: интуиция — ГИБРИД: gemini на нативном Google-ключе + muse-spark
    # на ключе closerouter. ДВА разных endpoint/ключа в одной цепочке (build_chain берёт
    # keys[key] per-entry, поэтому смешивание поддержано). gemini здесь — первичная интуиции,
    # а не рецензент совета.
    p = _keys_file(GEMINI_API_KEY="g", SAMBANOVA_API_KEY="s", CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert len(chain) == 2
        gem = chain[0]
        assert gem["id"] == "gemini"
        assert gem["baseUrl"] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert gem["apiKey"] == "g"                       # нативный ключ подписки
        muse = chain[1]
        assert muse["id"] == "muse-spark"
        assert muse["baseUrl"] == "https://api.closerouter.dev/v1/chat/completions"
        assert muse["apiKey"] == "cr"                     # отдельный ключ closerouter
    finally:
        os.remove(p)

def test_intuition_fallback_order():
    # порядок цепочки задан юзером: gemini(натив) -> muse-spark(closerouter)
    p = _keys_file(GEMINI_API_KEY="g", CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert [c["model"] for c in chain] == [
            "gemini-2.5-flash-lite", "meta/muse-spark-1.1"]
    finally:
        os.remove(p)

def test_intuition_drops_entries_without_key():
    # элемент цепочки без ключа выпадает (а не роняет всю цепочку): нет closerouter -> остаётся
    # только gemini; нет gemini -> только muse-spark; нет обоих -> пусто, интуиция воздержится
    p_gem_only = _keys_file(GEMINI_API_KEY="g")
    p_cr_only = _keys_file(CLOSEROUTER_API_KEY="cr")
    p_none = _keys_file()
    try:
        assert [c["id"] for c in keychain.build_chain(p_gem_only)] == ["gemini"]
        assert [c["id"] for c in keychain.build_chain(p_cr_only)] == ["muse-spark"]
        assert keychain.build_chain(p_none) == []
    finally:
        os.remove(p_gem_only); os.remove(p_cr_only); os.remove(p_none)


def test_intuition_chain_length():
    # вся цепочка интуиции — 2 модели (гибрид gemini + muse-spark), когда оба ключа есть
    p = _keys_file(GEMINI_API_KEY="g", CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert len(chain) == 2
    finally:
        os.remove(p)


def test_chain_summary_has_no_secrets():
    # СТРАЖ инцидента 2026-07-16: chain_summary для логов/отладки НЕ должна нести секретные
    # значения (apiKey/baseUrl) — только id+model. print(chain)/_chain() в лог утёк ключами;
    # отладка и пульт обязаны печатать chain_summary. Проверка: ни ключ, ни URL не в строке.
    SECRET = "sk-SUPER-SECRET-VALUE-xyz"
    p = _keys_file(GEMINI_API_KEY=SECRET, CLOSEROUTER_API_KEY="cr")
    try:
        summary = keychain.chain_summary(p)
        assert SECRET not in summary                      # значение ключа не утекло
        assert "https://" not in summary                  # endpoint не утёк
        assert "gemini(" in summary and "muse-spark(" in summary   # id+model на месте (диагноз работает)
        assert keychain.chain_summary(_keys_file()) == ""  # пустая цепь -> '' без секретов
    finally:
        os.remove(p)


def test_gemini_is_council_reviewer():
    p = _keys_file(GEMINI_API_KEY="g")
    try:
        assert "gemini" in keychain.council_models(p)
        oc = keychain.orchestra_context(p)
        assert oc is not None and oc["models"] == ["gemini"] and callable(oc["chat"])
    finally:
        os.remove(p)


def test_empty_keys_no_chain_no_council():
    p = _keys_file()
    try:
        assert keychain.build_chain(p) == []
        assert keychain.available(p) is False
        assert keychain.council_models(p) == []
        assert keychain.orchestra_context(p) is None
    finally:
        os.remove(p)


def test_all_others_go_to_council():
    # реш. юзера: все модели, кроме closerouter (интуиция) и отключённых, — рецензенты совета
    p = _keys_file(SAMBANOVA_API_KEY="a", GROQ_API_KEY="b", CLOSEROUTER_API_KEY="c",
                   MISTRAL_API_KEY="d", OPENROUTER_API_KEY="e", GEMINI_API_KEY="g",
                   COHERE_API_KEY="h", NVIDIA_API_KEY="i", CEREBRAS_API_KEY="j")
    try:
        council = set(keychain.council_models(p))
        assert "closerouter" not in council            # интуиция не в совете
        assert {"sambanova", "groq", "mistral", "openrouter", "gemini",
                "cohere", "nvidia"} <= council
    finally:
        os.remove(p)

def test_cerebras_disabled_but_not_deleted():
    # cerebras отключён (реш. юзера) — не в совете, даже с ключом; но спека НЕ удалена
    p = _keys_file(CEREBRAS_API_KEY="j", GEMINI_API_KEY="g")
    try:
        assert "cerebras" not in keychain.council_models(p)
        assert "cerebras" in keychain._COUNCIL_SPEC       # спека на месте (вернуть = убрать из DISABLED)
        assert "cerebras" in keychain._COUNCIL_DISABLED
    finally:
        os.remove(p)


def test_council_chat_rejects_unknown_reviewer():
    p = _keys_file(GEMINI_API_KEY="g")
    try:
        chat = keychain.make_council_chat(p)
        # неизвестный рецензент -> raise (совет продолжит с остальными, не молча)
        try:
            chat("unknown-model", "sys", "hi")
            assert False, "должен был бросить"
        except RuntimeError:
            pass
    finally:
        os.remove(p)


def test_orchestra_context_sets_one_wave_max_workers():
    # max_workers = число рецензентов → organ.py гонит их ОДНОЙ волной (не дефолтными 4),
    # иначе мёртвый эндпоинт во 2-й волне удваивал зависание совета (баг 2026-07-14).
    p = _keys_file(GEMINI_API_KEY="g", MISTRAL_API_KEY="m", COHERE_API_KEY="c")
    try:
        oc = keychain.orchestra_context(p)
        assert oc["max_workers"] == len(oc["models"]) == 3
    finally:
        os.remove(p)


def test_with_deadline_returns_and_propagates():
    # нормальный результат проходит насквозь
    assert keychain._with_deadline(lambda: "ok", deadline=5) == "ok"
    # исключение из fn долетает как раньше (контракт review: рецензент падает -> выпадает)
    try:
        keychain._with_deadline(lambda: (_ for _ in ()).throw(ValueError("boom")), deadline=5)
        assert False, "должен был пробросить ValueError"
    except ValueError:
        pass


def test_with_deadline_kills_slow_loris():
    # медленный/висящий вызов НЕ морозит совет — жёсткий wall-clock бросает TimeoutError
    import time
    t = time.time()
    try:
        keychain._with_deadline(lambda: time.sleep(30), deadline=1)
        assert False, "должен был бросить TimeoutError"
    except TimeoutError:
        pass
    assert time.time() - t < 5   # уложился в ~1с (deadline), а не ждал 30с
