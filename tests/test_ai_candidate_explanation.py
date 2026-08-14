"""候選方案 AI 輔助說明（ai:candidate_explanation）契約測試。

使用者裁決（讀法一，2026-07-24）：分群 calibrate 完成、候選產出後自動觸發此 AI 任務，
AI 只解釋三組候選方案的指標（coherence／diversity／balance／score／k／document_count）
取捨意義，輔助使用者判斷，不替使用者選、不評價專利內容。

本檔鎖住的紅線：
1. runner 底層**直呼**既有 domain 函式 candidate_review_payload / apply_candidate_explanations
   取指標與寫回，不另寫一份。
2. 「為 MCP 預留」：取指標與寫回封成兩個明確、可抽換的呼叫點（可注入），未來改走 MCP
   時只換入口、不動業務邏輯。
3. 🔴 payload 不得含專利內容／keywords／c-TF-IDF refs；prompt 沿用 payload 內既有 instruction。
4. AI 失敗不得擋候選挑選——自動 enqueue 失敗只記 log、不 raise、不影響分群本身。
5. job type 落 AI_JOB_TYPES（走 Companion，一般 worker 不領）；長時任務要有 0→100 進度。

CLI 一律用可注入的 fake runner，不真跑二進位、不燒 token、不產生真 job 進佇列。
"""

from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_candidate_explanation_runner as runner_mod
from backend.app.worker import handlers, runner
from backend.app.worker.cli_gateway import CliResult
from backend.app.clustering import workspace_service
from backend.app.clustering.sources import SOURCE_FIELD_TECHNICAL


# ── 測試替身 ───────────────────────────────────────────────────────


# calibrate 產生的候選指標 payload（無專利內容、無 keywords、無 refs）。
_FAKE_PAYLOAD = {
    "run_id": 42,
    "workspace_id": 7,
    "source_field": "wips_technical",
    "source_label": "技術特徵",
    "document_count": 120,
    "instruction": "請只根據三組候選的 coherence、diversity、balance、score、k 與資料量說明取捨。",
    "candidates": [
        {"candidate_id": 1, "candidate_type": "balanced", "k": 6,
         "coherence": 0.55, "diversity": 0.7, "balance": 0.8, "score": 0.68,
         "parameters": {}, "existing_explanation": None},
        {"candidate_id": 2, "candidate_type": "fine", "k": 9,
         "coherence": 0.6, "diversity": 0.75, "balance": 0.6, "score": 0.65,
         "parameters": {}, "existing_explanation": None},
    ],
}


class RecordingCli:
    """假 CLI runner：記錄 argv，依 payload 內 candidate_id 回吐說明。"""

    def __init__(self, text: str = "此方案主題數適中，coherence 與 balance 取得平衡。"):
        """保存要回吐的說明並準備記錄 argv。"""
        self.calls: list[list[str]] = []
        self.text = text

    def __call__(self, argv, timeout):
        """從 prompt 撈 candidate_id，逐一回吐固定說明。"""
        argv = list(argv)
        self.calls.append(argv)
        prompt = argv[2]
        ids = [c["candidate_id"] for c in _FAKE_PAYLOAD["candidates"]
               if str(c["candidate_id"]) in prompt]
        explanations = [{"candidate_id": cid, "explanation": self.text} for cid in ids]
        return CliResult(
            exit_code=0,
            stdout=json.dumps(
                {"result": json.dumps({"explanations": explanations}, ensure_ascii=False)}),
            stderr="",
        )

    @property
    def prompts(self) -> list[str]:
        """所有批次的 prompt 字串。"""
        return [argv[2] for argv in self.calls]


# ── 底層直呼既有 domain 函式（不另寫）＋ MCP 預留可抽換點 ─────────


class DomainSeamTests(unittest.TestCase):
    """runner 取指標／寫回必須直呼那兩個既有 domain 函式，且封成可抽換點。"""

    def test_default_fetch_and_write_are_the_existing_domain_functions(self):
        """未注入時，取指標／寫回的預設值即 workspace_service 的既有兩函式（非另寫）。"""
        self.assertIs(
            runner_mod.default_fetch_payload,
            workspace_service.candidate_review_payload,
        )
        self.assertIs(
            runner_mod.default_write_explanations,
            workspace_service.apply_candidate_explanations,
        )

    def test_fetch_and_write_seams_are_injectable(self):
        """取指標／寫回是可注入的兩個呼叫點——未來換 MCP 只換這裡。"""
        fetch_calls: list[int] = []
        write_calls: list[dict] = []

        def fake_fetch(run_id):
            fetch_calls.append(run_id)
            return _FAKE_PAYLOAD

        def fake_write(*, run_id, explanations):
            write_calls.append({"run_id": run_id, "explanations": explanations})
            return {"requested_count": len(explanations), "updated_count": len(explanations)}

        cli = RecordingCli()
        result = runner_mod.run_candidate_explanation(
            run_id=42,
            cli_runner=cli,
            fetch_payload=fake_fetch,
            write_explanations=fake_write,
        )
        self.assertEqual(fetch_calls, [42])
        self.assertEqual(len(write_calls), 1)
        self.assertEqual(write_calls[0]["run_id"], 42)
        self.assertEqual(result["explanations_written"], 2)


