"""ai:topic_backfill runner（CLU-014）——建議產出契約。

- 輸入＝候選文獻備註文本＋現有主題清單；prompt 必須限定「只能從現有主題選」。
- 輸出契約：[{patent_id, suggested_topic_key, reason}]；清單外主題標 invalid
  現形（不靜默丟棄、不自創主題）；未知 patent_id fail loud。
- 建議隨 job result 落 workflow_outputs（complete_job 自動存；2026-08-07 現實
  回寫：analysis_outputs 是 legacy_0021 空表非現行落點），不碰 topic_assignments。
- 無候選＝不呼叫 CLI、不落任何 output（誠實回 0）。
"""
from __future__ import annotations

import json
import unittest
from typing import Any

from backend.app.worker import ai_topic_backfill_runner as runner


CANDIDATES = [
    {"patent_id": 101, "patent_number": "TW-M641704", "title": "Fitness mechanism",
     "input_text": "磁控飛輪與渦電流阻力，單向軸承帶動渦卷彈簧回收拉繩"},
    {"patent_id": 102, "patent_number": "TW-M637313", "title": "Ski machine",
     "input_text": "動滑輪與定滑輪組構成阻力傳遞，變速機構調節"},
]
TOPICS = [
    {"topic_key": "T01", "label": "阻力產生與調節", "summary": "磁阻/渦電流"},
    {"topic_key": "T02", "label": "傳動機構", "summary": "滑輪/軸承"},
]


def _fake_cli(reply_rows):
    calls: list[dict[str, Any]] = []

    def cli(prompt: str, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return json.dumps({"suggestions": reply_rows}, ensure_ascii=False)

    cli.calls = calls
    return cli


def _run(reply_rows, candidates=None, persister=None):
    saved: list[dict[str, Any]] = []

    def _persist(payload):
        saved.append(payload)
        return {"analysis_id": 77, "output_id": 88}

    cli = _fake_cli(reply_rows)
    result = runner.run_topic_backfill(
        workspace_id=3,
        source_field="wips_independent_claims",
        candidate_fetcher=lambda: candidates if candidates is not None else list(CANDIDATES),
        topics_fetcher=lambda: list(TOPICS),
        cli_runner=cli,
        persister=persister or _persist,
    )
    return result, cli, saved


class PromptContractTests(unittest.TestCase):
    def test_prompt_contains_inputs_and_topic_menu(self):
        _, cli, _ = _run([{"patent_id": 101, "suggested_topic_key": "T01", "reason": "r"},
                          {"patent_id": 102, "suggested_topic_key": "T02", "reason": "r"}])
        prompt = cli.calls[0]["prompt"]
        self.assertIn("磁控飛輪", prompt)
        self.assertIn("T01", prompt)
        self.assertIn("阻力產生與調節", prompt)
        self.assertIn("只能", prompt, "prompt 必須限定只能從現有主題選")

    def test_no_candidates_skips_cli_and_persist(self):
        result, cli, saved = _run([], candidates=[])
        self.assertEqual(result["candidates"], 0)
        self.assertEqual(cli.calls, [])
        self.assertEqual(saved, [])


class OutputContractTests(unittest.TestCase):
    def test_valid_suggestions_persisted_as_narrative_output(self):
        result, _, saved = _run([
            {"patent_id": 101, "suggested_topic_key": "T01", "reason": "講阻力"},
            {"patent_id": 102, "suggested_topic_key": "T02", "reason": "講傳動"},
        ])
        self.assertEqual(result["suggested"], 2)
        self.assertEqual(result["invalid"], 0)
        payload = saved[0]
        self.assertEqual(payload["prompt_version"], runner.PROMPT_VERSION)
        self.assertEqual(payload["source_field"], "wips_independent_claims")
        keys = [s["suggested_topic_key"] for s in payload["suggestions"]]
        self.assertEqual(keys, ["T01", "T02"])

    def test_unknown_topic_key_marked_invalid_not_dropped(self):
        result, _, saved = _run([
            {"patent_id": 101, "suggested_topic_key": "T99", "reason": "亂編"},
            {"patent_id": 102, "suggested_topic_key": "T02", "reason": "ok"},
        ])
        self.assertEqual(result["invalid"], 1)
        rows = saved[0]["suggestions"]
        bad = next(r for r in rows if r["patent_id"] == 101)
        self.assertFalse(bad["valid"])
        self.assertIn("T99", bad["invalid_reason"])

    def test_unknown_patent_id_fails_loud(self):
        with self.assertRaises(runner.TopicBackfillError):
            _run([{"patent_id": 999, "suggested_topic_key": "T01", "reason": "?"}])

    def test_missing_patent_gets_placeholder_not_silent(self):
        """CLI 少回一件＝該件以 valid=False 現形，不得整批當成功。"""
        result, _, saved = _run([{"patent_id": 101, "suggested_topic_key": "T01", "reason": "r"}])
        rows = saved[0]["suggestions"]
        missing = next(r for r in rows if r["patent_id"] == 102)
        self.assertFalse(missing["valid"])
        self.assertEqual(result["invalid"], 1)


if __name__ == "__main__":
    unittest.main()
