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


def test_intuition_is_mistral_chain():
    # реш. юзера 2026-07-20: интуиция — цепочка closerouter (muse-spark → deepseek-v4-pro →
    # nemotron-3-ultra). Все 3 модели на ОДНОМ ключе CLOSEROUTER_API_KEY (build_chain берёт
    # keys[key] per-entry, поэтому один ключ на несколько моделей поддержан).
    p = _keys_file(CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert len(chain) == 3
        muse = chain[0]
        assert muse["id"] == "muse-spark"
        assert muse["baseUrl"] == "https://api.closerouter.dev/v1/chat/completions"
        assert muse["apiKey"] == "cr"
        assert muse["model"] == "meta/muse-spark-1.1"
        deepseek = chain[1]
        assert deepseek["id"] == "deepseek"
        assert deepseek["model"] == "deepseek/deepseek-v4-pro"
        nemotron = chain[2]
        assert nemotron["id"] == "nemotron"
        assert nemotron["model"] == "nvidia/nemotron-3-ultra"
    finally:
        os.remove(p)


def test_intuition_fallback_order():
    # порядок цепочки задан юзером: muse-spark → deepseek-v4-pro → nemotron-3-ultra
    p = _keys_file(CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert [c["model"] for c in chain] == [
            "meta/muse-spark-1.1",
            "deepseek/deepseek-v4-pro",
            "nvidia/nemotron-3-ultra",
        ]
    finally:
        os.remove(p)


def test_intuition_drops_entries_without_key():
    # элемент цепочки без ключа выпадает (а не роняет всю цепочку): нет CLOSEROUTER -> пусто,
    # интуиция воздержится
    p_muse = _keys_file(CLOSEROUTER_API_KEY="cr")
    p_none = _keys_file()
    try:
        chain = keychain.build_chain(p_muse)
        assert [c["id"] for c in chain] == ["muse-spark", "deepseek", "nemotron"]
        assert keychain.build_chain(p_none) == []
    finally:
        os.remove(p_muse)
        os.remove(p_none)


def test_intuition_chain_length():
    # вся цепочка интуиции — 3 модели (muse-spark → deepseek → nemotron), когда ключ есть
    p = _keys_file(CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert len(chain) == 3
    finally:
        os.remove(p)


def test_chain_summary_has_no_secrets():
    # СТРАЖ инцидента 2026-07-16: chain_summary для логов/отладки НЕ должна нести секретные
    # значения (apiKey/baseUrl) — только id+model. print(chain)/_chain() в лог утёк ключами;
    # отладка и пульт обязаны печатать chain_summary. Проверка: ни ключ, ни URL не в строке.
    SECRET = "sk-SUPER-SECRET-VALUE-xyz"
    p = _keys_file(CLOSEROUTER_API_KEY=SECRET)
    try:
        summary = keychain.chain_summary(p)
        assert SECRET not in summary  # значение ключа не утекло
        assert "https://" not in summary  # endpoint не утёк
        assert "muse-spark(" in summary  # все 3 моделей на месте
        assert "deepseek(" in summary
        assert "nemotron(" in summary
        assert keychain.chain_summary(_keys_file()) == ""  # пустая цепь -> '' без секретов
    finally:
        os.remove(p)


def test_gemini_disabled_not_in_council():
    # реш. юзера 2026-07-21: gemini geoblocked с сети юзера (HTTP 400 "User location is not
    # supported") — отключён от киборга. Спека/ключ остаются (вернуть = убрать из _COUNCIL_DISABLED),
    # но в совете его быть НЕ должно, даже с ключом.
    p = _keys_file(GEMINI_API_KEY="g")
    try:
        assert "gemini" not in keychain.council_models(p)
        assert "gemini" in keychain._COUNCIL_SPEC  # спека на месте
        assert "gemini" in keychain._COUNCIL_DISABLED  # флаг отключения установлен
        # один только gemini-ключ -> совет пуст (других рецензентов с ключом нет)
        assert keychain.orchestra_context(p) is None
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
    # реш. юзера: все модели, кроме muse-spark/deepseek/nemotron (интуиция) и отключённых
    # (cerebras, gemini) — рецензенты совета.
    p = _keys_file(
        SAMBANOVA_API_KEY="a",
        GROQ_API_KEY="b",
        CLOSEROUTER_API_KEY="c",
        MISTRAL_API_KEY="d",
        OPENROUTER_API_KEY="e",
        GEMINI_API_KEY="g",
        COHERE_API_KEY="h",
        NVIDIA_API_KEY="i",
        CEREBRAS_API_KEY="j",
    )
    try:
        council = set(keychain.council_models(p))
        assert "muse-spark" not in council  # интуиция не в совете
        assert "deepseek" not in council  # интуиция не в совете
        assert "nemotron" not in council  # интуиция не в совете
        assert "gemini" not in council  # отключён 2026-07-21 (geoblocked)
        assert "cerebras" not in council  # отключён (ключ 403)
        assert {"sambanova", "groq", "mistral", "openrouter", "cohere", "nvidia"} <= council
    finally:
        os.remove(p)


def test_cerebras_disabled_but_not_deleted():
    # cerebras отключён (реш. юзера) — не в совете, даже с ключом; но спека НЕ удалена
    p = _keys_file(CEREBRAS_API_KEY="j", GEMINI_API_KEY="g")
    try:
        assert "cerebras" not in keychain.council_models(p)
        assert "cerebras" in keychain._COUNCIL_SPEC  # спека на месте (вернуть = убрать из DISABLED)
        assert "cerebras" in keychain._COUNCIL_DISABLED
    finally:
        os.remove(p)


def test_council_chat_rejects_unknown_reviewer():
    # нужен хотя бы один живой рецензент, чтобы make_council_chat дал chat(); gemini отключён,
    # поэтому берём mistral (всегда в строю по умолчанию).
    p = _keys_file(MISTRAL_API_KEY="m")
    try:
        chat = keychain.make_council_chat(p)
        assert chat is not None
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
    # muse-spark/deepseek/nemotron исключены (интуиция), gemini отключён (geoblocked) → берём
    # mistral + cohere + openrouter = 3 живых рецензента.
    p = _keys_file(MISTRAL_API_KEY="m", COHERE_API_KEY="c", OPENROUTER_API_KEY="o")
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
    assert time.time() - t < 5  # уложился в ~1с (deadline), а не ждал 30с
