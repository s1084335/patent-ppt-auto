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

    # 全庫 workspace（2026-07-23 新增）在本類的斷言中不是主角：本類驗的是「批次 workspace
    # 的接線」，故固定用這個假 id 讓全庫路徑可跑，再於斷言時排除全庫的 job。
    GLOBAL_WS_ID = 999

    def _run_import(self, payload_extra, *, patent_ids=(9001, 9002),
                    workspace_result=None, recorder=None, active_jobs=()):
        """跑 handle_patent_import，回 (summary, recorder)；DB 與匯入本體皆 mock。"""
        from backend.app.db import job_repository as jr

        recorder = recorder or _JobRecorder()
        summary = {"status": "imported", "patent_ids": list(patent_ids)}
        payload = {"blob_id": 1, "original_filename": "file.xlsx", "file_hash": "h"}
        payload.update(payload_extra)
        # handler 內是 lazy import 同一個模組物件，patch 其屬性即可攔截（不碰 DB）。
        # 來源檔改由 DB blob 取得（2026-07-23）：取回／刪除都 mock 成 no-op。
        # 全庫同步與「有無既有 artifact」都會連 DB，本類不驗這兩者，一律 mock 掉：
        # _has_completed_clustering_run 回 False 代表首次匯入，維持 calibrate 的原斷言。
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path"), \
             mock.patch.object(handlers.import_blob_store, "delete_blob"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value=workspace_result), \
             mock.patch.object(handlers, "_sync_global_workspace",
                               return_value=self.GLOBAL_WS_ID), \
             mock.patch.object(handlers, "_has_completed_clustering_run", return_value=False), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=list(active_jobs)):
            result = handlers.handle_patent_import(payload, _fake_context())
        return result, recorder

    def _clustering_calls(self, recorder):
        """取出批次 workspace 的分群 enqueue 呼叫（排除全庫 workspace 的那兩個）。"""
        return [c for c in recorder.calls
                if c["job_type"] == "clustering_calibrate"
                and c["workspace_id"] != self.GLOBAL_WS_ID]

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
        # 2026-07-23 起含全庫 workspace 的兩個通道，故總數為 4（批次 2 ＋ 全庫 2）。
        self.assertEqual(len(result.get("clustering_job_ids") or []), 4)

    def test_embeddings_enqueued_before_clustering(self):
        """一般匯入也要補 embeddings，且 embeddings job 必須排在分群之前（單 worker FIFO）。"""
        _, recorder = self._run_import(
            {"new_workspace_name": "ws", "purpose": "general"},
            workspace_result={"workspace_id": 56})
        types = [c["job_type"] for c in recorder.calls]
        self.assertIn("embeddings", types)
        self.assertLess(types.index("embeddings"), types.index("clustering_calibrate"),
                        "embeddings 必須先入列，否則分群會讀到尚未算好的向量")

    def test_global_workspace_also_gets_clustering(self):
        """批次 workspace 之外，全庫 workspace 也要各排一份分群（共 4 個 job）。"""
        _, recorder = self._run_import(
            {"new_workspace_name": "ws", "purpose": "general"},
            workspace_result={"workspace_id": 61})
        clustering = [c for c in recorder.calls if c["job_type"] == "clustering_calibrate"]
        self.assertEqual(len(clustering), 4)
        self.assertEqual({c["workspace_id"] for c in clustering}, {61, self.GLOBAL_WS_ID})

    def test_no_workspace_skips_batch_clustering(self):
        """未圈批次 workspace → 沒有批次分群；全庫仍要分群（全庫涵蓋所有匯入專利）。

        原斷言為「完全不 enqueue 分群」，2026-07-23 起全庫 workspace 一律同步並分群，
        故改為只斷言「沒有批次 workspace 的分群」。
        """
        _, recorder = self._run_import({}, workspace_result=None)
        self.assertEqual(self._clustering_calls(recorder), [])
        clustering = [c for c in recorder.calls if c["job_type"] == "clustering_calibrate"]
        self.assertEqual({c["workspace_id"] for c in clustering}, {self.GLOBAL_WS_ID})

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


