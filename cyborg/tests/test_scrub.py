"""Тест вендоренного органа scrub_secrets и его адаптера в обвязке."""

import os
import sys
import tempfile
import unittest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import run  # noqa: E402  (проверяем, что _log_run вычищает лог)
import wiring  # noqa: E402
from organs_vendored import scrub_secrets  # noqa: E402


class TestScrub(unittest.TestCase):
    def test_scrub_text_redacts_secret(self):
        s = scrub_secrets.scrub_text("config API_KEY=sk-abcdefghij0123456789 tail")
        self.assertNotIn("sk-abcdefghij", s)
        self.assertIn("[REDACTED]", s)

    def test_scrub_text_keeps_clean(self):
        clean = "обычный текст без секретов"
        self.assertEqual(scrub_secrets.scrub_text(clean), clean)

    def test_scrub_organ_cleans_ideas(self):
        out = wiring._run_scrub(
            {
                "ideas": [
                    {"title": "ok", "why": "ключ AKIA1234567890ABCDEF внутри текста"},
                    {"title": "clean", "why": "всё нормально"},
                ]
            },
            {},
        )
        self.assertEqual(out["redacted"], 1)  # ровно одну идею почистили
        self.assertNotIn("AKIA1234567890ABCDEF", str(out["ideas_safe"]))
        self.assertIn("[REDACTED]", str(out["ideas_safe"]))

    def test_scrub_wired_in_chain(self):
        names = [o.name for o in wiring.build_organs()]
        self.assertIn("scrub_secrets", names)
        # deliver теперь потребляет очищенные идеи
        deliver = [o for o in wiring.build_organs() if o.name == "deliver"][0]
        self.assertEqual(deliver.consumes, ["ideas_safe"])

    def test_scrub_google_aq_token(self):
        # ключ Gemini формата AQ.<base64url> — новый паттерн (раньше не ловился). ФЕЙКОВЫЙ токен.
        fake = "AQ.FAKEtoken0123456789ABCDEFGHIJKLMNOPqrstuv"
        s = scrub_secrets.scrub_text("ключ " + fake + " хвост")
        self.assertNotIn(fake, s)
        self.assertIn("[REDACTED]", s)

    def test_scrub_gemini_aiza_key(self):
        # ключ Google/Gemini формата AIzaSy… (БЕЗ разделителя) — прежний паттерн требовал
        # AIza[-_] и НЕ ловил реальные ключи (это класс ключа проекта). ФЕЙКОВЫЙ ключ.
        fake = "AIza" + "SyD3mFf0FakeKey1234567890abcdefghij"  # AIza + ровно 35 символов
        s = scrub_secrets.scrub_text("ключ Gemini: " + fake + " далее текст")
        self.assertNotIn(fake, s)
        self.assertIn("[REDACTED]", s)

    def test_scrub_jwt(self):
        # JWT (три base64url-сегмента) — session/id-токен, прежде проходил насквозь. ФЕЙКОВЫЙ.
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4ifQ."
            "SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVFAKEsig"
        )
        s = scrub_secrets.scrub_text("токен " + jwt + " хвост")
        self.assertNotIn("SflKxwRJSMeKKF2QT4fwpMeJf36POk6yJVFAKEsig", s)
        self.assertIn("[REDACTED]", s)

    def test_scrub_uri_inline_creds(self):
        # инлайн-креды в URI: user:pass редактим, схема и @host остаются
        s = scrub_secrets.scrub_text("db: mongodb://admin:s3cretP4ssword@db.internal:27017/app")
        self.assertNotIn("s3cretP4ssword", s)
        self.assertIn("[REDACTED]", s)
        self.assertIn("@db.internal", s)  # хост не потерян

    def test_scrub_slack_discord_webhooks(self):
        slack = "https://hooks.slack.com/services/T00000000/B00000000/XXXXXXXXXXXXXXXXXXXXXXXX"
        disc = "https://discord.com/api/webhooks/123456789012345678/abcdefghijklmnopqrstuvwxyz0123456789ABCDEF"
        s = scrub_secrets.scrub_text(slack + "\n" + disc)
        self.assertNotIn("XXXXXXXXXXXXXXXXXXXXXXXX", s)
        self.assertNotIn("abcdefghijklmnopqrstuvwxyz0123456789ABCDEF", s)
        self.assertIn("[REDACTED]", s)

    def test_scrub_no_false_positive_port_and_url(self):
        # обычный URL и порт/время НЕ трогаем (нет user:pass@, нет ключей)
        clean = "сервис на http://localhost:8080/path, старт в 12:34, док https://example.com/page"
        self.assertEqual(scrub_secrets.scrub_text(clean), clean)

    def test_log_run_scrubs_secret(self):
        # структурный фикс: _log_run вычищает строку ДО записи, не полагаясь на граф органов
        tmp = tempfile.mkdtemp(prefix="runlog_")
        orig = run.DATA
        run.DATA = tmp
        try:
            out = {
                "goal": "доделай проект",
                "deliverable": "nudge",
                "trace": [{"organ": "finish_step"}],
                "result": {"why": "ротировать sk-ant-api03-DEADBEEF0000000000000000secret"},
            }
            run._log_run(out)
            with open(os.path.join(tmp, "runs.md"), encoding="utf-8") as f:
                body = f.read()
            self.assertNotIn("sk-ant-api03-DEADBEEF", body)
            self.assertIn("[REDACTED]", body)
        finally:
            run.DATA = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
