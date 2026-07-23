"""匯入後自動觸發分群的接線契約（2026-07-23 定案「分群改為匯入後自動觸發」）。

使用者定案：資料匯入完成即在背景進分群，使用者不需手動點「執行分群」，結果直接顯示在
分類區（技術／功效兩個 tab）。前端「分群任務」導覽項已移除，故後端必須自己把這條接起來。

本測只驗 handler 的 enqueue 決策（job 型別、payload、順序、去重、失敗隔離），
不真跑分群也不碰 DB：job_repository 以 mock 取代，import_wips_file 與檔案驗證亦 mock。
"""
from __future__ import annotations

import unittest
from contextlib import contextmanager
from unittest import mock

from backend.app.clustering.sources import SOURCE_FIELD_EFFECT, SOURCE_FIELD_TECHNICAL
from backend.app.worker import handlers


def _fake_context():
    """建立不碰 DB 的 JobContext 替身（keepalive 為 no-op contextmanager）。"""
    context = mock.MagicMock()

    @contextmanager
    def _noop_keepalive(*_args, **_kwargs):
        yield

    context.keepalive.side_effect = _noop_keepalive
    return context


class _JobRecorder:
    """記錄 create_job 呼叫並回傳遞增 job_id 的替身。"""

    def __init__(self):
        """初始化呼叫紀錄與 job_id 計數器。"""
        self.calls: list[dict] = []
        self._next_id = 1000

    def create_job(self, job_type, payload=None, *, workspace_id=None,
                   idempotency_key=None, max_attempts=3):
        """記錄一次 enqueue 並回傳帶 job_id 的假 ProcessingJob。"""
        self._next_id += 1
        self.calls.append({
            "job_type": job_type, "payload": payload or {},
            "workspace_id": workspace_id, "idempotency_key": idempotency_key,
        })
        return mock.MagicMock(job_id=self._next_id)


