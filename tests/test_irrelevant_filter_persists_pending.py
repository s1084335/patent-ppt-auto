"""ai:irrelevant_filter 判讀結果落庫契約（2026-07-27）。

補的斷鏈：runner 原本**只回傳 results、不寫 DB**——判讀結果只存在 job 的
workflow_outputs 裡，前端無從逐筆裁決，AI 跑完等於白跑。

驗證：
- run_irrelevant_filter 完成後呼叫 exclusions.store_ai_verdicts 落庫（status='pending'）。
- 回傳新增 stored 欄位，供 job 結果顯示實際落庫筆數。
- 落庫失敗只記 log 不 raise：judged 結果仍回傳，不讓寫庫問題吃掉整趟 AI 判讀
  （沿既有 enqueue 失敗隔離模式）。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.worker import ai_irrelevant_filter_runner as runner  # noqa: E402


WS = 771


class IrrelevantFilterPersistsPendingTests(unittest.TestCase):
    def test_results_are_stored_as_pending(self):
        """判讀完成後把 results 交給 store_ai_verdicts 落庫。"""
        results = [
            {"patent_id": 1, "verdict": "不相干", "reason": "與主題無關"},
            {"patent_id": 2, "verdict": "相干", "reason": "屬本主題"},
        ]
        fake_store = mock.MagicMock(return_value=1)
        with mock.patch.object(runner, "store_ai_verdicts", fake_store), \
                mock.patch.object(runner.psycopg, "connect", mock.MagicMock()):
            stored = runner._persist_verdicts(WS, results)
        self.assertEqual(stored, 1)
        args, _kwargs = fake_store.call_args
        self.assertEqual(args[0], WS)
        self.assertEqual(list(args[1]), results)

    def test_persist_failure_does_not_raise(self):
        """落庫失敗只記 log 不 raise——AI 判讀結果仍要回得去。"""
        fake_store = mock.MagicMock(side_effect=RuntimeError("db down"))
        with mock.patch.object(runner, "store_ai_verdicts", fake_store), \
                mock.patch.object(runner.psycopg, "connect", mock.MagicMock()):
            stored = runner._persist_verdicts(WS, [{"patent_id": 1, "verdict": "不相干"}])
        self.assertEqual(stored, 0, "落庫失敗回 0，不中斷流程")

    def test_reviewable_verdicts_match_runner_values(self):
        """複核層收的 verdict 值必須真的出現在 runner 的合法值清單裡。

        ⚠ 這條防的是今天反覆出現的「寫入端／讀取端落點不一致」：runner 產出繁體中文
        三分（相干／可疑／不相干），若 exclusions 濾的是英文字串，判讀結果會被靜默
        全數丟棄——不拋錯、不進 log、待複核清單永遠空的。
        """
        from backend.app.clustering.exclusions import REVIEWABLE_VERDICTS

        self.assertTrue(
            REVIEWABLE_VERDICTS.issubset(set(runner.VALID_VERDICTS)),
            f"REVIEWABLE_VERDICTS={sorted(REVIEWABLE_VERDICTS)} 不是 runner "
            f"VALID_VERDICTS={list(runner.VALID_VERDICTS)} 的子集——兩層值域已脫鉤")
        self.assertNotIn(
            "相干", REVIEWABLE_VERDICTS,
            "判定『相干』者本就留在原主題，不應進待複核清單")

    def test_runner_imports_store_ai_verdicts(self):
        """runner 必須接上 exclusions.store_ai_verdicts（不自己寫 SQL）。"""
        source = (
            PROJECT_ROOT / "backend" / "app" / "worker" / "ai_irrelevant_filter_runner.py"
        ).read_text(encoding="utf-8")
        self.assertTrue(
            "store_ai_verdicts" in source,
            "runner 未接上 exclusions.store_ai_verdicts")
        self.assertTrue(
            "INSERT INTO derived_layer.workspace_excluded_patents" not in source,
            "排除表寫入須收口在 clustering.exclusions，runner 不得自己寫 SQL")


if __name__ == "__main__":
    unittest.main()
