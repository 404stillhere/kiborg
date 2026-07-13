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


def test_intuition_is_closerouter_chain():
    # реш. юзера: интуиция — цепочка на ОДНОМ ключе closerouter; gemini/sambanova ушли в совет
    p = _keys_file(GEMINI_API_KEY="g", SAMBANOVA_API_KEY="s", CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert all(c["baseUrl"] == "https://api.closerouter.dev/v1/chat/completions" for c in chain)
        assert all(c["apiKey"] == "cr" for c in chain)              # весь фолбэк на одном ключе
        ids = [c["id"] for c in chain]
        assert "gemini" not in ids and "sambanova" not in ids
    finally:
        os.remove(p)

def test_intuition_fallback_order():
    # порядок цепочки задан юзером: deepseek -> glm5 -> muse-spark -> codex-spark
    p = _keys_file(CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert [c["model"] for c in chain] == [
            "deepseek/deepseek-v4-pro", "z-ai/glm-5", "meta/muse-spark-1.1", "openai/gpt-5.3-codex-spark"]
    finally:
        os.remove(p)

def test_intuition_empty_without_closerouter_key():
    # без ключа closerouter вся цепочка пуста -> интуиция воздержится
    p = _keys_file(GEMINI_API_KEY="g")
    try:
        assert keychain.build_chain(p) == []
    finally:
        os.remove(p)


def test_intuition_chain_length():
    # вся цепочка интуиции — 4 модели на ключе closerouter
    p = _keys_file(CLOSEROUTER_API_KEY="cr")
    try:
        chain = keychain.build_chain(p)
        assert len(chain) == 4
        assert all(c["baseUrl"] == "https://api.closerouter.dev/v1/chat/completions" for c in chain)
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
