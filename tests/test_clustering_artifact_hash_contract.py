"""finalize 與 incremental 寫入 topic_state 的 artifact hash 落點契約（純字串檢查，不需 DB）。

動因（2026-07-27 實測 KeyError）：
- `_latest_completed_run` 把 topic_state_json **攤平到頂層**後，incremental 讀
  `latest["model_artifact_hash"]`。
- 但 finalize（runner.finalize_top_level）原本把 hash 塞進 `metrics` 子物件，未落頂層
  → 首次 calibrate→finalize 完成後，任何 incremental 都 KeyError: 'model_artifact_hash'。
- incremental 自己（workspace_service）寫的是頂層，兩處不一致才是 bug 本體。

本測鎖住：finalize 寫入端必須把 model_artifact_hash 放進 state 頂層（與 incremental 一致），
避免日後有人改回 metrics-only 而重蹈覆轍。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "backend" / "app" / "clustering" / "runner.py"
WORKSPACE_SERVICE = PROJECT_ROOT / "backend" / "app" / "clustering" / "workspace_service.py"


class ArtifactHashTopLevelContractTests(unittest.TestCase):
    """finalize 與 incremental 都須把 model_artifact_hash 寫在 topic_state 頂層。"""

    def _finalize_merge_patch(self) -> str:
        """取 finalize 落庫尾段內 _merge_topic_state 的 patch 片段（state 頂層寫入處）。

        ⚠ 2026-08-09 掃描目標由 `_persist_final_topics` 改為 `_finish_final_topics`。
        **契約沒有變**——finalize 仍必須把 hash 與 version 寫進 state 頂層；變的是
        這段落庫尾段被抽出成獨立函式，好讓 DP-Means 與 BERTopic 兩條路徑共用
        （replace-clustering-with-dpmeans）。抽出的目的正是**避免第二條路徑漏抄
        這段**，與本測試要守的東西同向。
        """
        source = RUNNER.read_text(encoding="utf-8")
        start = source.index("def _finish_final_topics")
        end = source.index("\ndef ", start + 1)
        body = source[start:end]
        merge_idx = body.find("_merge_topic_state")
        self.assertNotEqual(merge_idx, -1, "finalize 應以 _merge_topic_state 落狀態")
        return body[merge_idx:]

    def test_finalize_merges_hash_at_state_top_level(self):
        """finalize 的 _merge_topic_state patch 必須含頂層 model_artifact_hash。

        讀取端 `_latest_completed_run` 以 `{**state, **latest}` 攤平後直接取
        `latest["model_artifact_hash"]`；只寫在 metrics 子物件內會 KeyError。
        """
        self.assertIn(
            '"model_artifact_hash"', self._finalize_merge_patch(),
            "model_artifact_hash 必須在 _merge_topic_state 的 patch（state 頂層），"
            "不能只塞在 persist_final_topics 的 metrics 子物件內",
        )

    def test_finalize_merges_artifact_version_at_top_level(self):
        """finalize 也必須寫頂層 artifact_version。

        incremental 讀 `int(latest["artifact_version"])` 推下一版；
        `_latest_completed_run` 的 SQL 也以該值排序（缺值會落 NULLS LAST）。
        """
        self.assertIn(
            '"artifact_version"', self._finalize_merge_patch(),
            "finalize 需在 state 頂層寫 artifact_version，供 incremental 遞增與排序取用",
        )

    def test_incremental_also_writes_top_level_hash(self):
        """incremental 端維持頂層寫法（回歸鎖，避免被改成 metrics-only）。"""
        source = WORKSPACE_SERVICE.read_text(encoding="utf-8")
        self.assertRegex(
            source,
            r'"status":\s*"completed",\s*\n\s*"model_artifact_hash":',
            "incremental 完成時應在 state 頂層寫 model_artifact_hash",
        )

    def test_reader_expects_top_level_key(self):
        """讀取端契約：_latest_completed_run 攤平後以頂層 key 取用（本測的前提）。"""
        source = WORKSPACE_SERVICE.read_text(encoding="utf-8")
        self.assertIn('latest["model_artifact_hash"]', source)


if __name__ == "__main__":
    unittest.main()
