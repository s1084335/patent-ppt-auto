"""系統桶移除契約測試（2026-07-27 使用者定案：「其他」「未分類」都完全移除）。

不連 DB、不跑分群——以原始碼與純函式層級驗證три件事：
1. runner 產生的 topic_dicts 不得含 topic_kind 為 unclassified／other 的系統桶。
2. 增量指派的 fallback 不得依賴系統桶；未知 model topic ID 改以 centroid 最近的
   active 主題承接（MiniBatchKMeans 不產生 outlier，未知 ID 只會在合併／停用主題後出現）。
3. 全庫不得殘留「找不到未分類桶就 raise」的死路徑。

為何移除：兩桶自建立以來 doc_count 恆為 0（初始與增量都用 MiniBatchKMeans，
每個點必被指派到最近中心，不存在 HDBSCAN 的 -1 outlier），對使用者是純雜訊。
剔除語意改由 workspace_excluded_patents 的「不相干」桶承接（0036）。
"""
from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER = PROJECT_ROOT / "backend" / "app" / "clustering" / "runner.py"
WORKSPACE_SERVICE = PROJECT_ROOT / "backend" / "app" / "clustering" / "workspace_service.py"


def _strip_comments_and_docstrings(source: str) -> str:
    """移除註解與 docstring，避免說明文字裡的字串被誤判為實作殘留。"""
    tree = ast.parse(source)
    docstrings = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                and isinstance(first.value.value, str):
            docstrings.add((first.lineno, first.end_lineno))
    kept = []
    for index, line in enumerate(source.splitlines(), start=1):
        if any(start <= index <= end for start, end in docstrings):
            continue
        kept.append(re.sub(r"#.*$", "", line))
    return "\n".join(kept)


class NoSystemTopicBucketsTests(unittest.TestCase):
    def _offending_lines(self, path: Path, token: str) -> list[str]:
        """回傳含 token 的「檔名:行號」清單；斷言訊息只印命中行，不印整份原始碼。"""
        code = _strip_comments_and_docstrings(path.read_text(encoding="utf-8"))
        return [
            f"{path.name}:{no}"
            for no, line in enumerate(code.splitlines(), start=1)
            if token in line
        ]

    def test_runner_does_not_emit_system_buckets(self):
        """runner 不再寫入 UNCLASSIFIED／OTHER 兩個系統桶。"""
        for token in ("UNCLASSIFIED", "unclassified"):
            hits = self._offending_lines(RUNNER, token)
            self.assertEqual(
                hits, [],
                f"runner.py 仍殘留系統桶 token {token!r} @ {hits}；兩桶已於 2026-07-27 移除")

    def test_workspace_service_has_no_unclassified_fallback(self):
        """增量指派不再依賴未分類桶，也不再有「找不到就 raise」的死路徑。"""
        hits = self._offending_lines(WORKSPACE_SERVICE, "unclassified")
        self.assertEqual(
            hits, [],
            f"workspace_service.py 仍依賴 unclassified 系統桶 @ {hits}")

    def test_nearest_active_topic_helper_exists(self):
        """未知 model topic ID 改以 centroid 最近的 active 主題承接。"""
        code = WORKSPACE_SERVICE.read_text(encoding="utf-8")
        self.assertTrue(
            "def _nearest_active_topic_code" in code,
            "缺少 centroid 最近 active 主題的 fallback helper")

    def test_nearest_active_topic_picks_closest_centroid(self):
        """helper 純函式行為：回傳距離最近的 active 主題 topic_code。"""
        import sys

        sys.path.insert(0, str(PROJECT_ROOT / "backend"))
        from app.clustering.workspace_service import _nearest_active_topic_code

        centroids = {"T001": [0.0, 0.0], "T002": [10.0, 10.0]}
        self.assertEqual(_nearest_active_topic_code([1.0, 1.0], centroids), "T001")
        self.assertEqual(_nearest_active_topic_code([9.0, 9.5], centroids), "T002")

    def test_nearest_active_topic_returns_none_when_no_topics(self):
        """沒有任何 active 主題時回 None，由呼叫端決定（不塞假 topic_key）。"""
        import sys

        sys.path.insert(0, str(PROJECT_ROOT / "backend"))
        from app.clustering.workspace_service import _nearest_active_topic_code

        self.assertIsNone(_nearest_active_topic_code([1.0, 1.0], {}))


if __name__ == "__main__":
    unittest.main()