class ImportAutoClusteringTests(unittest.TestCase):
    """匯入成功後自動 enqueue 兩通道分群，並確保 embeddings 先於分群。"""

    def _run_import(self, payload_extra, *, patent_ids=(9001, 9002),
                    workspace_result=None, recorder=None, active_jobs=()):
        """跑 handle_patent_import，回 (summary, recorder)；DB 與匯入本體皆 mock。"""
        from backend.app.db import job_repository as jr

        recorder = recorder or _JobRecorder()
        summary = {"status": "imported", "patent_ids": list(patent_ids)}
        payload = {"path": "imports/uuid/file.xlsx", "file_hash": "h"}
        payload.update(payload_extra)
        # handler 內是 lazy import 同一個模組物件，patch 其屬性即可攔截（不碰 DB）。
        with mock.patch.object(handlers, "is_within_imports_root", return_value=True), \
             mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch.object(handlers, "file_sha256", return_value="h"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value=workspace_result), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=list(active_jobs)):
            result = handlers.handle_patent_import(payload, _fake_context())
        return result, recorder

    def _clustering_calls(self, recorder):
        """取出 recorder 中的分群 enqueue 呼叫。"""
        return [c for c in recorder.calls if c["job_type"] == "clustering_calibrate"]

    def test_enqueues_both_channels_after_import_with_workspace(self):
        """匯入成功且有 workspace → 技術與功效各 enqueue 一個 clustering_calibrate。"""
        result, recorder = self._run_import(
            {"new_workspace_name": "自動分群批", "purpose": "general"},
            workspace_result={"workspace_id": 55})
        calls = self._clustering_calls(recorder)
        self.assertEqual(len(calls), 2, "技術／功效兩通道各要一個分群 job")
        fields = {c["payload"]["source_field"] for c in calls}
        self.assertEqual(fields, {SOURCE_FIELD_TECHNICAL, SOURCE_FIELD_EFFECT})
        for call in calls:
            self.assertEqual(call["payload"]["workspace_id"], 55)
            self.assertEqual(call["workspace_id"], 55)
        # summary 要回報自動分群的 job ids，供前端／log 追蹤。
        self.assertEqual(len(result.get("clustering_job_ids") or []), 2)

    def test_embeddings_enqueued_before_clustering(self):
        """一般匯入也要補 embeddings，且 embeddings job 必須排在分群之前（單 worker FIFO）。"""
        _, recorder = self._run_import(
            {"new_workspace_name": "ws", "purpose": "general"},
            workspace_result={"workspace_id": 56})
        types = [c["job_type"] for c in recorder.calls]
        self.assertIn("embeddings", types)
        self.assertLess(types.index("embeddings"), types.index("clustering_calibrate"),
                        "embeddings 必須先入列，否則分群會讀到尚未算好的向量")

    def test_no_workspace_skips_clustering(self):
        """未指定 workspace（不圈 workspace）→ 不 enqueue 分群（分群以 workspace 為範圍）。"""
        result, recorder = self._run_import({}, workspace_result=None)
        self.assertEqual(self._clustering_calls(recorder), [])
        self.assertIsNone(result.get("clustering_job_ids"))

    def test_no_patent_ids_skips_clustering(self):
        """重複檔／無新專利（patent_ids 空）→ 不 enqueue 分群。"""
        _, recorder = self._run_import(
            {"new_workspace_name": "ws"}, patent_ids=(),
            workspace_result={"workspace_id": 57})
        self.assertEqual(self._clustering_calls(recorder), [])

    def test_does_not_stack_when_clustering_already_active(self):
        """同 workspace 已有 queued/running 的分群 job → 不重複堆疊。"""
        active = [mock.MagicMock(job_type="clustering_calibrate", status="queued",
                                 payload_json={"source_field": SOURCE_FIELD_TECHNICAL})]
        _, recorder = self._run_import(
            {"workspace_id": 58}, workspace_result={"workspace_id": 58},
            active_jobs=active)
        calls = self._clustering_calls(recorder)
        fields = {c["payload"]["source_field"] for c in calls}
        self.assertNotIn(SOURCE_FIELD_TECHNICAL, fields,
                         "技術通道已有在跑的分群 job，不應再建一個")
        self.assertIn(SOURCE_FIELD_EFFECT, fields, "功效通道未在跑，仍應 enqueue")

    def test_enqueue_failure_does_not_fail_import(self):
        """分群 enqueue 失敗只記錄不 raise：匯入本身已成功，job 不該變 failed。"""
        recorder = _JobRecorder()

        def _boom(job_type, payload=None, **kw):
            if job_type == "clustering_calibrate":
                raise RuntimeError("queue down")
            return recorder.create_job(job_type, payload, **kw)

        failing = _JobRecorder()
        failing.create_job = _boom  # type: ignore[method-assign]
        result, _ = self._run_import(
            {"new_workspace_name": "ws"}, workspace_result={"workspace_id": 59},
            recorder=failing)
        # 沒有 raise，且匯入 summary 正常回傳。
        self.assertEqual(result["status"], "imported")

    def test_case_comparison_import_still_enqueues_embeddings_once(self):
        """案件比對匯入不因新接線而重複 enqueue embeddings。"""
        _, recorder = self._run_import(
            {"new_workspace_name": "cc", "purpose": "case_comparison"},
            workspace_result={"workspace_id": 60})
        embeddings_calls = [c for c in recorder.calls if c["job_type"] == "embeddings"]
        self.assertEqual(len(embeddings_calls), 1, "embeddings 只需一個 job（雙通道同批算）")


class _HeartbeatStore:
    """記錄 heartbeat 的 (階段文字, 百分比)，供進度序列斷言；不碰資料庫。"""

    def __init__(self):
        """初始化 heartbeat 紀錄。"""
        self.beats: list[tuple[str | None, int | None]] = []

    def heartbeat(self, *, job_id, worker_id, current_stage=None, progress_percent=None):
        """記錄一次 heartbeat。"""
        self.beats.append((current_stage, progress_percent))

    def is_cancelled(self, *, job_id):
        """測試不觸發取消。"""
        return False


def _real_context(store, payload, job_type):
    """建立走真實 JobContext 的測試 context（heartbeat 進 _HeartbeatStore）。"""
    from backend.app.worker.job_context import JobContext
    from backend.app.worker.queue_client import ProcessingJob

    job = ProcessingJob(
        job_id=77, job_type=job_type, status="running", workspace_id=None,
        payload_json=payload, result_json=None, progress_percent=0,
        current_stage="queued", attempt_count=1, max_attempts=3)
    return JobContext(job=job, worker_id="worker-test", store=store)


