"""AI 主題標籤／摘要任務（ai:topic_label）契約測試。

目的：讓正式 topic version 的主題名從 c-TF-IDF 關鍵詞拼接（如 "unit / said / second"）
變成人看得懂的中文主題名。本檔鎖住四條紅線：

1. **keywords 絕對不得傳給 CLI**（使用者定案）：c-TF-IDF 只是「挑哪 5 筆代表專利」的方法，
   關鍵詞內容本身不得進 payload／prompt。給了關鍵字，LLM 會覆述關鍵詞而非閱讀專利內容命名。
2. 代表專利取既有 `representative_patent_ids`（引擎已用 c-TF-IDF cosine 排好），不重算排序。
3. job 分工：`ai:topic_label` 只有 ai_bridge 領得到，一般 worker 領不到。
4. label guard：`label_source='manual'` 的人工命名不得被 AI 覆蓋。

CLI 一律用可注入的 fake runner，不真跑二進位。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from backend.app.db import job_repository
from backend.app.worker import ai_bridge, ai_topic_label_runner, runner
from backend.app.worker.ai_narrative_runner import CliResult
from backend.app.worker.queue_client import ProcessingJob


# ── 測試替身 ───────────────────────────────────────────────────────

# 引擎已產出的 labeling payload 形狀（來自 clustering.workspace_service.topic_labeling_payload）。
# 注意：這份形狀刻意沒有 keywords，本檔的紅線測試即斷言它一路不被加回去。
_ENGINE_PAYLOAD = {
    "workspace_id": 7,
    "source_field": "wips_independent_claims",
    "source_label": "技術",
    "run_id": 41,
    "instruction": "請只根據每個 topic 的前 5 筆代表性專利文件產生 topic_code、label、summary；不要依賴 keywords。",
    "topics": [
        {
            "topic_code": "T001",
            "current_label_source": "fallback",
            "representative_patents": [
                "一種阻力調節機構，包含固定座與可動件……",
                "健身器材之磁控阻力裝置，其特徵在於……",
                "飛輪組件與制動帶之連動結構……",
                "阻力盤與感測單元之配置……",
                "可調式配重塊之導引結構……",
            ],
        },
        {
            "topic_code": "T002",
            "current_label_source": "manual",
            "representative_patents": ["跑步機緩衝底板結構……", "避震模組之彈性件配置……"],
        },
    ],
}


def _fake_payload_builder(*, workspace_id: int, source_field: str, topic_keys=None):
    """取代 clustering.workspace_service.topic_labeling_payload，避免測試連 DB。"""
    return json.loads(json.dumps(_ENGINE_PAYLOAD))


class RecordingCli:
    """假 CLI runner：記錄收到的 argv，回傳固定 JSON 結果。"""

    def __init__(self, labels: list[dict] | None = None):
        """保存要回吐的標籤結果，並準備記錄 argv。"""
        self.argv: list[str] = []
        self.labels = labels if labels is not None else [
            {"topic_code": "T001", "label": "阻力調節機構", "summary": "以磁control阻力盤調節運動負荷。"},
            {"topic_code": "T002", "label": "緩衝避震結構", "summary": "以彈性件吸收踏面衝擊。"},
        ]

    def __call__(self, argv, timeout):
        """記錄 argv 後回傳 headless CLI 風格的 JSON stdout。"""
        self.argv = list(argv)
        return CliResult(
            exit_code=0,
            stdout=json.dumps({"result": json.dumps({"topics": self.labels}, ensure_ascii=False)}),
            stderr="",
        )


def _topic_label_job(payload: dict | None = None) -> ProcessingJob:
    """建立一筆 ai:topic_label 測試 job。"""
    return ProcessingJob(
        job_id=101,
        job_type="ai:topic_label",
        status="queued",
        workspace_id=7,
        payload_json=payload if payload is not None else {
            "workspace_id": 7,
            "source_field": "wips_independent_claims",
        },
        result_json=None,
        progress_percent=0,
        current_stage="queued",
        attempt_count=0,
        max_attempts=1,
    )


# ── 1. job type 註冊與分工 ────────────────────────────────────────


class TopicLabelJobTypeTests(unittest.TestCase):
    """ai:topic_label 必須是合法 job type，且只由 AI bridge 消費。"""

    def test_job_type_registered(self):
        """create_job 白名單要認得 ai:topic_label，否則 API 建不出任務。"""
        self.assertIn("ai:topic_label", job_repository.JOB_TYPES)

    def test_job_type_is_ai_job(self):
        """AI bridge 的 claim 過濾要含 ai:topic_label。"""
        self.assertIn("ai:topic_label", ai_bridge.AI_JOB_TYPES)

    def test_default_worker_does_not_claim_topic_label(self):
        """一般 worker 沒有 CLI，不得領走 AI 標籤任務（維持既有分工）。"""
        self.assertNotIn("ai:topic_label", runner.DEFAULT_WORKER_JOB_TYPES)
        self.assertNotIn("ai:narrative", runner.DEFAULT_WORKER_JOB_TYPES)
        self.assertIn("report_generate", runner.DEFAULT_WORKER_JOB_TYPES)

    def test_ai_job_types_single_source(self):
        """AI job 集合只有一份事實來源，避免 runner 與 bridge 各自漂移。"""
        self.assertEqual(set(runner.AI_JOB_TYPES), set(ai_bridge.AI_JOB_TYPES))


# ── 2. payload 組裝（紅線：不得含 keywords）───────────────────────


class TopicLabelPayloadTests(unittest.TestCase):
    """payload 必須帶代表專利文檔與必要 metadata，且完全不含 keywords。"""

    def _build(self):
        return ai_topic_label_runner.build_topic_label_payload(
            workspace_id=7,
            source_field="wips_independent_claims",
            payload_builder=_fake_payload_builder,
        )

    def test_payload_contains_representative_documents(self):
        """每個 topic 要帶其 c-TF-IDF 代表專利的文檔內容（正向）。"""
        payload = self._build()
        topics = {t["topic_code"]: t for t in payload["topics"]}
        self.assertEqual(len(topics["T001"]["representative_patents"]), 5)
        self.assertIn("阻力調節機構", topics["T001"]["representative_patents"][0])

    def test_payload_respects_representative_doc_limit(self):
        """代表專利上限沿用引擎常數，不自行放大。"""
        self.assertEqual(ai_topic_label_runner.TOPIC_LABELING_DOC_LIMIT, 5)
        for topic in self._build()["topics"]:
            self.assertLessEqual(
                len(topic["representative_patents"]),
                ai_topic_label_runner.TOPIC_LABELING_DOC_LIMIT,
            )

    def test_payload_contains_required_metadata(self):
        """workspace／source_field／topic version 識別必須在 payload 內。"""
        payload = self._build()
        self.assertEqual(payload["workspace_id"], 7)
        self.assertEqual(payload["source_field"], "wips_independent_claims")
        self.assertEqual(payload["run_id"], 41)
        self.assertTrue(payload["instruction"])

    def test_payload_has_no_keywords_field(self):
        """🔴 紅線（反向）：payload 任一層都不得出現 keywords 相關欄位。"""
        payload = self._build()
        forbidden_keys = {"keywords", "keywords_json", "topic_keywords", "terms"}

        def walk(node, path="$"):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(
                        str(key).lower(), forbidden_keys,
                        msg=f"payload {path} 出現 keywords 欄位：{key}",
                    )
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, item in enumerate(node):
                    walk(item, f"{path}[{i}]")

        walk(payload)

    def test_payload_does_not_leak_keyword_labels(self):
        """🔴 紅線（反向）：關鍵詞拼接的舊 label 不得出現在 payload 文字內。"""
        payload = self._build()
        blob = json.dumps(payload, ensure_ascii=False)
        # 引擎 fallback label 形如 "unit / said / second"；不得被帶進 CLI 看得到的地方。
        self.assertNotIn("unit / said / second", blob)
        self.assertNotIn("keywords_json", blob)
        for topic in payload["topics"]:
            self.assertNotIn("label", topic)

    def test_payload_builder_called_once_for_all_topics(self):
        """效率：一次批次取所有 topic 的代表文檔，不每個 topic 查一次。"""
        calls: list[dict] = []

        def counting_builder(**kwargs):
            calls.append(kwargs)
            return json.loads(json.dumps(_ENGINE_PAYLOAD))

        ai_topic_label_runner.build_topic_label_payload(
            workspace_id=7,
            source_field="wips_independent_claims",
            payload_builder=counting_builder,
        )
        self.assertEqual(len(calls), 1)


# ── 3. prompt 與 CLI 呼叫 ─────────────────────────────────────────


class TopicLabelPromptTests(unittest.TestCase):
    """prompt 是 CLI 唯一看得到的字串，同樣受紅線約束。"""

    def test_prompt_embeds_representative_documents(self):
        """prompt 要帶代表專利文檔，CLI 才能讀內容命名。"""
        payload = ai_topic_label_runner.build_topic_label_payload(
            workspace_id=7, source_field="wips_independent_claims",
            payload_builder=_fake_payload_builder,
        )
        prompt = ai_topic_label_runner.build_prompt(payload)
        self.assertIn("阻力調節機構", prompt)
        self.assertIn("T001", prompt)

    def test_prompt_has_no_keywords(self):
        """🔴 紅線：prompt 不得含 keywords 或關鍵詞拼接字串。"""
        payload = ai_topic_label_runner.build_topic_label_payload(
            workspace_id=7, source_field="wips_independent_claims",
            payload_builder=_fake_payload_builder,
        )
        prompt = ai_topic_label_runner.build_prompt(payload)
        self.assertNotIn("keywords_json", prompt)
        self.assertNotIn("unit / said / second", prompt)

        # 連「keywords」字樣都不得出現：即使是「不要依賴 keywords」這種禁令，
        # 也等於把關鍵詞概念送進 prompt，會把模型往覆述高頻詞的方向帶。
        self.assertNotIn("keywords", prompt.lower())

    def test_cli_command_uses_payload_cli_kind_and_model(self):
        """CLI 呼叫沿用 ai_narrative_runner 的組裝：cli_kind／model 由 payload 帶。"""
        argv = ai_topic_label_runner.build_cli_command("claude", "PROMPT", model="claude-opus-4-8")
        self.assertEqual(argv[0], "claude")
        self.assertIn("--model", argv)
        self.assertIn("claude-opus-4-8", argv)
        self.assertIn("PROMPT", argv)

    def test_cli_command_supports_opencode(self):
        """雙 CLI 可換：opencode 走同一組裝表，不寫死 claude。"""
        argv = ai_topic_label_runner.build_cli_command("opencode", "PROMPT")
        self.assertEqual(argv[0], "opencode")


class TopicLabelRunTests(unittest.TestCase):
    """run_topic_label 端到端（fake CLI + fake apply），不碰 DB、不跑二進位。"""

    def test_run_drives_cli_and_applies_labels(self):
        """CLI 產出的 label/summary 要經 apply 回填，並回報覆蓋數。"""
        cli = RecordingCli()
        applied: list[dict] = []

        def fake_apply(*, workspace_id, source_field, labels, updated_by):
            applied.append({"workspace_id": workspace_id, "source_field": source_field,
                            "labels": labels, "updated_by": updated_by})
            # 模擬引擎 guard：T002 為 manual，被跳過
            return {"updated_count": 1}

        result = ai_topic_label_runner.run_topic_label(
            workspace_id=7,
            source_field="wips_independent_claims",
            cli_kind="claude",
            model="claude-opus-4-8",
            cli_runner=cli,
            payload_builder=_fake_payload_builder,
            apply_labels=fake_apply,
        )

        self.assertEqual(result["topics_requested"], 2)
        self.assertEqual(result["topics_updated"], 1)
        self.assertEqual(result["cli_kind"], "claude")
        self.assertEqual(result["run_id"], 41)
        self.assertEqual(applied[0]["labels"][0]["topic_code"], "T001")
        # AI 產出一律標 llm（草稿），不得自我升級為 manual
        self.assertEqual(applied[0]["labels"][0]["source"], "llm")

    def test_run_never_sends_keywords_to_cli(self):
        """🔴 紅線：實際送進 CLI 的 argv 不得含任何 keywords 內容。"""
        cli = RecordingCli()
        ai_topic_label_runner.run_topic_label(
            workspace_id=7, source_field="wips_independent_claims",
            cli_runner=cli, payload_builder=_fake_payload_builder,
            apply_labels=lambda **kw: {"updated_count": 2},
        )
        blob = " ".join(cli.argv)
        self.assertNotIn("keywords", blob)
        self.assertNotIn("unit / said / second", blob)

    def test_run_rejects_labels_for_unknown_topic(self):
        """CLI 亂造 topic_code 時要擋掉，不把幻覺標籤寫進正式 state。"""
        cli = RecordingCli(labels=[{"topic_code": "T999", "label": "亂造", "summary": ""}])
        with self.assertRaises(ai_topic_label_runner.TopicLabelRunnerError):
            ai_topic_label_runner.run_topic_label(
                workspace_id=7, source_field="wips_independent_claims",
                cli_runner=cli, payload_builder=_fake_payload_builder,
                apply_labels=lambda **kw: {"updated_count": 0},
            )

    def test_run_rejects_empty_cli_output(self):
        """CLI 沒產任何標籤時直接失敗，不靜默回報成功。"""
        cli = RecordingCli(labels=[])
        with self.assertRaises(ai_topic_label_runner.TopicLabelRunnerError):
            ai_topic_label_runner.run_topic_label(
                workspace_id=7, source_field="wips_independent_claims",
                cli_runner=cli, payload_builder=_fake_payload_builder,
                apply_labels=lambda **kw: {"updated_count": 0},
            )

    def test_run_forces_llm_source_even_if_cli_claims_manual(self):
        """🔴 label guard：CLI 自稱 manual 也要被改回 llm，人工定案權不外流。"""
        cli = RecordingCli(labels=[
            {"topic_code": "T001", "label": "阻力調節機構", "summary": "x", "source": "manual"},
        ])
        applied: list[dict] = []

        def fake_apply(*, workspace_id, source_field, labels, updated_by):
            applied.append({"labels": labels})
            return {"updated_count": 1}

        ai_topic_label_runner.run_topic_label(
            workspace_id=7, source_field="wips_independent_claims",
            cli_runner=cli, payload_builder=_fake_payload_builder,
            apply_labels=fake_apply,
        )
        self.assertEqual(applied[0]["labels"][0]["source"], "llm")


# ── 4. AI bridge 消費 ─────────────────────────────────────────────


class AiBridgeTopicLabelTests(unittest.TestCase):
    """bridge 領到 ai:topic_label 後要派給對應 runner，並回寫結果。"""

    def test_execute_ai_job_handles_topic_label(self):
        """bridge 支援第二種 AI job，不再只認 ai:narrative。"""
        from tests.test_ai_bridge import FakeAiQueue

        job = _topic_label_job()
        store = FakeAiQueue(job)
        with mock.patch.object(
            ai_bridge, "_run_ai_topic_label_job", return_value={"topics_updated": 3}
        ) as patched:
            result = ai_bridge.execute_ai_job(job, worker_id="ai-bridge-test", store=store)

        patched.assert_called_once()
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(store.completed[0], {"topics_updated": 3})

    def test_run_once_claims_both_ai_job_types(self):
        """bridge 的 claim 過濾要同時涵蓋兩種 AI job。"""
        from tests.test_ai_bridge import FakeAiQueue

        store = FakeAiQueue(None)
        ai_bridge.run_once(worker_id="ai-bridge-test", stale_after_seconds=60, store=store)
        self.assertIn("ai:topic_label", store.claimed_job_types)
        self.assertIn("ai:narrative", store.claimed_job_types)


if __name__ == "__main__":
    unittest.main()
