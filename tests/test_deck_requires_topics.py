"""沒有分群主題就不產簡報（tasks §9.2c／§9.2d）。

## 為什麼

使用者 2026-08-19：「沒有分群不會去進入做簡報。」
⚠ 但實查：程式裡**沒有這道守門**。`_compose` 是
`if content.get("conclusions"): slide_conclusions else: slide_rec`，
而 `_check_conclusions` 只在 `topic_facts` 非空時才要求 conclusions
——沒有任何一處拒絕「無主題就產 deck」。那是**流程上不會**，不是**程式擋著**。

這個差別決定 §9 的 rec 退場安不安全：退場後若真有人在無分群時跑 deck，
第 2 頁會**靜默消失**——使用者拿到的簡報少一頁而沒有任何訊息，
是缺席型偏差裡最貴的那種。

## 判準是「訊息說得出原因」不是「有 raise」

⚠ 只 raise 的話使用者看到的是一個技術例外（KeyError／IndexError），
仍然不知道要去做分群。錯誤訊息本身就是這道檢查的產出。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.worker import ai_report_deck_runner as deck


class RequireTopicsTests(unittest.TestCase):
    def _work(self, facts: list | None) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        work = Path(tmp.name)
        if facts is not None:
            (work / "topic_facts.json").write_text(
                json.dumps(facts, ensure_ascii=False), encoding="utf-8")
        return work

    def test_empty_topics_is_rejected(self):
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            deck.require_topic_facts(self._work([]))
        self.assertIn("分群", str(ctx.exception),
                      "訊息沒提到分群——使用者不知道該去做什麼")

    def test_missing_file_is_rejected(self):
        """⚠ 檔案不存在與內容為空是**同一件事**：都代表沒有主題。

        分開處理的話，其中一條會被漏掉，而漏掉的那條就是靜默通過的入口。
        """
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            deck.require_topic_facts(self._work(None))
        self.assertIn("分群", str(ctx.exception))

    def test_message_says_what_to_do(self):
        """判準：讀得出**原因**與**下一步**，不是一個技術例外。"""
        with self.assertRaises(deck.DeckRunnerError) as ctx:
            deck.require_topic_facts(self._work([]))
        msg = str(ctx.exception)
        self.assertIn("無法產簡報", msg)
        self.assertTrue(
            any(k in msg for k in ("請先", "先完成")),
            f"訊息沒說下一步該做什麼：{msg}")

    def test_with_topics_passes(self):
        """⚠ 反面要驗：有主題時不得誤擋，否則正常流程整條斷掉。"""
        facts = [{"topic": "拉繩滑雪", "finding": "10件/9家"}]
        self.assertEqual(deck.require_topic_facts(self._work(facts)), 1)


if __name__ == "__main__":
    unittest.main()
