"""#3b 報表版本記 topic_run_id／topic_state_version＋不一致時提示（2026-08-05 定案）。

為什麼要有：分群改版後重跑，舊報表的主題資料就過期了，但報表本身看不出來——
使用者拿舊版報表出 PPT，圖上的主題標籤與現行分群不一致而完全無從察覺。
定案走**提示不擋**（擋會讓重新分群後再也無法為舊版報表出 PPT）。

⚠ repository 的 `get_latest_topic_state` 本來就回傳 `run_id`／`state_run_id`，
loader 只是沒往外帶——不必多查一趟 DB。
"""
from __future__ import annotations

import unittest
from unittest import mock


class LoaderCarriesRunIdTests(unittest.TestCase):
    def test_loader_returns_run_ids(self):
        """loader 要把 repository 已經查到的 run_id／state_run_id 帶出來。"""
        from backend.app.reports import cluster_data_loader as L

        state = {
            "workspace_id": 3, "source_field": "effect_summary",
            "run_id": 188, "state_run_id": 186,
            "topics": [{"topic_code": "T001", "label": "甲", "patent_ids": [1]}],
        }
        with mock.patch.object(L, "PostgresTopicStateRepository") as repo:
            repo.return_value.get_latest_topic_state.return_value = state
            conn = mock.MagicMock()
            conn.cursor.return_value.fetchall.return_value = []
            data = L.load_cluster_workspace_data(3, "effect_summary", conn)
        self.assertEqual(data["topic_run_id"], 188)
        self.assertEqual(data["topic_state_version"], 186)


class MergeKeepsPerChannelVersionTests(unittest.TestCase):
    def test_versions_recorded_per_channel(self):
        """雙通道各記各的——兩邊 run_id 不同，混成一個值就分不出哪個過期。"""
        from backend.app.worker import handlers

        parts = {
            "wips_independent_claims": {
                "topics": [{"topic_code": "T001"}], "assignments": [], "topic_rows": [],
                "patents": {}, "normalized_applicants": [], "top_applicants_ws": [],
                "topic_run_id": 187, "topic_state_version": 186,
            },
            "effect_summary": {
                "topics": [{"topic_code": "T001"}], "assignments": [], "topic_rows": [],
                "patents": {}, "normalized_applicants": [], "top_applicants_ws": [],
                "topic_run_id": 189, "topic_state_version": 188,
            },
        }
        original = handlers._load_report_cluster_data
        handlers._load_report_cluster_data = lambda ws, sf: parts.get(sf)
        try:
            merged = handlers._merge_cluster_channels(
                3, ["wips_independent_claims", "effect_summary"])
        finally:
            handlers._load_report_cluster_data = original
        self.assertEqual(merged["topic_run_id"],
                         {"wips_independent_claims": 187, "effect_summary": 189})
        self.assertEqual(merged["topic_state_version"],
                         {"wips_independent_claims": 186, "effect_summary": 188})


class ParametersStampTests(unittest.TestCase):
    def test_parameters_carry_topic_version(self):
        from backend.app.reports.chart_runner import build_report_parameters

        params = build_report_parameters(
            cluster_data={"topic_run_id": {"effect_summary": 189},
                          "topic_state_version": {"effect_summary": 188}})
        self.assertEqual(params["topic_run_id"], {"effect_summary": 189})
        self.assertEqual(params["topic_state_version"], {"effect_summary": 188})

    def test_absent_when_no_cluster_data(self):
        """取不到版本就不落鍵——落 null 會讓下游誤以為「版本是空的」。"""
        from backend.app.reports.chart_runner import build_report_parameters

        params = build_report_parameters(cluster_data=None)
        self.assertNotIn("topic_run_id", params)
        self.assertNotIn("topic_state_version", params)


class StalenessWarningTests(unittest.TestCase):
    """版本不一致＝提示，不擋（2026-08-05 使用者定案）。"""

    def test_warns_when_stale(self):
        from backend.app.worker import ai_report_ppt_runner as R

        warnings = R.topic_version_warnings(
            recorded={"effect_summary": 186}, current={"effect_summary": 190})
        self.assertTrue(warnings)
        self.assertIn("effect_summary", warnings[0])

    def test_silent_when_same(self):
        from backend.app.worker import ai_report_ppt_runner as R

        self.assertEqual(
            R.topic_version_warnings(recorded={"effect_summary": 186},
                                     current={"effect_summary": 186}), [])

    def test_silent_when_unknown(self):
        """舊報表沒記版本時不得亂報——沒有依據就不提示。"""
        from backend.app.worker import ai_report_ppt_runner as R

        self.assertEqual(R.topic_version_warnings(recorded=None, current={"x": 1}), [])
        self.assertEqual(R.topic_version_warnings(recorded={"x": 1}, current=None), [])

    def test_does_not_block(self):
        """提示不擋：函式只回字串清單，不 raise。"""
        from backend.app.worker import ai_report_ppt_runner as R

        R.topic_version_warnings(recorded={"a": 1}, current={"a": 2})  # 不得 raise