# ── 紅線：payload 不含專利內容／keywords／refs；prompt 沿用既有 instruction ──


class PromptContractTests(unittest.TestCase):
    """prompt 只帶指標與既有 instruction，絕不含專利內容／keywords／refs。"""

    def test_prompt_uses_existing_instruction_and_no_patent_content(self):
        """prompt 沿用 payload['instruction']，且只帶指標，不夾帶專利內容/keywords/refs。"""
        prompt = runner_mod.build_prompt(_FAKE_PAYLOAD)
        # 沿用既有 instruction（不另寫一份）。
        self.assertIn(_FAKE_PAYLOAD["instruction"], prompt)
        # 指標必須在 prompt 內，AI 才有依據。
        self.assertIn("coherence", prompt)
        self.assertIn("candidate_id", prompt)
        # 絕不出現專利內容／關鍵詞／c-TF-IDF 代表文檔字樣。
        for banned in ("keyword", "keywords", "c-tf-idf", "ctfidf", "代表文檔", "代表性專利", "reference_documents"):
            self.assertNotIn(banned, prompt.lower())

    def test_fetch_payload_strips_reference_docs(self):
        """regression：既有 candidate_review_payload 已 pop refs；runner 不得繞過改自撈。

        runner 不自組指標，只吃 fetch_payload 回傳；此測確認 runner 把整份 payload 的
        candidates 指標交給 prompt，而 refs 由既有 domain 函式在來源就守住。
        """
        prompt = runner_mod.build_prompt(_FAKE_PAYLOAD)
        # parameters 內若含 refs 相關鍵不得外洩（此 fake 已無，僅防未來夾帶）。
        self.assertNotIn("reference", prompt.lower())


class OutputContractTests(unittest.TestCase):
    """CLI 輸出契約 {"explanations":[{candidate_id, explanation}]} 寫回 llm_explanation。"""

    def test_run_writes_explanations_back(self):
        """整條：取指標 → CLI → 寫回；寫回內容為 explanations 陣列。"""
        written: list[dict] = []

        def fake_write(*, run_id, explanations):
            written.append({"run_id": run_id, "explanations": explanations})
            return {"requested_count": len(explanations), "updated_count": len(explanations)}

        cli = RecordingCli()
        runner_mod.run_candidate_explanation(
            run_id=42,
            cli_runner=cli,
            fetch_payload=lambda _rid: _FAKE_PAYLOAD,
            write_explanations=fake_write,
        )
        self.assertEqual(len(written), 1)
        explanations = written[0]["explanations"]
        self.assertEqual({e["candidate_id"] for e in explanations}, {1, 2})
        for e in explanations:
            self.assertTrue(e["explanation"].strip())

    def test_empty_candidates_skips_cli(self):
        """沒有候選就不呼叫 CLI、不寫回（避免空燒 token）。"""
        cli = RecordingCli()
        wrote = []
        result = runner_mod.run_candidate_explanation(
            run_id=42,
            cli_runner=cli,
            fetch_payload=lambda _rid: {**_FAKE_PAYLOAD, "candidates": []},
            write_explanations=lambda **kw: wrote.append(kw),
        )
        self.assertEqual(cli.calls, [])
        self.assertEqual(wrote, [])
        self.assertEqual(result["explanations_written"], 0)


# ── 進度 ───────────────────────────────────────────────────────────


class ProgressTests(unittest.TestCase):
    """AI 任務要有 0→100 進度與階段文字，不可無限 spinner。"""

    def test_progress_advances_with_stage_text(self):
        """回報進度單調遞增、落在 0–100、每段有階段文字。"""
        seen: list[tuple[str, int]] = []
        runner_mod.run_candidate_explanation(
            run_id=42,
            cli_runner=RecordingCli(),
            fetch_payload=lambda _rid: _FAKE_PAYLOAD,
            write_explanations=lambda **kw: {"requested_count": 2, "updated_count": 2},
            progress=lambda stage, percent: seen.append((stage, percent)),
        )
        self.assertTrue(seen, "未回報任何進度")
        percents = [p for _, p in seen]
        self.assertEqual(percents, sorted(percents), "進度必須單調遞增")
        self.assertTrue(all(0 <= p <= 100 for p in percents))
        self.assertTrue(all(stage.strip() for stage, _ in seen), "每段進度須有階段文字")
        self.assertEqual(percents[-1], 100)


