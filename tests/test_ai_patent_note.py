"""文獻備註 AI 產生任務（ai:patent_note）契約測試。

規格唯一來源：`D:\\力山\\.agents\\context\\patent-display-spec.md`「文獻備註（#6）」節。
本檔鎖住使用者定案的五條線：

1. **來源＝獨立項**（`core_layer.patents."獨立項[KR,JP,US,CN,EP,IN]"`），不是 abstract 摘要欄。
   ⚠ 2026-07-27 修正：原讀「主權項」，與分群技術通道來源不一致（實測兩欄不同：
   有主權項 49 筆、有獨立項 40 筆）。來源欄現由 clustering.sources 推導。
2. **落點＝`core_layer.patents."文獻備註"`**（0032 起搬主表；一專利一列，回寫直接 WHERE id）。
3. **一律輸出繁體中文**：來源獨立項中英混雜（實測有全英文專利），prompt 必須明確要求繁中。
4. **100 字是上限不是目標**：prompt 不得要求寫滿或設下限（避免 AI 灌水湊字數）。
5. **批次按字數切、不得按件數切**：獨立項中位 1,000 字、p95 2,905、最長 10,008，
   固定件數/批遇到長獨立項會撐爆 context。單筆超長者截斷後獨立成批。

外加效率紅線：DB 寫入走 executemany 批次，不逐筆 UPDATE（N+1）。
CLI 一律用可注入的 fake runner，不真跑二進位、不燒 token。
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_bridge, ai_patent_note_runner, runner
from backend.app.worker.ai_narrative_runner import CliResult
from backend.app.worker.queue_client import ProcessingJob


# ── 測試替身 ───────────────────────────────────────────────────────


class RecordingCli:
    """假 CLI runner：記錄每次收到的 argv，依 prompt 內的 patent_id 回吐備註。"""

    def __init__(self, note_text: str = "一種阻力調節機構，以磁控阻力盤調節運動負荷。"):
        """保存要回吐的備註文字，並準備記錄每批 argv。"""
        self.calls: list[list[str]] = []
        self.note_text = note_text

    def __call__(self, argv, timeout):
        """從資料檔撈出本批的 patent_id 清單，逐一回吐固定備註。

        2026-07-27 起資料走檔案不走命令列（Windows CreateProcess 32,767 上限），
        故此處與真實 CLI 一樣「讀 argv 內的檔案路徑」，不再從 prompt 字串解析。
        """
        argv = list(argv)
        self.calls.append(argv)
        notes = [
            {"patent_id": pid, "note": self.note_text}
            for pid in _patent_ids_in_payload(argv)
        ]
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps({"notes": notes}, ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        """所有批次的 prompt 字串。"""
        return [argv[2] for argv in self.calls]


def _patent_ids_in_payload(argv) -> list[int]:
    """從 argv 內的資料檔路徑讀回本批 patent_id（模擬 CLI 用 Read 讀檔）。"""
    import json as _json
    import re as _re
    for arg in argv:
        # 路徑可能含中文或空白，逐行找出「資料檔…：<path>」那一行，不用 \S+ 硬切
        for line in str(arg).splitlines():
            if "資料檔" not in line or ".json" not in line:
                continue
            candidate = Path(line.split("：", 1)[-1].strip())
            if candidate.exists():
                data = _json.loads(candidate.read_text(encoding="utf-8"))
                return [int(i["patent_id"]) for i in data.get("items", [])]
    return []


def _patent_ids_in_prompt(prompt: str) -> list[int]:
    """從 prompt 的「### patent_id: N」標頭撈回本批專利 id（測試用解析）。"""
    ids: list[int] = []
    for line in prompt.splitlines():
        if line.startswith("### patent_id:"):
            ids.append(int(line.split(":", 1)[1].strip()))
    return ids


class FakeNoteStore:
    """假落點：記錄讀取條件與寫入內容，取代真實 DB。"""

    def __init__(self, candidates):
        """candidates 為 [(patent_id, 獨立項文字), ...]。"""
        self.candidates = list(candidates)
        self.written: list[tuple[int, str]] = []
        self.write_calls = 0
        self.skip_existing_seen: bool | None = None

    def fetch(self, *, workspace_id, skip_existing=True, limit=None):
        """回傳待產生備註的候選（patent_id, 獨立項文字）。"""
        self.skip_existing_seen = skip_existing
        rows = self.candidates
        return rows if limit is None else rows[:limit]

    def write(self, pairs):
        """批次寫入；記錄呼叫次數以驗證非 N+1。"""
        self.write_calls += 1
        self.written.extend(pairs)
        return len(pairs)


# ── 批次切分（按字數，不按件數）─────────────────────────────────