class WiringTests(unittest.TestCase):
    """接線：光有純函式不夠，要真的出現在任務結果裡才看得到。"""

    def test_run_report_ppt_returns_warnings_key(self):
        import inspect

        from backend.app.worker import ai_report_ppt_runner as R

        src = inspect.getsource(R.run_report_ppt)
        self.assertIn("topic_version_warnings", src)
        self.assertIn('"topic_version_warnings": _stale', src)

    def test_current_versions_survive_db_failure(self):
        from backend.app.worker import ai_report_ppt_runner as R

        with mock.patch(
                "backend.app.repositories.topic_state_repository.PostgresTopicStateRepository",
                side_effect=RuntimeError("db down")):
            self.assertEqual(R.current_topic_versions(3), {})

    def test_no_workspace_no_query(self):
        from backend.app.worker import ai_report_ppt_runner as R

        self.assertEqual(R.current_topic_versions(None), {})


class CurrentVersionsQueryTests(unittest.TestCase):
    """`current_topic_versions` 的分支：查得到／該通道無主題／run_id 為 None。

    ⚠ 這支會在產 PPT 時真的跑到 DB，三條分支都要驗——只驗 happy path 的話，
    「某通道還沒分群」這種常見情形會在正式環境才第一次執行到。
    """

    def _patch(self, side_effect):
        from backend.app.repositories import topic_state_repository as R

        repo = mock.MagicMock()
        repo.get_latest_topic_state.side_effect = side_effect
        return mock.patch.object(R, "PostgresTopicStateRepository", return_value=repo)

    def test_collects_run_id_per_channel(self):
        from backend.app.worker import ai_report_ppt_runner as R

        def _state(ws, source_field):
            return {"run_id": 100 if "claims" in source_field else 200}

        with self._patch(_state):
            self.assertEqual(
                R.current_topic_versions(3),
                {"wips_independent_claims": 100, "effect_summary": 200})

    def test_channel_without_topics_is_skipped(self):
        from backend.app.repositories.topic_state_repository import (
            TopicStateNotFoundError,
        )
        from backend.app.worker import ai_report_ppt_runner as R

        def _state(ws, source_field):
            if "claims" in source_field:
                raise TopicStateNotFoundError("no topics")
            return {"run_id": 7}

        with self._patch(_state):
            self.assertEqual(R.current_topic_versions(3), {"effect_summary": 7})

    def test_null_run_id_not_recorded(self):
        """run_id 為 None 時不落鍵——落了會被比對成「不一致」而亂報。"""
        from backend.app.worker import ai_report_ppt_runner as R

        with self._patch(lambda ws, sf: {"run_id": None}):
            self.assertEqual(R.current_topic_versions(3), {})


class RecordedVersionReadTests(unittest.TestCase):
    """從 report_data.json 讀出報表當時記的版本（三種檔案狀態）。"""

    def _read(self, tmp):
        """重演 run_report_ppt 裡的讀取邏輯（同一段條件）。"""
        import json
        from pathlib import Path

        path = Path(tmp) / "report_data.json"
        try:
            return (json.loads(path.read_text(encoding="utf-8")).get("parameters") or {}
                    ).get("topic_run_id") if path.exists() else None
        except Exception:  # noqa: BLE001
            return None

    def test_reads_recorded_version(self):
        import json
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "report_data.json").write_text(
                json.dumps({"parameters": {"topic_run_id": {"effect_summary": 9}}}),
                encoding="utf-8")
            self.assertEqual(self._read(tmp), {"effect_summary": 9})

    def test_missing_file_returns_none(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._read(tmp))

    def test_corrupt_json_returns_none(self):
        """壞檔不得讓 PPT 產製失敗——提示性功能不可反過來擋主流程。"""
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "report_data.json").write_text("{壞", encoding="utf-8")
            self.assertIsNone(self._read(tmp))


if __name__ == "__main__":
    unittest.main()
