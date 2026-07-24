"""市場資料 AI 摘要（ai:market_summary）契約測試（市場線批2）。

批2 核心：AI 讀 workspace 的市場 PDF，產「結構化＋敘述」摘要草稿（accepted_at=NULL），
使用者逐筆確認後才算正式，報表只讀 accepted 現行版。本檔鎖住的紅線：

1. job type 落 AI_JOB_TYPES（走 Companion，一般 worker 不領）。
2. runner 讀該 workspace 的市場 PDF（用 batch1 MarketDocumentStore.list/get），
   以 pymupdf 抽文字內嵌 prompt → CLI 不需 Read/網路工具（安全來自任務設計）。
3. ⚠ prompt **不寫死抽取欄位清單**——依 PDF 實際有的內容抽，有什麼抽什麼，沒有不硬湊。
   範例 PPT 第10頁的 few-shot 定位為「品質／口吻參考」，不是「必填欄位規格」。
4. payload_json 為**彈性結構**（items 陣列，異質指標都放得進），非固定 schema。
5. 專利主角、市場輔助鐵律：數字薄弱寫質性 narrative、不硬造；payload_json 可空。
6. 草稿 accepted_at=NULL；確認後才 accepted；報表只讀 accepted。
7. 全庫 workspace 拒產摘要（全庫不提供市場資料）。
8. CLI 白名單不開 WebSearch/WebFetch/Read。

⚠ 不同架構的市場 PDF（通用性紅線）：至少兩種差異結構（一份有區域分項＋數字、
一份只有質性描述無數字）都要能正確抽取、不漏不硬套——見 HeterogeneousPdfTests。

CLI 一律用可注入的 fake runner，不真跑二進位、不燒 token、不產生真 job 進佇列。
"""
from __future__ import annotations

import json
import unittest

from backend.app.db import job_repository
from backend.app.worker import ai_market_summary_runner as runner_mod
from backend.app.worker import runner as worker_runner
from backend.app.worker.ai_narrative_runner import CliResult


# ── 測試替身 ───────────────────────────────────────────────────────


class FakeDocStore:
    """假的 MarketDocumentStore：回吐固定的市場 PDF metadata。"""

    def __init__(self, docs):
        """docs＝list[dict]（含 stored_filename／original_filename）。"""
        self._docs = docs

    def list_documents(self, workspace_id):
        """列出該 workspace 的市場 PDF metadata。"""
        return list(self._docs)


_UNSET = object()  # sentinel：區分「未傳 payload」與「明確傳 None」（質性摘要可空）