class ClusteringTriggerStrategyTests(unittest.TestCase):
    """分群觸發策略（2026-07-23 定案）：首次 calibrate、之後一律 incremental。

    判斷依據為「該 workspace＋通道有無已完成 run（有 run 即有 artifact）」，沿用
    clustering 既有的 _latest_completed_run，不新增判斷機制。全庫 workspace 與一般
    workspace 是兩份獨立 artifact，故各自獨立判斷。
    """

    def _run(self, *, workspace_id, existing_runs=(), global_id=None, active_jobs=()):
        """跑 _enqueue_workspace_clustering 決策，回 recorder 的 enqueue 紀錄。

        existing_runs：已有已完成 artifact 的 (workspace_id, source_field) 集合。
        """
        from backend.app.db import job_repository as jr

        recorder = _JobRecorder()
        completed = {(int(w), f) for w, f in existing_runs}

        def _has_artifact(*, workspace_id, source_field):
            return (int(workspace_id), source_field) in completed

        with mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=list(active_jobs)), \
             mock.patch.object(handlers, "_has_completed_clustering_run",
                               side_effect=_has_artifact):
            handlers._enqueue_workspace_clustering(workspace_id, {})
        return recorder.calls

    def test_first_time_enqueues_calibrate(self):
        """該 workspace 尚無已完成 run → 兩通道都 enqueue clustering_calibrate（建 artifact）。"""
        calls = self._run(workspace_id=70)
        self.assertEqual({c["job_type"] for c in calls}, {"clustering_calibrate"})
        self.assertEqual({c["payload"]["source_field"] for c in calls},
                         {SOURCE_FIELD_TECHNICAL, SOURCE_FIELD_EFFECT})

    def test_second_time_enqueues_incremental(self):
        """已有已完成 run → 改 enqueue clustering_incremental（套既有 artifact，主題結構不變）。"""
        calls = self._run(
            workspace_id=71,
            existing_runs=[(71, SOURCE_FIELD_TECHNICAL), (71, SOURCE_FIELD_EFFECT)])
        self.assertEqual({c["job_type"] for c in calls}, {"clustering_incremental"},
                         "第二次匯入不得重跑完整分群，否則主題結構會整個換掉")
        for call in calls:
            self.assertEqual(call["payload"]["workspace_id"], 71)

    def test_per_channel_independent_decision(self):
        """通道各自判斷：技術已有 artifact 走 incremental，功效尚無則仍 calibrate。"""
        calls = self._run(workspace_id=72, existing_runs=[(72, SOURCE_FIELD_TECHNICAL)])
        by_field = {c["payload"]["source_field"]: c["job_type"] for c in calls}
        self.assertEqual(by_field[SOURCE_FIELD_TECHNICAL], "clustering_incremental")
        self.assertEqual(by_field[SOURCE_FIELD_EFFECT], "clustering_calibrate")

    def test_active_guard_covers_both_job_types(self):
        """已在跑的通道不再堆疊——incremental job 也算佔用該通道。"""
        active = [mock.MagicMock(job_type="clustering_incremental", status="running",
                                 payload_json={"source_field": SOURCE_FIELD_TECHNICAL})]
        calls = self._run(
            workspace_id=73,
            existing_runs=[(73, SOURCE_FIELD_TECHNICAL), (73, SOURCE_FIELD_EFFECT)],
            active_jobs=active)
        fields = {c["payload"]["source_field"] for c in calls}
        self.assertNotIn(SOURCE_FIELD_TECHNICAL, fields, "技術通道已在跑，不應再建一個")
        self.assertIn(SOURCE_FIELD_EFFECT, fields)