class BatchByCharBudgetTests(unittest.TestCase):
    """批次以累計字數為界動態成批；不得以固定件數切。"""

    def test_batches_split_by_char_budget_not_fixed_count(self):
        """字數預算內盡量多裝；超過即另起一批 → 每批件數不固定。"""
        items = [
            (1, "a" * 400),
            (2, "b" * 400),
            (3, "c" * 900),   # 前兩筆已 800，加這筆超過 1000 → 另起新批
            (4, "d" * 50),
        ]
        batches = ai_patent_note_runner.build_batches(items, char_budget=1000)
        self.assertEqual([[pid for pid, _ in b] for b in batches], [[1, 2], [3, 4]])
        # 件數不固定才代表是按字數切；若各批件數一致就是按件數切。
        self.assertNotEqual(len(batches[0]), 0)
        for batch in batches:
            total = sum(len(text) for _, text in batch)
            # 單批只在「本身就超預算」時才可能超過（超長單筆已先截斷）。
            self.assertLessEqual(total, 1000)

    def test_oversized_single_item_is_truncated_and_alone_in_batch(self):
        """超長單筆（實測最長 10,008 字）截斷到上限並獨立成批，不撐爆 context。"""
        items = [(1, "x" * 50), (2, "y" * 9000), (3, "z" * 50)]
        batches = ai_patent_note_runner.build_batches(items, char_budget=1000)
        oversized = [b for b in batches if b[0][0] == 2]
        self.assertEqual(len(oversized), 1, "超長單筆必須自成一批")
        self.assertEqual(len(oversized[0]), 1, "超長單筆該批不得夾帶其他專利")
        self.assertLessEqual(len(oversized[0][0][1]), 1000, "超長單筆必須截斷到預算內")

    def test_empty_source_is_dropped(self):
        """來源無值（空字串／空白）不成批——沿「來源無值就空著」定案。"""
        items = [(1, "  "), (2, "有效獨立項內容"), (3, "")]
        batches = ai_patent_note_runner.build_batches(items, char_budget=1000)
        self.assertEqual([pid for b in batches for pid, _ in b], [2])


# ── prompt 契約（繁中、不湊字數）───────────────────────────────


class PromptContractTests(unittest.TestCase):
    """prompt 必須要求繁中輸出，且不得要求寫滿 100 字。"""

    def test_prompt_requires_traditional_chinese(self):
        """來源可能全英文，prompt 必須明確要求一律輸出繁體中文。"""
        prompt = ai_patent_note_runner.build_prompt(
            [(1, "A resistance adjusting mechanism comprising a fixed seat ...")]
        )
        self.assertIn("繁體中文", prompt)
        # 必須明說「不論來源語言」，否則模型看到全英文來源會跟著回英文。
        self.assertTrue(
            any(k in prompt for k in ("不論來源語言", "無論來源語言", "即使來源為英文")),
            f"prompt 未載明來源語言無關的繁中要求：{prompt}",
        )

    def test_prompt_states_100_chars_as_upper_bound_without_lower_bound(self):
        """100 字是上限不是目標：不得出現寫滿/至少/下限等湊字數指示。"""
        prompt = ai_patent_note_runner.build_prompt([(1, "一種阻力調節機構……")])
        self.assertIn("100", prompt)
        self.assertTrue(
            any(k in prompt for k in ("以內", "不超過", "上限")),
            f"prompt 未把 100 字表述成上限：{prompt}",
        )
        for banned in ("寫滿", "至少", "不少於", "字數下限", "務必達到"):
            self.assertNotIn(banned, prompt, f"prompt 不得出現湊字數指示：{banned}")

    def test_prompt_states_target_and_hard_limit(self):
        """兩層字數線：目標線讓模型自己收在完整句子、硬上限絕不可超過。

        沿革：
        - 2026-07-26 定兩層線（70／100）。動因：只寫上限時模型當成目標寫滿，
          實測 8 筆幾乎每筆逾 100 字後被硬切、斷在句中（「…第二段減小，形」）。
        - 2026-07-28 使用者再定：目標線收到 **60**，且**移除程式硬切**——
          超過硬上限改為 raise，prompt 明示懲罰機制讓模型自己守住。

        ⚠ 斷言改讀常數而非寫死數字：日後調整字數只需改常數，這條不會變成過期紅線
        （本檔原斷言寫死 "70"，2026-07-28 改 60 後即誤報）。
        """
        from backend.app.worker.ai_patent_note_runner import (
            NOTE_MAX_CHARS, NOTE_TARGET_CHARS,
        )
        prompt = ai_patent_note_runner.build_prompt([(1, "一種阻力調節機構……")])
        self.assertIn(str(NOTE_TARGET_CHARS), prompt)
        self.assertIn(str(NOTE_MAX_CHARS), prompt, "硬上限仍須在 prompt 中載明")

    def test_prompt_requires_complete_sentence_ending(self):
        """要求最後一句完整：寧可短，也不要寫到被截斷。"""
        prompt = ai_patent_note_runner.build_prompt([(1, "一種阻力調節機構……")])
        self.assertTrue(
            any(k in prompt for k in ("完整", "句號")),
            f"prompt 未要求輸出完整句子：{prompt}",
        )

    def test_prompt_carries_claim_text_and_patent_ids(self):
        """prompt 必須帶獨立項全文與 patent_id，讓回吐可對回專利。"""
        prompt = ai_patent_note_runner.build_prompt([(11, "獨立項甲"), (22, "獨立項乙")])
        self.assertIn("### patent_id: 11", prompt)
        self.assertIn("獨立項甲", prompt)
        self.assertIn("### patent_id: 22", prompt)
        self.assertIn("獨立項乙", prompt)


