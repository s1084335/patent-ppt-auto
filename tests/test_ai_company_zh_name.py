"""公司中文名 AI 草稿任務（ai:company_zh_name）契約測試（純單元，不碰 DB）。

規格唯一來源：decisions.md 2026-07-24「公司中文名由 AI 產草稿」＋「落點修正：沿用
company_aliases」。本檔鎖住使用者定案的紅線：

1. **job type 落在 AI_JOB_TYPES**（唯一事實來源），一般 worker 不領、bridge 自動同步。
2. **prompt 防硬翻**：要求市場慣用中文名，且**明確允許並鼓勵回報「查無慣用中文名（保留原文）」**，
   不得音譯／硬造冷門公司中文名。
3. **三態可區分**：AI 草稿的判定分「translated（有中文名）」與「keep_original（查無保留原文）」，
   在草稿態即可區分，且都與「未判斷（根本無草稿列）」不同。
4. **AI 草稿不直接進正式顯示欄**：草稿寫 review_status='ai_suggested'，refresh 的
   code_alias_names 只採 confirmed，故草稿天然不顯示；須經使用者確認才寫正式。

CLI 一律用可注入的 fake runner，不真跑二進位、不燒 token。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_company_zh_name_runner as zh_runner
from backend.app.worker.ai_narrative_runner import CliResult


# ── 測試替身 ───────────────────────────────────────────────────────


class RecordingCli:
    """假 CLI runner：記錄 argv，依 prompt 內的公司代碼回吐中文名草稿。

    verdict_map: {company_code: (verdict, zh_name)}；未列者預設 keep_original。
    """

    def __init__(self, verdict_map=None):
        self.calls: list[list[str]] = []
        self.verdict_map = verdict_map or {}

    def __call__(self, argv, timeout):
        argv = list(argv)
        self.calls.append(argv)
        names = []
        for code in _codes_in_argv(argv):
            verdict, zh = self.verdict_map.get(code, ("keep_original", None))
            item = {"company_code": code, "verdict": verdict}
            if zh is not None:
                item["zh_name"] = zh
            names.append(item)
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps({"names": names}, ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        return [argv[2] for argv in self.calls]


def _codes_in_argv(argv: list[str]) -> list[str]:
    """撈回本批代碼。

    2026-07-28 起 runner 改走**資料檔**（公司名不再進命令列——500 家會撞 Windows
    32,767 上限），故先找 argv 裡的 payload 路徑讀檔；讀不到才退回舊的 prompt 解析
    （build_prompt 保留供離線除錯，仍有測試覆蓋）。
    這樣 fake 的取值方式與真實 CLI 一致（真的 CLI 也是 Read 那個檔）。
    """
    import json as _json
    from pathlib import Path as _Path

    # 路徑嵌在 instruction 的「資料檔（JSON，UTF-8）：<path>」那一行。
    # ⚠ 不能用 \S+ 抓——中文不是空白字元，會把「資料檔（JSON，UTF-8）：」一起吃進去。
    # 改為逐行找 .json 結尾者，取全形冒號後的部分。
    for token in argv:
        for line in str(token).splitlines():
            line = line.strip()
            if not line.endswith(".json"):
                continue
            candidate = line.split("：")[-1].split(": ")[-1].strip()
            if _Path(candidate).exists():
                data = _json.loads(_Path(candidate).read_text(encoding="utf-8"))
                return [c["code"] for c in data.get("companies", [])]
    # 舊路徑（build_prompt）相容
    codes: list[str] = []
    for token in argv:
        for line in str(token).splitlines():
            if line.startswith("### company_code:"):
                codes.append(line.split(":", 1)[1].strip())
    return codes


class FakeZhNameStore:
    """假落點：記錄取清單與草稿寫入，取代真實 DB。"""

    def __init__(self, candidates):
        """candidates 為 [(company_code, 英文公司名), ...]。"""
        self.candidates = list(candidates)
        self.drafts: list[dict] = []
        self.write_calls = 0

    def fetch_pending(self, *, limit=None):
        rows = self.candidates
        return rows if limit is None else rows[:limit]

    def write_drafts(self, drafts):
        """草稿批次寫入；記錄呼叫次數以驗證非 N+1。"""
        self.write_calls += 1
        self.drafts.extend(drafts)
        return len(drafts)


# ── prompt 契約（防硬翻、查無可區分）─────────────────────────────


class PromptContractTests(unittest.TestCase):
    """prompt 必須要求市場慣用中文名、允許查無保留原文、禁止硬翻音譯。"""

    def test_prompt_carries_company_names_and_codes(self):
        prompt = zh_runner.build_prompt([("C001", "Chervon"), ("C002", "Makita Corp.")])
        self.assertIn("### company_code: C001", prompt)
        self.assertIn("Chervon", prompt)
        self.assertIn("### company_code: C002", prompt)
        self.assertIn("Makita Corp.", prompt)

    def test_prompt_asks_for_market_common_name_not_literal(self):
        """要求市場慣用中文名，明令不直翻/不音譯。"""
        prompt = zh_runner.build_prompt([("C001", "Chervon")])
        self.assertIn("慣用", prompt)
        self.assertTrue(
            any(k in prompt for k in ("不直翻", "不要直翻", "非直翻")),
            f"prompt 未禁止直翻：{prompt}",
        )
        self.assertTrue(
            any(k in prompt for k in ("不音譯", "不要音譯", "勿音譯")),
            f"prompt 未禁止音譯：{prompt}",
        )

    def test_prompt_allows_and_encourages_keep_original(self):
        """查無慣用中文名時必須能明確回報保留原文，且鼓勵這樣做（防硬造）。"""
        prompt = zh_runner.build_prompt([("C001", "Obscure Widgets LLC")])
        self.assertIn("保留原文", prompt)
        # verdict 兩態必須寫進契約，讓「查無」與「有中文名」可區分。
        self.assertIn("keep_original", prompt)
        self.assertIn("translated", prompt)

    def test_prompt_is_traditional_chinese_and_json_contract(self):
        prompt = zh_runner.build_prompt([("C001", "Chervon")])
        self.assertIn("繁體中文", prompt)
        self.assertIn("company_code", prompt)
        self.assertIn("verdict", prompt)


# ── 草稿落點（三態、不進正式顯示欄）─────────────────────────────


class DraftWriteTests(unittest.TestCase):
    """AI 產出寫成草稿態，區分 translated / keep_original，不直接覆蓋顯示名。"""

    def test_translated_and_keep_original_drafts_are_distinguishable(self):
        """有中文名（translated）與查無（keep_original）在草稿即可區分。"""
        store = FakeZhNameStore([("C001", "Chervon"), ("C002", "Obscure Widgets LLC")])
        cli = RecordingCli({"C001": ("translated", "泉峰")})
        result = zh_runner.run_company_zh_name(cli_runner=cli, store=store)
        by_code = {d["company_code"]: d for d in store.drafts}
        self.assertEqual(by_code["C001"]["verdict"], "translated")
        self.assertEqual(by_code["C001"]["zh_name"], "泉峰")
        self.assertEqual(by_code["C002"]["verdict"], "keep_original")
        # keep_original 草稿的中文名應落回原文（顯示不硬翻）。
        self.assertEqual(by_code["C002"]["zh_name"], "Obscure Widgets LLC")
        self.assertEqual(result["drafts_written"], 2)

    def test_draft_status_is_ai_suggested_not_confirmed(self):
        """草稿一律 review_status='ai_suggested'——不得寫成 confirmed 混進正式顯示欄。"""
        store = FakeZhNameStore([("C001", "Chervon")])
        cli = RecordingCli({"C001": ("translated", "泉峰")})
        zh_runner.run_company_zh_name(cli_runner=cli, store=store)
        self.assertTrue(store.drafts)
        for draft in store.drafts:
            self.assertEqual(draft["review_status"], "ai_suggested")

    def test_write_is_batched_not_n_plus_1(self):
        """草稿批次寫入，一次送整批，不逐筆 UPDATE。"""
        store = FakeZhNameStore([("C001", "Chervon"), ("C002", "Makita Corp."), ("C003", "Milwaukee")])
        cli = RecordingCli()
        zh_runner.run_company_zh_name(cli_runner=cli, store=store)
        self.assertLessEqual(store.write_calls, 1, "多筆草稿應一次批次寫入")

    def test_unknown_code_from_cli_is_rejected(self):
        """CLI 幻覺代碼不得寫進資料。"""

        def hallucinating_cli(argv, timeout):
            return CliResult(
                exit_code=0,
                stdout=json.dumps({"result": json.dumps(
                    {"names": [{"company_code": "ZZZ", "verdict": "translated", "zh_name": "亂造"}]})}),
                stderr="",
            )

        store = FakeZhNameStore([("C001", "Chervon")])
        with self.assertRaises(zh_runner.CompanyZhNameRunnerError):
            zh_runner.run_company_zh_name(cli_runner=hallucinating_cli, store=store)

    def test_empty_candidates_no_cli_call(self):
        """無待中文化公司時不呼叫 CLI、不空燒 token。"""
        store = FakeZhNameStore([])
        cli = RecordingCli()
        result = zh_runner.run_company_zh_name(cli_runner=cli, store=store)
        self.assertEqual(result["drafts_written"], 0)
        self.assertEqual(len(cli.calls), 0)


# ── job 註冊（AI job、走 Companion、一般 worker 不領）───────────


class JobRegistrationTests(unittest.TestCase):
    """ai:company_zh_name 只由 ai_bridge 領取，一般 worker 領不到。"""

    def test_job_type_registered_as_ai_job(self):
        from backend.app.worker import ai_bridge, runner
        self.assertIn("ai:company_zh_name", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:company_zh_name", job_repository.JOB_TYPES)
        # bridge 由 job_repository 自動推導，故新 type 自動同步、一般 worker 自動不領。
        self.assertIn("ai:company_zh_name", ai_bridge.AI_JOB_TYPES)
        self.assertNotIn("ai:company_zh_name", runner.DEFAULT_WORKER_JOB_TYPES)


# ── 最小權限白名單（沿安全來自任務設計）────────────────────────


class CliCommandTests(unittest.TestCase):
    """CLI 不需讀檔/連網：白名單最小、prompt 自帶全部輸入。"""

    def test_claude_command_has_empty_allowed_tools(self):
        argv = zh_runner.build_cli_command("claude", "prompt-body")
        self.assertIn("--allowedTools", argv)
        idx = argv.index("--allowedTools")
        self.assertEqual(argv[idx + 1], "", "本任務不需任何工具，白名單須為空")


if __name__ == "__main__":
    unittest.main()