class GlobalWorkspaceImportWiringTests(unittest.TestCase):
    """匯入後同步全庫 workspace 並為其獨立 enqueue 分群（最多 4 個 job）。"""

    def _run_import(self, *, workspace_result, global_id=91, existing_runs=()):
        """跑 handle_patent_import，回 (summary, recorder)；全庫同步與 artifact 判斷皆 mock。"""
        from backend.app.db import job_repository as jr

        recorder = _JobRecorder()
        summary = {"status": "imported", "patent_ids": [9001, 9002]}
        payload = {"blob_id": 1, "original_filename": "f.xlsx", "file_hash": "h",
                   "new_workspace_name": "批次"}
        completed = {(int(w), f) for w, f in existing_runs}

        def _has_artifact(*, workspace_id, source_field):
            return (int(workspace_id), source_field) in completed

        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path"), \
             mock.patch.object(handlers.import_blob_store, "delete_blob"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value=workspace_result), \
             mock.patch.object(handlers, "_sync_global_workspace", return_value=global_id), \
             mock.patch.object(handlers, "_has_completed_clustering_run",
                               side_effect=_has_artifact), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=[]):
            result = handlers.handle_patent_import(payload, _fake_context())
        return result, recorder

    def _clustering(self, recorder):
        """取出所有分群 enqueue（calibrate 與 incremental 皆算）。"""
        return [c for c in recorder.calls
                if c["job_type"] in {"clustering_calibrate", "clustering_incremental"}]

    def test_import_enqueues_four_clustering_jobs(self):
        """一次匯入最多 4 個分群 job：該 workspace 技術／功效 ＋ 全庫技術／功效。"""
        _, recorder = self._run_import(workspace_result={"workspace_id": 55}, global_id=91)
        calls = self._clustering(recorder)
        self.assertEqual(len(calls), 4)
        self.assertEqual({c["payload"]["workspace_id"] for c in calls}, {55, 91})

    def test_global_and_workspace_decide_independently(self):
        """全庫已有 artifact、一般 workspace 沒有 → 全庫走 incremental、workspace 走 calibrate。"""
        _, recorder = self._run_import(
            workspace_result={"workspace_id": 56}, global_id=92,
            existing_runs=[(92, SOURCE_FIELD_TECHNICAL), (92, SOURCE_FIELD_EFFECT)])
        by_ws: dict[int, set[str]] = {}
        for call in self._clustering(recorder):
            by_ws.setdefault(call["payload"]["workspace_id"], set()).add(call["job_type"])
        self.assertEqual(by_ws[92], {"clustering_incremental"}, "全庫已有 artifact 應走 incremental")
        self.assertEqual(by_ws[56], {"clustering_calibrate"}, "新 workspace 首次仍需 calibrate")

    def test_global_sync_runs_even_without_batch_workspace(self):
        """未圈一般 workspace 也要同步全庫，並為全庫排分群（全庫涵蓋所有專利）。"""
        _, recorder = self._run_import(workspace_result=None, global_id=93)
        calls = self._clustering(recorder)
        self.assertEqual({c["payload"]["workspace_id"] for c in calls}, {93})

    def test_global_sync_failure_does_not_fail_import(self):
        """全庫同步失敗只記錄不 raise：匯入已成功落庫，不該把 job 標 failed。"""
        from backend.app.db import job_repository as jr

        recorder = _JobRecorder()
        summary = {"status": "imported", "patent_ids": [1]}
        payload = {"blob_id": 1, "original_filename": "f.xlsx", "file_hash": "h",
                   "new_workspace_name": "w"}
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path"), \
             mock.patch.object(handlers.import_blob_store, "delete_blob"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value={"workspace_id": 57}), \
             mock.patch.object(handlers, "_sync_global_workspace",
                               side_effect=RuntimeError("db down")), \
             mock.patch.object(handlers, "_has_completed_clustering_run", return_value=False), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=[]):
            result = handlers.handle_patent_import(payload, _fake_context())
        self.assertEqual(result["status"], "imported")


class ClusteringJobWorkspaceLinkTests(unittest.TestCase):
    """自動 enqueue 的 job 必須帶 workspace_id 欄位，前端才查得到「這個 workspace 在做什麼」。"""

    def test_auto_jobs_carry_workspace_id_column(self):
        """分群 job 的 create_job 帶 workspace_id（workflow_runs.workspace_id 可被 list_jobs 過濾）。"""
        from backend.app.db import job_repository as jr

        recorder = _JobRecorder()
        summary = {"status": "imported", "patent_ids": [1, 2]}
        payload = {"blob_id": 1, "original_filename": "f.xlsx", "file_hash": "h",
                   "new_workspace_name": "w"}
        # 全庫同步與 artifact 判斷都會連 DB，本測只驗 workspace_id 欄有沒有帶上，故 mock 掉。
        with mock.patch.object(handlers.import_blob_store, "write_blob_to_path"), \
             mock.patch.object(handlers.import_blob_store, "delete_blob"), \
             mock.patch.object(handlers, "import_wips_file", return_value=summary), \
             mock.patch.object(handlers, "_attach_import_workspace",
                               return_value={"workspace_id": 91}), \
             mock.patch.object(handlers, "_sync_global_workspace", return_value=999), \
             mock.patch.object(handlers, "_has_completed_clustering_run", return_value=False), \
             mock.patch.object(jr, "create_job", side_effect=recorder.create_job), \
             mock.patch.object(jr, "list_jobs", return_value=[]):
            handlers.handle_patent_import(payload, _fake_context())
        clustering = [c for c in recorder.calls if c["job_type"] == "clustering_calibrate"]
        self.assertTrue(clustering)
        # 每個分群 job 的 workspace_id 欄都要等於其 payload 的 workspace（批次 91／全庫 999）。
        for call in clustering:
            self.assertEqual(call["workspace_id"], call["payload"]["workspace_id"])
            self.assertIn(call["workspace_id"], {91, 999})


if __name__ == "__main__":
    unittest.main()