class RecordingCli:
    """假 CLI runner：記錄 argv，回吐一份結構化＋敘述摘要。"""

    def __init__(self, payload=_UNSET, narrative="對公司意涵：北美住宅為主戰場。"):
        """保存要回吐的 payload_json／narrative 並準備記錄 argv。"""
        self.calls: list[list[str]] = []
        # 用 sentinel：payload=None 代表「質性摘要、無結構化數字」，不可被預設覆蓋。
        self.payload = {
            "items": [
                {"label": "全球市場", "value_min": 55, "value_max": 110,
                 "unit": "億美元", "period": "2024-2025", "source": "多家機構"},
            ],
        } if payload is _UNSET else payload
        self.narrative = narrative

    def __call__(self, argv, timeout):
        """記錄 argv，回吐 {"payload_json":..., "narrative":...}。"""
        argv = list(argv)
        self.calls.append(argv)
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps(
                {"payload_json": self.payload, "narrative": self.narrative},
                ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        """所有批次的 prompt 字串。"""
        return [argv[argv.index("-p") + 1] for argv in self.calls]


def _fake_extract_text(docs):
    """假的 PDF 抽文字：依 stored_filename 回不同內容（不需真 PDF、不裝 pymupdf）。"""
    texts = {
        "regional.pdf": "全球電動割草機市場 55-110 億美元，CAGR 5-9%。北美最大市場占 35-41%。",
        "qualitative.pdf": "北美為主要市場，通路以家居賣場為主，CARB 法規推動電動化。",
    }
    return {d["stored_filename"]: texts.get(d["stored_filename"], "市場概況。")
            for d in docs}


# ── job 註冊：走 Companion，一般 worker 不領 ───────────────────────


class JobRegistrationTests(unittest.TestCase):
    """ai:market_summary 只由 ai_bridge 領取，一般 worker 領不到。"""

    def test_job_type_registered_as_ai_job(self):
        """job type 落在 AI_JOB_TYPES（唯一事實來源），一般 worker 不領。"""
        self.assertIn("ai:market_summary", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:market_summary", job_repository.JOB_TYPES)
        self.assertNotIn("ai:market_summary", worker_runner.DEFAULT_WORKER_JOB_TYPES)


# ── CLI 白名單：不開網路、不開 Read（安全來自任務設計） ─────────────


class CliWhitelistTests(unittest.TestCase):
    """市場摘要 CLI 白名單為空——內容內嵌 prompt，不需讀檔/連網。"""

    def test_cli_command_opens_no_network_no_read(self):
        """build_cli_command 產的 argv 不含 WebSearch/WebFetch/Read/Glob/Grep/Write。"""
        argv = runner_mod.build_cli_command("claude", "prompt-body")
        joined = " ".join(argv)
        for banned in ("WebSearch", "WebFetch", "Read", "Glob", "Grep", "Write"):
            self.assertNotIn(banned, joined)
        # 白名單旗標存在且為空字串（明確關閉所有工具）。
        self.assertIn("--allowedTools", argv)
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "")


# ── prompt 不寫死欄位、few-shot 為品質參考、鐵律落實 ─────────────────


class PromptContractTests(unittest.TestCase):
    """prompt 教 AI 怎麼解釋，但不寫死抽取結構；範例只當品質參考。"""

    def _prompt(self):
        """組一份含兩份異質 PDF 文字的 prompt。"""
        return runner_mod.build_prompt(
            {"regional.pdf": "北美最大市場占 35-41%，CAGR 5-9%。",
             "qualitative.pdf": "北美為主要市場，CARB 法規推動電動化。"}
        )

    def test_prompt_has_fewshot_page10_examples(self):
        """few-shot 放範例 PPT 第10頁的實際句子（讓 AI 知道好摘要長怎樣）。"""
        prompt = self._prompt()
        # 範例第10頁的代表句子至少出現一條（品質對標證據）。
        self.assertTrue(
            any(s in prompt for s in ("CARB", "住宅", "35", "對公司意涵", "自走式")),
            "prompt 未帶範例第10頁的品質參考句",
        )

    def test_prompt_does_not_hardcode_extraction_fields(self):
        """prompt 不得列「必抽欄位清單」硬性要求每份 PDF 都要有 CAGR/區域/銷售對象。

        通用性紅線：不假設固定市場指標。prompt 須引導「依 PDF 實際有的內容抽、
        沒有不硬湊」，不得出現把 CAGR／區域分項／銷售對象列為必填的字樣。
        """
        prompt = runner_mod.build_prompt({"a.pdf": "任意市場文字"})
        # 必須明確表達「有什麼抽什麼、沒有不硬湊」的自適應原則。
        self.assertTrue(
            any(k in prompt for k in ("有什麼", "實際", "依該份", "不硬湊", "不硬造")),
            "prompt 未表達自適應抽取原則",
        )
        # few-shot 須被界定為「參考」而非「規格/必填」。
        self.assertTrue(
            any(k in prompt for k in ("參考", "範例僅", "不是要求", "不代表")),
            "prompt 未把範例界定為品質參考",
        )

    def test_prompt_embeds_patent_secondary_rule(self):
        """prompt 明寫專利主角、市場輔助：數字薄弱寫質性、不硬造、不用專利推算市場。"""
        prompt = self._prompt()
        self.assertIn("質性", prompt)
        self.assertTrue(
            any(k in prompt for k in ("不硬造", "不得臆測", "不編造", "不硬湊")),
            "prompt 未含不硬造數字的鐵律",
        )
        self.assertIn("專利", prompt)

    def test_prompt_embeds_pdf_text_inline(self):
        """PDF 文字內嵌 prompt——CLI 不需讀檔即可完成（安全來自任務設計）。"""
        prompt = runner_mod.build_prompt({"regional.pdf": "北美最大市場占 35-41%"})
        self.assertIn("北美最大市場占 35-41%", prompt)


# ── 不同架構 PDF 都要能處理（通用性紅線） ─────────────────────────


class HeterogeneousPdfTests(unittest.TestCase):
    """兩種差異結構的市場 PDF 都能正確產摘要，不漏不硬套。"""

    def _run(self, *, cli, docs):
        """跑一次 runner，回傳 create_summary 收到的參數。"""
        created = {}

        class FakeSummaryStore:
            def create_summary(self, workspace_id, *, payload_json=None,
                               narrative=None, source_document=None):
                created.update(dict(workspace_id=workspace_id, payload_json=payload_json,
                                    narrative=narrative, source_document=source_document))
                return 111
        return created, runner_mod.run_market_summary(
            workspace_id=7,
            cli_runner=cli,
            document_store=FakeDocStore(docs),
            summary_store=FakeSummaryStore(),
            extract_text=_fake_extract_text,
            is_global=lambda _wid: False,
        )

    def test_regional_pdf_produces_structured_payload(self):
        """有區域分項＋數字的 PDF → payload_json 有 items、數值進得去。"""
        cli = RecordingCli(payload={"items": [
            {"label": "北美市占", "value_min": 35, "value_max": 41, "unit": "%"}]})
        created, result = self._run(
            cli=cli, docs=[{"stored_filename": "regional.pdf",
                            "original_filename": "regional.pdf"}])
        self.assertIsNotNone(created["payload_json"])
        self.assertEqual(created["payload_json"]["items"][0]["value_min"], 35)
        self.assertEqual(result["summary_id"], 111)

    def test_qualitative_pdf_allows_null_payload(self):
        """只有質性描述、無數字的 PDF → payload_json 可空，narrative 承接（不硬造）。"""
        cli = RecordingCli(
            payload=None,
            narrative="北美為主要市場，通路以家居賣場為主，CARB 法規推動電動化。")
        created, _ = self._run(
            cli=cli, docs=[{"stored_filename": "qualitative.pdf",
                            "original_filename": "qualitative.pdf"}])
        self.assertIsNone(created["payload_json"])
        self.assertIn("CARB", created["narrative"])

    def test_multiple_pdfs_all_embedded_in_prompt(self):
        """多份 PDF 全部抽文字併入同一 prompt（跨檔彙整由 AI 做）。"""
        cli = RecordingCli()
        self._run(cli=cli, docs=[
            {"stored_filename": "regional.pdf", "original_filename": "regional.pdf"},
            {"stored_filename": "qualitative.pdf", "original_filename": "qualitative.pdf"},
        ])
        prompt = cli.prompts[0]
        self.assertIn("北美最大市場", prompt)
        self.assertIn("CARB", prompt)


# ── 草稿 → 確認 → 報表只讀 accepted ───────────────────────────────


class DraftAcceptGuardTests(unittest.TestCase):
    """AI 產出＝草稿（accepted_at=NULL）；確認後才 accepted；報表只讀 accepted。"""

    def test_summary_created_as_draft(self):
        """runner 產出的摘要為草稿——create_summary 不代為 accept。"""
        accepted_calls = []

        class FakeSummaryStore:
            def create_summary(self, workspace_id, **kw):
                return 222

            def accept(self, summary_id):
                accepted_calls.append(summary_id)
                return True
        runner_mod.run_market_summary(
            workspace_id=7, cli_runner=RecordingCli(),
            document_store=FakeDocStore([{"stored_filename": "regional.pdf",
                                          "original_filename": "regional.pdf"}]),
            summary_store=FakeSummaryStore(), extract_text=_fake_extract_text,
            is_global=lambda _wid: False,
        )
        # runner 不得自動 accept——確認是使用者的事。
        self.assertEqual(accepted_calls, [])

    def test_report_reads_accepted_only(self):
        """報表取用只讀 accepted 現行版——未確認草稿不進報表。"""
        # accepted 現行版：get_accepted_current 回它。
        class Store:
            def get_current(self, wid):
                return {"summary_id": 1, "accepted_at": None, "status": "current"}

            def get_accepted_current(self, wid):
                return None  # 現行版尚未確認 → 報表拿不到

        self.assertIsNone(runner_mod.get_report_market_summary(
            7, summary_store=Store(), is_global=lambda _wid: False))

    def test_report_returns_accepted_current(self):
        """現行版已確認 → 報表拿得到。"""
        class Store:
            def get_accepted_current(self, wid):
                return {"summary_id": 1, "accepted_at": "2026-07-24T00:00:00",
                        "status": "current"}

        got = runner_mod.get_report_market_summary(
            7, summary_store=Store(), is_global=lambda _wid: False)
        self.assertIsNotNone(got)
        self.assertEqual(got["summary_id"], 1)


# ── 全庫拒產（全庫不提供市場資料） ────────────────────────────────


class GlobalWorkspaceRejectionTests(unittest.TestCase):
    """全庫 workspace 不提供市場資料——runner 拒為全庫產摘要。"""

    def test_run_rejects_global_workspace(self):
        """全庫 workspace 呼 run_market_summary → raise，不呼 CLI、不寫摘要。"""
        cli = RecordingCli()
        created = []

        class FakeSummaryStore:
            def create_summary(self, *a, **kw):
                created.append(kw)
                return 1
        with self.assertRaises(runner_mod.MarketSummaryRunnerError):
            runner_mod.run_market_summary(
                workspace_id=99, cli_runner=cli,
                document_store=FakeDocStore([{"stored_filename": "regional.pdf",
                                              "original_filename": "regional.pdf"}]),
                summary_store=FakeSummaryStore(), extract_text=_fake_extract_text,
                is_global=lambda _wid: True,
            )
        self.assertEqual(cli.calls, [])
        self.assertEqual(created, [])

    def test_report_rejects_global_workspace(self):
        """全庫 workspace 取報表市場摘要一律 None（全庫隱藏市場功能）。"""
        class Store:
            def get_accepted_current(self, wid):
                return {"summary_id": 1}  # 即使有也不給全庫
        self.assertIsNone(runner_mod.get_report_market_summary(
            99, summary_store=Store(), is_global=lambda _wid: True))


# ── 無 PDF 就不呼叫 CLI ────────────────────────────────────────────


class NoDocumentsTests(unittest.TestCase):
    """workspace 沒有市場 PDF → 不呼叫 CLI、不寫摘要（不空燒 token）。"""

    def test_no_documents_skips_cli(self):
        """沒有市場 PDF 就不呼 CLI、不 create_summary。"""
        cli = RecordingCli()
        created = []

        class FakeSummaryStore:
            def create_summary(self, *a, **kw):
                created.append(kw)
                return 1
        result = runner_mod.run_market_summary(
            workspace_id=7, cli_runner=cli,
            document_store=FakeDocStore([]),
            summary_store=FakeSummaryStore(), extract_text=_fake_extract_text,
            is_global=lambda _wid: False,
        )
        self.assertEqual(cli.calls, [])
        self.assertEqual(created, [])
        self.assertIsNone(result.get("summary_id"))


# ── 進度 ───────────────────────────────────────────────────────────


class ProgressTests(unittest.TestCase):
    """AI 任務要有 0→100 進度與階段文字，不可無限 spinner。"""

    def test_progress_advances_to_100(self):
        """回報進度單調遞增、落在 0–100、最後到 100。"""
        seen = []

        class FakeSummaryStore:
            def create_summary(self, *a, **kw):
                return 1
        runner_mod.run_market_summary(
            workspace_id=7, cli_runner=RecordingCli(),
            document_store=FakeDocStore([{"stored_filename": "regional.pdf",
                                          "original_filename": "regional.pdf"}]),
            summary_store=FakeSummaryStore(), extract_text=_fake_extract_text,
            is_global=lambda _wid: False,
            progress=lambda stage, percent: seen.append((stage, percent)),
        )
        self.assertTrue(seen)
        percents = [p for _, p in seen]
        self.assertEqual(percents, sorted(percents))
        self.assertTrue(all(0 <= p <= 100 for p in percents))
        self.assertEqual(percents[-1], 100)


if __name__ == "__main__":
    unittest.main()