def _has_cjk(text: str) -> bool:
    """判斷字串是否含中日韓統一表意文字（階段文字須為繁中可讀）。"""
    return any("一" <= ch <= "鿿" for ch in text or "")


class EmbeddingsProgressTests(unittest.TestCase):
    """embeddings job 的階段序列：繁中可讀、百分比 0→100 遞增（前端才看得出系統在動）。"""

    def _run(self, payload):
        """以假 write_patent_embeddings 跑 handle_embeddings，回 heartbeat 序列。

        db_writer 會拉進 transformers（載入極慢），故用假模組注入 sys.modules 攔截
        handler 內的 lazy import，測試不碰真實模型堆疊。
        """
        import sys

        store = _HeartbeatStore()
        summary = mock.MagicMock(source_rows=10, reused_rows=2, upserted_rows=8,
                                 table_rows_for_source=10)
        fake_db_writer = mock.MagicMock()
        fake_db_writer.write_patent_embeddings.return_value = summary
        with mock.patch.dict(sys.modules,
                             {"backend.app.clustering.db_writer": fake_db_writer}):
            handlers.handle_embeddings(payload, _real_context(store, payload, "embeddings"))
        return store.beats

    def test_stages_are_readable_chinese_and_progress_monotonic(self):
        """兩通道各有繁中階段文字，百分比單調遞增並收在 100。"""
        beats = self._run({"source_fields": [SOURCE_FIELD_TECHNICAL, SOURCE_FIELD_EFFECT]})
        stages = [s for s, _ in beats]
        percents = [p for _, p in beats]
        self.assertEqual(percents, sorted(percents), "進度不得倒退")
        self.assertEqual(percents[-1], 100, "結束要收到 100")
        self.assertTrue(all(_has_cjk(s) for s in stages),
                        f"階段文字須為繁中可讀，實際：{stages}")
        # 兩個通道都要出現各自的中文名稱（技術／功效），使用者才知道在跑哪一段。
        joined = " ".join(stages)
        self.assertIn("技術", joined)
        self.assertIn("功效", joined)


class ClusteringCalibrateProgressTests(unittest.TestCase):
    """clustering_calibrate 的階段文字要讓使用者看懂在幹嘛（繁中可讀）。"""

    def test_calibrate_stages_are_readable_chinese(self):
        """calibrate 起訖階段為繁中文字，且進度收在 100。"""
        store = _HeartbeatStore()
        payload = {"workspace_id": 1, "source_field": SOURCE_FIELD_TECHNICAL}
        with mock.patch.object(handlers, "calibrate_top_level",
                               return_value={"run_id": 1, "candidates": []}):
            handlers.handle_clustering_calibrate(
                payload, _real_context(store, payload, "clustering_calibrate"))
        stages = [s for s, _ in store.beats]
        percents = [p for _, p in store.beats]
        self.assertTrue(all(_has_cjk(s) for s in stages),
                        f"階段文字須為繁中可讀，實際：{stages}")
        self.assertEqual(percents, sorted(percents))
        self.assertEqual(percents[-1], 100)


class ClusteringJobWorkspaceLinkTests(unittest.TestCase):
    """自動 enqueue 的 job 必須帶 workspace_id 欄位，前端才查得到「這個 workspace 在做什麼」。"""

    def test_auto_jobs_carry_workspace_id_column(self):
        """分群 job 的 create_job 帶 workspace_id（workflow_runs.workspace_id 可被 list_jobs 過濾）。"""
        from backend.app.db import job_repository as jr

        recorder = _JobRecorder()
        summary = {"status": "imported", "patent_ids": [1, 2]}
        payload = {"path": "imports/u/f.xlsx", "file_hash": "h", "new_workspace_name": "w"}
        with mock.patch.object(handlers, "is_within_imports_root", return_value=True), \
             mock.patch("pathlib.Path.is_file", return_value=True), \
             mock.patch.object(handlers, "file_sha256", return_value="h"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value={"workspace_id": 91}), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=[]):
            handlers.handle_patent_import(payload, _fake_context())
        clustering = [c for c in recorder.calls if c["job_type"] == "clustering_calibrate"]
        self.assertTrue(clustering)
        for call in clustering:
            self.assertEqual(call["workspace_id"], 91)


if __name__ == "__main__":
    unittest.main()