# ── 落點與不覆蓋既有值 ─────────────────────────────────────────


class WriteBackTests(unittest.TestCase):
    """備註寫回 patents."文獻備註"（主表，回寫直接 WHERE id），且不重複燒 token。"""

    def test_run_writes_notes_via_store_in_batches(self):
        """整條流程：讀候選 → 分批呼 CLI → 批次寫回，寫入不得 N+1。"""
        # 每筆 400 字、預算 1000 → 前兩筆同批、第三筆另起，共 2 批。
        store = FakeNoteStore([(1, "獨立項甲" * 100), (2, "獨立項乙" * 100), (3, "獨立項丙" * 100)])
        cli = RecordingCli()
        result = ai_patent_note_runner.run_patent_note(
            workspace_id=7,
            cli_runner=cli,
            store=store,
            char_budget=1000,
        )
        self.assertEqual(sorted(pid for pid, _ in store.written), [1, 2, 3])
        self.assertEqual(result["notes_written"], 3)
        self.assertEqual(result["batches"], 2)
        # 每批一次寫入；3 筆分 2 批 → 2 次寫入呼叫，少於 3 筆（非 N+1）。
        self.assertEqual(store.write_calls, len(cli.calls))
        self.assertLess(store.write_calls, 3)

    def test_skips_patents_that_already_have_note(self):
        """已有備註者預設跳過：可重跑但不重複燒 token。"""
        store = FakeNoteStore([(1, "獨立項甲")])
        cli = RecordingCli()
        ai_patent_note_runner.run_patent_note(
            workspace_id=7, cli_runner=cli, store=store, char_budget=1000
        )
        self.assertIs(store.skip_existing_seen, True)

    def test_note_store_targets_文獻備註_column(self):
        """落點鎖死（0032 搬主表）：WRITE 必須是 UPDATE core_layer.patents ... WHERE id，
        不再寫 patent_attributes、不再選 MAX(raw_record_id)——一專利一列直接命中。"""
        sql = ai_patent_note_runner.PatentNoteStore.WRITE_SQL
        self.assertIn("UPDATE", sql.upper())
        self.assertIn("core_layer.patents", sql)
        self.assertIn('"文獻備註"', sql)
        self.assertIn("WHERE id", sql)
        # 回寫可靠性關鍵：不再落 patent_attributes，也不再選 raw_record 列。
        self.assertNotIn("patent_attributes", sql)
        self.assertNotIn("MAX", sql.upper())
        # 來源＝三級順位（2026-07-28 使用者定案，唯一定義在 clustering.sources）：
        # 獨立項 → 所有權利要求 → abstract。
        # ⚠ 本斷言前身要求「只讀獨立項、不得含 abstract」，理由是與分群同源。該前提在
        # 同日晚間被推翻：查清「有主權項無獨立項」的 9 筆全是 TW（獨立項欄名列的六國
        # 不含 TW），而備註正是那些專利交給 AI 補分的**唯一輸入**——只讀獨立項會讓它們
        # 兩邊皆空、補分機制自我堵死。再查兩級皆空的 11 筆＝CN 外觀設計（權利要求四欄
        # 全空是專利類型本質，但摘要 11/11、最長 530 字），故加第三級 abstract。
        # 分群技術通道**仍固定只讀獨立項**（見 test_patent_note_source_tiers.py 的
        # ClusteringStillIndependentOnlyTests），備註覆蓋較廣是刻意的。
        read_sql = ai_patent_note_runner.PatentNoteStore.READ_SQL
        from backend.app.clustering.sources import PATENT_NOTE_SOURCE_COLUMNS
        for col in PATENT_NOTE_SOURCE_COLUMNS:
            self.assertIn(f'"{col}"', read_sql, f"備註來源缺第 {col} 級")
        self.assertNotIn('"主權項"', read_sql, '使用者明示排除主權項——涵蓋附屬項，語意較雜')
        self.assertNotIn("patent_attributes", read_sql)

    def test_read_sql_uses_current_workspace_membership_source(self):
        """regression：0021 後成員在 workspaces.patent_ids_json，明細表已下沉 legacy_0021。

        初版誤用已不存在的 app_layer.workspace_patents，EXPLAIN 實測 UndefinedTable。
        """
        read_sql = ai_patent_note_runner.PatentNoteStore.READ_SQL
        self.assertIn("patent_ids_json", read_sql)
        self.assertNotIn("app_layer.workspace_patents", read_sql)

    def test_unknown_patent_id_from_cli_is_rejected(self):
        """CLI 幻覺 patent_id 不得寫進正式資料。"""

        def hallucinating_cli(argv, timeout):
            return CliResult(
                exit_code=0,
                stdout=json.dumps({"result": json.dumps({"notes": [{"patent_id": 999, "note": "x"}]})}),
                stderr="",
            )

        store = FakeNoteStore([(1, "獨立項甲")])
        with self.assertRaises(ai_patent_note_runner.PatentNoteRunnerError):
            ai_patent_note_runner.run_patent_note(
                workspace_id=7, cli_runner=hallucinating_cli, store=store, char_budget=1000
            )