# ── job 註冊：走 Companion，一般 worker 不領 ───────────────────────


class JobRegistrationTests(unittest.TestCase):
    """ai:candidate_explanation 只由 ai_bridge 領取，一般 worker 領不到。"""

    def test_job_type_registered_as_ai_job(self):
        """job type 落在 AI_JOB_TYPES（唯一事實來源），一般 worker 不領。"""
        self.assertIn("ai:candidate_explanation", job_repository.AI_JOB_TYPES)
        self.assertIn("ai:candidate_explanation", job_repository.JOB_TYPES)
        self.assertNotIn("ai:candidate_explanation", runner.DEFAULT_WORKER_JOB_TYPES)


# ── 自動觸發：calibrate 完成後 enqueue，失敗不影響分群 ─────────────


def _fake_context():
    """建立不碰 DB 的 JobContext 替身（keepalive 為 no-op contextmanager）。"""
    context = mock.MagicMock()

    @contextmanager
    def _noop_keepalive(*_args, **_kwargs):
        yield

    context.keepalive.side_effect = _noop_keepalive
    context.job.job_id = 500
    return context


class _CalibrateRecorder:
    """記錄 create_job 呼叫並回傳遞增 job_id 的替身。"""

    def __init__(self, *, fail: bool = False):
        """初始化紀錄；fail=True 時 create_job 一律拋例外（驗失敗隔離）。"""
        self.calls: list[dict] = []
        self._next_id = 2000
        self._fail = fail

    def create_job(self, job_type, payload=None, *, workspace_id=None,
                   idempotency_key=None, max_attempts=3):
        """記錄一次 enqueue；fail 模式一律拋錯。"""
        self.calls.append({"job_type": job_type, "payload": payload or {},
                           "workspace_id": workspace_id})
        if self._fail:
            raise RuntimeError("simulated enqueue failure")
        self._next_id += 1
        return mock.MagicMock(job_id=self._next_id)


class AutoTriggerTests(unittest.TestCase):
    """calibrate 完成、候選產出後自動 enqueue 候選 AI 說明任務。"""

    def _summary(self, *, candidates):
        """組一份 calibrate 回傳的 CalibrationSummary-like 物件。"""
        return {"run_id": 42, "candidates": candidates,
                "workspace_id": 7, "source_field": "wips_technical", "status": "ok"}

    def test_calibrate_enqueues_candidate_explanation_when_candidates_exist(self):
        """候選產出後自動 enqueue 一筆 ai:candidate_explanation，帶 run_id。"""
        from backend.app.db import job_repository as jr

        recorder = _CalibrateRecorder()
        payload = {"workspace_id": 7, "source_field": SOURCE_FIELD_TECHNICAL}
        with mock.patch.object(
            handlers, "calibrate_top_level",
            return_value=self._summary(candidates=[{"candidate_id": 1}]),
        ), mock.patch.object(jr, "create_job", recorder.create_job):
            handlers.handle_clustering_calibrate(payload, _fake_context())
        ai_calls = [c for c in recorder.calls if c["job_type"] == "ai:candidate_explanation"]
        self.assertEqual(len(ai_calls), 1, recorder.calls)
        self.assertEqual(ai_calls[0]["payload"].get("run_id"), 42)

    def test_no_candidates_does_not_enqueue(self):
        """沒有候選就不 enqueue（沒東西可解釋）。"""
        from backend.app.db import job_repository as jr

        recorder = _CalibrateRecorder()
        payload = {"workspace_id": 7, "source_field": SOURCE_FIELD_TECHNICAL}
        with mock.patch.object(
            handlers, "calibrate_top_level",
            return_value=self._summary(candidates=[]),
        ), mock.patch.object(jr, "create_job", recorder.create_job):
            handlers.handle_clustering_calibrate(payload, _fake_context())
        ai_calls = [c for c in recorder.calls if c["job_type"] == "ai:candidate_explanation"]
        self.assertEqual(ai_calls, [])

    def test_enqueue_failure_does_not_break_calibrate(self):
        """enqueue 失敗只記 log，不 raise——不得擋分群本身/候選挑選。"""
        from backend.app.db import job_repository as jr

        recorder = _CalibrateRecorder(fail=True)
        payload = {"workspace_id": 7, "source_field": SOURCE_FIELD_TECHNICAL}
        with mock.patch.object(
            handlers, "calibrate_top_level",
            return_value=self._summary(candidates=[{"candidate_id": 1}]),
        ), mock.patch.object(jr, "create_job", recorder.create_job):
            # 不得因 enqueue 失敗而拋例外
            result = handlers.handle_clustering_calibrate(payload, _fake_context())
        # calibrate 本體結果照常回傳
        self.assertEqual(result["run_id"], 42)


if __name__ == "__main__":
    unittest.main()
