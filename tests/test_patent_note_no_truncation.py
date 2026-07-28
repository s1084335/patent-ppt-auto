"""文獻備註不得截斷（2026-07-28 使用者定案）。

## 使用者原話

「先不要做截斷，第一線是 60 字，第二條線是 100 字，硬上限，AI 要在 100 字內寫到好」

## 改前的問題

`pairs.append((patent_id, note[:NOTE_MAX_CHARS]))` ——超過 100 字**直接切掉**，
斷在句中。使用者實機看到被截斷的備註，判定「這樣很不好」。

2026-07-26 加兩層線（70 目標／100 死線）原本就是為了「讓模型自己收句、避免觸發
硬切」——但硬切仍在，且實務上仍會發生。

## 定案

| 層 | 值 | 作用 |
|---|---|---|
| 目標線 | **60 字**（原 70） | 寫進 prompt，給模型收句餘裕 |
| 硬上限 | **100 字** | prompt 明示絕對不可超過 |
| 程式行為 | **不截斷** | 超過視為模型未遵循指示 |

⚠ 與主題標籤同一口徑：`workspace_service` 的 label／summary 超過硬上限是
**raise 讓呼叫端重生，不靜默截斷**（「超過硬上限視為 LLM 未遵循指示」）。
備註原本走硬切，是兩處規則不一致；本次統一。

## 為何超過要 raise 而不是照寫

照寫等於預設「上限只是建議」，模型會逐漸放飛；且列表欄位過長會撐版面。
raise 讓該批失敗、可重跑，比靜默塞一段超長文字誠實。
"""
from __future__ import annotations

import inspect
import unittest


class NoTruncationTests(unittest.TestCase):
    """程式不得再對 note 做切片。"""

    def test_no_slice_at_runtime(self):
        """執行期驗證：正常長度的備註原樣寫入，一個字都不改。

        ⚠ 不用「搜原始碼有沒有 note[:...]」——那會被說明註解與 docstring 餵飽
        （本測試初版即如此：字串只存在於自己的 docstring 卻判定 red）。
        """
        note = "一種滑雪機阻力調節機構，以磁控飛輪提供無段阻尼並可即時調整負載。"
        self.assertEqual(OverLimitRaisesTests._run_with(note), [(1, note)],
                         "備註被加工了——不得有任何切片")

    def test_thresholds(self):
        """60 目標／100 硬上限。"""
        from backend.app.worker.ai_patent_note_runner import (
            NOTE_MAX_CHARS,
            NOTE_TARGET_CHARS,
        )

        self.assertEqual(NOTE_TARGET_CHARS, 60)
        self.assertEqual(NOTE_MAX_CHARS, 100)

    def test_prompt_states_both_lines(self):
        """兩個數字都要進 prompt，模型才知道目標與死線。"""
        from backend.app.worker.ai_patent_note_runner import build_prompt

        prompt = build_prompt([(1, "some claim text")])
        self.assertIn("60", prompt)
        self.assertIn("100", prompt)

    def test_prompt_has_no_lower_bound(self):
        """⚠ 兩個數字都不是下限——prompt 不得要求寫滿（既有定案，不得回退）。"""
        from backend.app.worker.ai_patent_note_runner import build_prompt

        prompt = build_prompt([(1, "x")])
        for banned in ("至少", "不少於", "寫滿", "務必達到"):
            with self.subTest(banned=banned):
                self.assertNotIn(banned, prompt)


class OverLimitRaisesTests(unittest.TestCase):
    """超過硬上限 → raise，與主題標籤同口徑。"""

    @staticmethod
    def _run_with(note):
        """跑一次完整寫入路徑，回傳實際寫入的 pairs。

        ⚠ 長度檢查在 `run_patent_note` 的批次迴圈，不在 `_extract_notes`
        （後者只解析 JSON）。測試必須打真正有檢查的那層。
        ⚠ 沿用 test_ai_patent_note.FakeNoteStore，不自造第二套 fake。
        """
        import json as _json
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from test_ai_patent_note import FakeNoteStore

        from backend.app.worker import ai_patent_note_runner as r
        from backend.app.worker.ai_narrative_runner import CliResult

        def fake_cli(argv, timeout):
            return CliResult(
                exit_code=0,
                stdout=_json.dumps({"result": _json.dumps(
                    {"notes": [{"patent_id": 1, "note": note}]}, ensure_ascii=False)}),
                stderr="")

        store = FakeNoteStore([(1, "some claim text")])
        r.run_patent_note(cli_runner=fake_cli, store=store)
        return store.written

    def test_over_limit_note_raises(self):
        """101 字的備註必須被擋下，不得靜默寫入。"""
        from backend.app.worker import ai_patent_note_runner as r

        with self.assertRaises(r.PatentNoteRunnerError) as ctx:
            self._run_with("字" * 101)
        self.assertIn("100", str(ctx.exception))

    def test_exactly_at_limit_passes(self):
        """剛好 100 字要放行（上限是「不得超過」，不是「不得達到」）。"""
        note = "字" * 100
        self.assertEqual(self._run_with(note), [(1, note)])


if __name__ == "__main__":
    unittest.main()
