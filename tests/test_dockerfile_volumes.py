"""Dockerfile 必須宣告持久化路徑（2026-07-27 實機 9l）。

實機症狀：`docker run` 沒帶 `-v` → 重建容器後
`FileNotFoundError: clustering artifact not found: /app/data/model_artifacts/...`
→ **所有 workspace 的增量分群都失敗**，且 PatentSBERTa（837MB）每次冷啟都重下載。

使用者原話：「寫進 dockerfile，不然指令少了我就死了。」
——靠人記得在指令帶 `-v` 不可靠；`VOLUME` 宣告讓未帶 `-v` 時至少自動建
anonymous volume，不會一重啟就全失。

⚠ VOLUME 不能取代 named volume：anonymous volume 每次 `docker run` 都是新的。
正式部署仍須明確指定，且 backend 與 worker 掛同一組（不同容器、檔案系統不共享）。
本測試只鎖「宣告存在」這條底線。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = PROJECT_ROOT / "Dockerfile"

# 需要跨容器重建保留的路徑：
#   /app/data   分群 artifact ＋ 模型權重（沒了 → 增量分群 FileNotFound）
#   /app/output 報表產物（沒了 → ai:narrative 的 resolve_run_dir 讀不到）
REQUIRED_VOLUMES = ("/app/data", "/app/output")


class DockerfileVolumeTests(unittest.TestCase):
    def _volume_declarations(self) -> set[str]:
        """取出 Dockerfile 內所有 VOLUME 宣告的路徑（支援 JSON 陣列與空白分隔兩種寫法）。"""
        paths: set[str] = set()
        for line in DOCKERFILE.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("VOLUME"):
                continue
            body = stripped[len("VOLUME"):].strip()
            paths.update(re.findall(r'"([^"]+)"', body) or body.split())
        return paths

    def test_declares_required_volumes(self):
        """/app/data 與 /app/output 都必須宣告 VOLUME。"""
        declared = self._volume_declarations()
        for path in REQUIRED_VOLUMES:
            with self.subTest(path=path):
                self.assertIn(
                    path, declared,
                    f"Dockerfile 未宣告 VOLUME {path}——"
                    "重建容器會遺失該路徑內容（實機 9l：增量分群全掛）")

    def test_required_dirs_are_created(self):
        """對應目錄要先 mkdir，VOLUME 才掛得上正確擁有者的目錄。"""
        content = DOCKERFILE.read_text(encoding="utf-8")
        for path in REQUIRED_VOLUMES:
            with self.subTest(path=path):
                self.assertIn(
                    path, content.split("VOLUME")[0],
                    f"{path} 未在 VOLUME 宣告前建立（mkdir/chown）")

    def test_run_command_example_in_comments(self):
        """Dockerfile 註解要留完整的 docker run 範例（含 -v），使用者不必翻文件。

        使用者明示「指令少了我就死了」——範例寫在這裡，改 Dockerfile 時一起看到。
        """
        content = DOCKERFILE.read_text(encoding="utf-8")
        for path in REQUIRED_VOLUMES:
            with self.subTest(path=path):
                self.assertIn(
                    f"-v patent-{path.split('/')[-1]}:{path}", content,
                    f"Dockerfile 註解缺少 {path} 的 named volume 範例")


if __name__ == "__main__":
    unittest.main()
