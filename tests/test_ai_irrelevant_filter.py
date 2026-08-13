"""不相干專利篩選 AI 判讀 runner（ai:irrelevant_filter）契約測試。

規格唯一來源：irrelevant-patent-filter-spec.md 第 25-121 行（c-TF-IDF 最低 N 筆方案）。

鎖住的紅線：
1. job type 落 AI_JOB_TYPES（走 Companion，一般 worker 不領）。
2. 🔴 prompt **只含文獻備註**——絕不含 c-TF-IDF keywords／相似度分數／主題 label/summary。
3. **各筆獨立判讀**：prompt 明令「逐筆絕對判斷、不得以同批其他專利為基準、不得回傳排序或
   最差 N 筆」；輸出逐筆結果。
4. 輸入＝文獻備註（0032 已搬到 core_layer.patents."文獻備註"）；備註為空標「無法判斷」，
   不得預設相干/不相干。
5. 輸出三分：相干／可疑／不相干，每筆附理由；嚴格度「中」寫進 prompt。
6. 批次 50：超過 50 筆分多批呼叫。
7. 主題 label/summary 待確認——先不給（最保守，避免誘導）。

CLI 一律用可注入 fake runner，不真跑二進位、不燒 token、不產生真 job。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_irrelevant_filter_runner as runner_mod
from backend.app.worker import runner as worker_runner
from backend.app.worker.cli_gateway import CliResult


_PERSIST_PATCHER = mock.patch.object(runner_mod, "_persist_verdicts", return_value=0)


def setUpModule():
    """本檔只驗 runner 契約；落庫行為由專用 persistence 測試負責。"""
    _PERSIST_PATCHER.start()


def tearDownModule():
    _PERSIST_PATCHER.stop()


class RecordingCli:
    """假 CLI runner：記錄 argv，回吐逐筆判讀結果。"""

    def __init__(self, verdicts=None):
        """verdicts＝要回吐的 [{patent_id, verdict, reason}, ...]；None 時依 prompt 內
        patent_id 自動生成全「相干」結果。"""
        self.calls: list[list[str]] = []
        self._verdicts = verdicts

    def __call__(self, argv, timeout):
        """記錄 argv，回吐 {"results":[{patent_id, verdict, reason}, ...]}。"""
        argv = list(argv)
        self.calls.append(argv)
        if self._verdicts is not None:
            results = self._verdicts
        else:
            # 從**資料檔**掃出本批 patent_id，全部回「相干」（測試預設）。
            # 2026-07-27 起備註走檔案不走命令列，故與真實 CLI 一樣讀檔。
            from tests.ai_payload_test_helpers import patent_ids_from_argv

            pids = patent_ids_from_argv(argv)
            results = [{"patent_id": p, "verdict": "相干", "reason": "測試"} for p in pids]
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps(
                {"results": results}, ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        return [argv[argv.index("-p") + 1] for argv in self.calls]


def _fake_notes(candidates):
    """假的文獻備註讀取：candidates＝[(patent_id, note)]，直接回 dict。"""
    return {pid: note for pid, note in candidates}


# ── job 註冊 ───────────────────────────────────────────────────────


class JobRegistrationTests(unittest.TestCase):
    def test_job_type_registered_as_ai_job(self):
        """ai:irrelevant_filter 落 AI_JOB_TYPES，一般 worker 不領。"""
        self.assertIn("ai:irrelevant_filter", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:irrelevant_filter", job_repository.JOB_TYPES)
        self.assertNotIn("ai:irrelevant_filter", worker_runner.DEFAULT_WORKER_JOB_TYPES)


# ── 🔴 prompt 只含文獻備註，不含 keywords/分數/label ──────────────────


class PromptRedlineTests(unittest.TestCase):
    """prompt 只含專利文獻備註（＋可選主題 label 對照），絕不夾帶 c-TF-IDF keywords／分數。

    2026-07-24 定案：主題 label 改為「給 AI 當對照」（第 1 題）；keywords／相似度分數仍為紅線。
    """

    def test_prompt_contains_only_notes_not_scores(self):
        """prompt 帶備註文字，但不出現相似度分數字樣或 keywords（label 已改為允許，不在禁字內）。"""
        prompt = runner_mod.build_prompt(
            [(1, "一種電動割草機的刀盤結構"), (2, "半導體封裝散熱模組")]
        )
        self.assertIn("電動割草機", prompt)
        self.assertIn("半導體封裝", prompt)
        # 不得含相似度分數／keywords 相關字樣（紅線）。label 不再列入禁字（改為對照用）。
        for banned in ("similarity", "cosine", "c-tf-idf", "c-TF-IDF", "keyword",
                       "關鍵詞", "相似度", "分數", "score"):
            self.assertNotIn(banned, prompt)

    def test_prompt_includes_topic_label_when_given(self):
        """給主題 label 時，prompt 帶入該 label 當對照（第 1 題定案）。"""
        prompt = runner_mod.build_prompt([(1, "備註")], topic_label="鋸切結構")
        self.assertIn("鋸切結構", prompt)

    def test_prompt_with_label_still_excludes_scores_and_keywords(self):
        """帶 label 也不得夾帶 keywords／相似度分數（紅線不因 label 放行而鬆動）。"""
        prompt = runner_mod.build_prompt([(1, "一種電動割草機刀盤")], topic_label="割草機刀盤")
        for banned in ("similarity", "cosine", "c-tf-idf", "c-TF-IDF", "keyword",
                       "關鍵詞", "相似度", "分數", "score"):
            self.assertNotIn(banned, prompt)

    def test_prompt_without_label_omits_reference_line(self):
        """未給 label 時 prompt 不硬湊主題對照句（沿既有行為，向後相容）。"""
        prompt = runner_mod.build_prompt([(1, "備註")])
        self.assertNotIn("主題對照", prompt)

    def test_prompt_forbids_relative_ranking(self):
        """prompt 明令逐筆絕對判斷、不得以同批其他專利為基準、不得回排序/最差 N 筆。"""
        prompt = runner_mod.build_prompt([(1, "備註一"), (2, "備註二")])
        self.assertIn("逐筆", prompt)
        # 明確禁止相對化的字樣至少出現一種。
        self.assertTrue(
            any(k in prompt for k in ("絕對判斷", "不得以同批", "不得排序", "不是排名",
                                      "各自獨立", "獨立判斷")),
            "prompt 未明令逐筆獨立/禁止相對化",
        )

    def test_prompt_states_strictness_medium(self):
        """嚴格度『中』的判準寫進 prompt（排除不同產品類別）。"""
        prompt = runner_mod.build_prompt([(1, "備註")])
        self.assertIn("中", prompt)
        # 中級判準的代表描述：排除不同產品類別。
        self.assertTrue(
            any(k in prompt for k in ("不同產品類別", "不同的產品類別", "產品類別")),
            "prompt 未寫入嚴格度中的判準",
        )

    def test_prompt_three_way_output_with_reason(self):
        """prompt 要求三分輸出（相干/可疑/不相干）且每筆附理由。"""
        prompt = runner_mod.build_prompt([(1, "備註")])
        for label in ("相干", "可疑", "不相干"):
            self.assertIn(label, prompt)
        self.assertIn("理由", prompt)


# ── 空備註 → 無法判斷 ──────────────────────────────────────────────


class EmptyNoteTests(unittest.TestCase):
    def test_empty_note_marked_undecidable_without_calling_ai_for_it(self):
        """備註為空者直接標『無法判斷』，不預設相干/不相干。"""
        cli = RecordingCli(verdicts=[{"patent_id": 1, "verdict": "相干", "reason": "有內容"}])
        results = runner_mod.run_irrelevant_filter(
            workspace_id=7,
            candidates=[(1, "有備註的專利"), (2, ""), (3, "   ")],
            cli_runner=cli,
            fetch_notes=None,  # candidates 已含 note
        )["results"]
        by_id = {r["patent_id"]: r for r in results}
        self.assertEqual(by_id[2]["verdict"], "無法判斷")
        self.assertEqual(by_id[3]["verdict"], "無法判斷")
        # 空備註不進 prompt（AI 不判空的）。
        self.assertNotIn("patent_id=2", " ".join(cli.prompts))

    def test_all_empty_notes_skips_cli(self):
        """全部備註為空 → 不呼 CLI，全標無法判斷。"""
        cli = RecordingCli()
        out = runner_mod.run_irrelevant_filter(
            workspace_id=7, candidates=[(1, ""), (2, None)], cli_runner=cli)
        self.assertEqual(cli.calls, [])
        self.assertTrue(all(r["verdict"] == "無法判斷" for r in out["results"]))


# ── 批次 50 ────────────────────────────────────────────────────────


class BatchingTests(unittest.TestCase):
    def test_batches_of_fifty(self):
        """120 筆有備註 → 分 3 批（50/50/20）呼叫 CLI。"""
        candidates = [(i, f"備註{i}") for i in range(1, 121)]
        cli = RecordingCli()
        runner_mod.run_irrelevant_filter(
            workspace_id=7, candidates=candidates, cli_runner=cli, batch_size=50)
        self.assertEqual(len(cli.calls), 3)

    def test_all_patents_get_a_verdict(self):
        """每筆有備註的專利都拿到判讀結果（不漏筆）。"""
        candidates = [(i, f"備註{i}") for i in range(1, 61)]
        cli = RecordingCli()
        out = runner_mod.run_irrelevant_filter(
            workspace_id=7, candidates=candidates, cli_runner=cli, batch_size=50)
        got_ids = {r["patent_id"] for r in out["results"]}
        self.assertEqual(got_ids, {i for i in range(1, 61)})


# ── 三分輸出正規化 ─────────────────────────────────────────────────


class VerdictNormalizationTests(unittest.TestCase):
    def test_only_three_verdict_values_plus_undecidable(self):
        """AI 回傳的 verdict 一律限縮在 相干/可疑/不相干；未知值視為可疑（保守）。"""
        cli = RecordingCli(verdicts=[
            {"patent_id": 1, "verdict": "相干", "reason": "a"},
            {"patent_id": 2, "verdict": "不相干", "reason": "b"},
            {"patent_id": 3, "verdict": "可疑", "reason": "c"},
            {"patent_id": 4, "verdict": "亂寫的值", "reason": "d"},
        ])
        out = runner_mod.run_irrelevant_filter(
            workspace_id=7,
            candidates=[(1, "n1"), (2, "n2"), (3, "n3"), (4, "n4")],
            cli_runner=cli)
        by_id = {r["patent_id"]: r["verdict"] for r in out["results"]}
        self.assertEqual(by_id[1], "相干")
        self.assertEqual(by_id[2], "不相干")
        self.assertEqual(by_id[3], "可疑")
        # 未知值保守歸「可疑」（讓使用者重點檢視，不擅自判相干/不相干）。
        self.assertEqual(by_id[4], "可疑")


# ── CLI 白名單：不讀檔/不連網 ─────────────────────────────────────


class CliWhitelistTests(unittest.TestCase):
    def test_cli_command_opens_no_tools(self):
        """備註內嵌 prompt，CLI 不需 Read/網路——白名單為空。"""
        argv = runner_mod.build_cli_command("claude", "prompt-body")
        joined = " ".join(argv)
        for banned in ("WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write"):
            self.assertNotIn(banned, joined)
        self.assertIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "")


# ── 進度 ───────────────────────────────────────────────────────────


class ProgressTests(unittest.TestCase):
    def test_progress_advances_to_100(self):
        cli = RecordingCli()
        seen = []
        runner_mod.run_irrelevant_filter(
            workspace_id=7,
            candidates=[(1, "備註")],
            cli_runner=cli,
            progress=lambda stage, pct: seen.append((stage, pct)),
        )
        percents = [p for _, p in seen]
        self.assertTrue(percents)
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)


if __name__ == "__main__":
    unittest.main()