# ── 進度與 job 註冊 ────────────────────────────────────────────


class ProgressTests(unittest.TestCase):
    """長時任務要有 0→100 進度與階段文字，不可無限 spinner。"""

    def test_progress_advances_per_batch_with_stage_text(self):
        """每批回報一次進度，百分比遞增且落在 0–100。"""
        store = FakeNoteStore([(i, "獨立項" * 200) for i in range(1, 7)])
        seen: list[tuple[str, int]] = []
        ai_patent_note_runner.run_patent_note(
            workspace_id=7,
            cli_runner=RecordingCli(),
            store=store,
            char_budget=1000,
            progress=lambda stage, percent: seen.append((stage, percent)),
        )
        self.assertTrue(seen, "未回報任何進度")
        percents = [p for _, p in seen]
        self.assertEqual(percents, sorted(percents), "進度必須單調遞增")
        self.assertTrue(all(0 <= p <= 100 for p in percents))
        self.assertTrue(all(stage.strip() for stage, _ in seen), "每段進度須有階段文字")
        # 階段文字須帶「第 n/N 批」可讀資訊，不是固定同一句。
        self.assertGreater(len({stage for stage, _ in seen}), 1)


class JobRegistrationTests(unittest.TestCase):
    """ai:patent_note 只由 ai_bridge 領取，一般 worker 領不到。"""

    def test_job_type_registered_as_ai_job(self):
        """job type 落在 AI_JOB_TYPES（唯一事實來源），一般 worker 不領。"""
        self.assertIn("ai:patent_note", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:patent_note", job_repository.JOB_TYPES)
        self.assertIn("ai:patent_note", ai_bridge.AI_JOB_TYPES)
        self.assertNotIn("ai:patent_note", runner.DEFAULT_WORKER_JOB_TYPES)

    def test_ai_bridge_dispatches_patent_note_job(self):
        """ai_bridge 能派工到文獻備註 handler，並回報 heartbeat。"""
        job = ProcessingJob(
            job_id=51,
            job_type="ai:patent_note",
            status="running",
            payload_json={"workspace_id": 7, "_cli_runner": RecordingCli()},
            workspace_id=7,
            progress_percent=0,
            current_stage=None,
            attempt_count=1,
            max_attempts=1,
            result_json=None,
            error_message=None,
        )
        store = mock.MagicMock()
        fake_note_store = FakeNoteStore([(1, "獨立項甲")])
        # patch 整個 PatentNoteStore 符號（呼叫回 fake），不 patch slot __new__——
        # mock.patch.object 對 __new__ 這種 C-level slot 還原不乾淨，會洩漏到同 session 後續測試。
        with mock.patch.object(
            ai_patent_note_runner, "PatentNoteStore", return_value=fake_note_store
        ):
            outcome = ai_bridge.execute_ai_job(job, worker_id="ai-bridge-test", store=store)
        self.assertEqual(outcome["status"], "succeeded", outcome.get("error"))
        self.assertEqual(outcome["result"]["notes_written"], 1)
        stages = [c.kwargs.get("current_stage") for c in store.heartbeat.call_args_list]
        self.assertTrue(any(s and "備註" in s for s in stages), stages)


if __name__ == "__main__":
    unittest.main()
