"""AI 任務資料檔共用核心（2026-07-27）。

動因：`ai:topic_label` 在本機 Companion 執行時 FileNotFoundError [WinError 206]。
實測 prompt 達 128,101 字元，而 Windows `CreateProcess` 的命令列上限是 32,767
（Linux 約 2MB，故容器內不會發生；但 AI 任務依架構定案只由本機 Companion 領取，
必定在 Windows 上跑，必定超標）。縮小批次只是治標——主題數與獨立項長度都是變數，
下一批照樣爆，且錯誤訊息「檔名或副檔名太長」與真因完全對不上。

定案（使用者 2026-07-27）：
- 資料寫成檔案、CLI 以 Read 讀取（權限從「零工具」放寬到**只有 Read**，
  仍不得寫檔/執行指令/上網）。
- 檔案集中在 `var/ai_payloads/<任務類型>/`，**保留 7 天**後自動清理（策略 A）。
- 三支共用同一核心（topic_label／patent_note／irrelevant_filter），不再各自散落。
"""
from __future__ import annotations

import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.worker import ai_payload_file as pf


class PayloadFileWriteTests(unittest.TestCase):
    """寫檔行為：集中目錄、依任務分類、檔名唯一可追溯。"""

    def test_writes_under_task_subdirectory(self):
        with TemporaryDirectory() as tmp:
            p = pf.write_payload_file(
                "topic_label", {"topics": []}, root=Path(tmp), run_id=93,
                label="ws1_wips",
            )
            self.assertTrue(p.exists())
            self.assertEqual(p.parent.name, "topic_label", "須依任務類型分子目錄")
            self.assertEqual(p.suffix, ".json")

    def test_filename_carries_run_id_for_traceability(self):
        """檔名帶 run_id：出問題時能直接對到那筆 job 看它讀了什麼。"""
        with TemporaryDirectory() as tmp:
            p = pf.write_payload_file(
                "patent_note", {"items": []}, root=Path(tmp), run_id=94, label="batch01"
            )
            self.assertIn("run94", p.name)
            self.assertIn("batch01", p.name)

    def test_concurrent_writes_do_not_collide(self):
        """同一任務同時多批不得互相覆蓋（檔名需唯一）。"""
        with TemporaryDirectory() as tmp:
            paths = {
                pf.write_payload_file("irrelevant_filter", {"i": i},
                                      root=Path(tmp), run_id=95, label=f"b{i}")
                for i in range(5)
            }
            self.assertEqual(len(paths), 5)

    def test_content_is_readable_json(self):
        """寫出的是 UTF-8 JSON，CLI 用 Read 讀得懂；中文不轉義。"""
        with TemporaryDirectory() as tmp:
            data = {"topics": [{"topic_code": "T001", "docs": ["手持電動工具的刀片組件"]}]}
            p = pf.write_payload_file("topic_label", data, root=Path(tmp), run_id=1)
            loaded = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(loaded, data)
            self.assertIn("手持電動工具", p.read_text(encoding="utf-8"))


class PayloadRetentionTests(unittest.TestCase):
    """保留 7 天（策略 A）：能回頭查，又不會無限累積。"""

    def test_removes_files_older_than_retention(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = root / "topic_label" / "old.json"
            old.parent.mkdir(parents=True)
            old.write_text("{}", encoding="utf-8")
            eight_days = time.time() - 8 * 86400
            import os
            os.utime(old, (eight_days, eight_days))

            fresh = pf.write_payload_file("topic_label", {}, root=root, run_id=2)
            pf.cleanup_old_payloads(root=root)

            self.assertFalse(old.exists(), "超過保留期的檔案應被清掉")
            self.assertTrue(fresh.exists(), "保留期內的檔案不得誤刪")

    def test_retention_days_is_seven(self):
        self.assertEqual(pf.RETENTION_DAYS, 7)


class PortabilityTests(unittest.TestCase):
    """可攜性：不得寫死任何機器專屬路徑；Installer 裝在不可寫目錄時要能改落點。"""

    def test_no_hardcoded_machine_paths(self):
        """原始碼不得出現磁碟代號、使用者家目錄等字面路徑。"""
        import inspect
        import re

        src = inspect.getsource(pf)
        hits = re.findall(
            r'["\'][A-Za-z]:[\\/][^"\']*["\']|["\']/(?:home|Users)/[^"\']*["\']', src
        )
        self.assertFalse(hits, f"發現寫死的機器路徑：{hits}")

    def test_default_derives_from_module_location(self):
        """預設落點由 __file__ 推導——安裝到哪解到哪，不假設開發機位置。"""
        self.assertTrue(str(pf.DEFAULT_PAYLOAD_ROOT).endswith(
            str(Path("var") / "ai_payloads")))

    def test_follows_companion_state_dir(self):
        """跟隨 Companion 既有的 AI_BRIDGE_STATE_DIR，Installer 只需設一個變數。

        情境：Installer 裝在 Program Files（一般使用者不可寫），Companion 已用該變數
        把狀態指到 %LOCALAPPDATA%；資料檔必須跟著走，否則寫不進去。
        """
        import os

        with TemporaryDirectory() as tmp:
            os.environ["AI_BRIDGE_STATE_DIR"] = tmp
            os.environ.pop("AI_PAYLOAD_DIR", None)
            try:
                self.assertEqual(pf.payload_root(), Path(tmp).resolve() / "ai_payloads")
            finally:
                os.environ.pop("AI_BRIDGE_STATE_DIR", None)

    def test_explicit_override_wins(self):
        """AI_PAYLOAD_DIR 優先於 state dir；參數又優先於環境變數。"""
        import os

        with TemporaryDirectory() as a, TemporaryDirectory() as b:
            os.environ["AI_BRIDGE_STATE_DIR"] = a
            os.environ["AI_PAYLOAD_DIR"] = b
            try:
                self.assertEqual(pf.payload_root(), Path(b).resolve())
                self.assertEqual(pf.payload_root(Path(a)), Path(a))
            finally:
                os.environ.pop("AI_BRIDGE_STATE_DIR", None)
                os.environ.pop("AI_PAYLOAD_DIR", None)


class BatchSplitTests(unittest.TestCase):
    """分批：資料落檔解決命令列上限，分批解決 AI context window。"""

    def test_small_input_stays_single_batch(self):
        """現行資料量（不超過預算）不得被切開，維持單次呼叫。"""
        items = [{"topic_code": f"T{i}", "docs": ["x" * 100]} for i in range(5)]
        self.assertEqual(len(pf.split_into_batches(items, max_chars=100_000)), 1)

    def test_splits_when_over_budget(self):
        """超過預算即切批；每批都不超過預算（單項過大者除外）。"""
        items = [{"i": i, "doc": "x" * 30_000} for i in range(10)]
        batches = pf.split_into_batches(items, max_chars=100_000)
        self.assertGreater(len(batches), 1)
        import json as _j
        for b in batches:
            if len(b) > 1:
                self.assertLessEqual(
                    sum(len(_j.dumps(i, ensure_ascii=False)) for i in b), 100_000)

    def test_oversized_single_item_kept_whole(self):
        """單項超過預算時獨立成批，不截斷、不丟棄——寧可批大也不能少給資料。"""
        items = [{"doc": "x" * 250_000}, {"doc": "y" * 10}]
        batches = pf.split_into_batches(items, max_chars=100_000)
        self.assertEqual(len(batches), 2)
        self.assertEqual(len(batches[0]), 1)

    def test_no_item_is_lost(self):
        """切批不得遺漏任何項目（總數守恆）。"""
        items = [{"i": i, "doc": "x" * 8_000} for i in range(37)]
        batches = pf.split_into_batches(items, max_chars=50_000)
        self.assertEqual(sum(len(b) for b in batches), 37)

    def test_empty_input(self):
        self.assertEqual(pf.split_into_batches([]), [])


class CliCommandTests(unittest.TestCase):
    """命令列只帶短指示與檔案路徑，資料不進 argv。"""

    def test_argv_is_short_regardless_of_payload_size(self):
        """就算資料 128K，argv 仍遠低於 Windows 32,767 上限。"""
        with TemporaryDirectory() as tmp:
            big = {"topics": [{"doc": "x" * 130_000}]}
            p = pf.write_payload_file("topic_label", big, root=Path(tmp), run_id=3)
            argv = pf.build_cli_command_with_payload(
                "claude", instruction="為每個 topic 命名", payload_path=p
            )
            total = sum(len(a) for a in argv) + len(argv)
            self.assertLess(total, 4000, f"argv 應保持短小，實測 {total}")

    def test_grants_read_only(self):
        """權限從零放寬到**只有 Read**：不得出現 Write／Bash／網路工具。"""
        with TemporaryDirectory() as tmp:
            p = pf.write_payload_file("topic_label", {}, root=Path(tmp), run_id=4)
            argv = pf.build_cli_command_with_payload(
                "claude", instruction="x", payload_path=p
            )
            joined = " ".join(argv)
            self.assertIn("--allowedTools", joined)
            idx = argv.index("--allowedTools")
            tools = argv[idx + 1]
            self.assertEqual(tools, "Read", "只能給 Read，不得含 Write/Bash/WebFetch")

    def test_payload_path_present_in_instruction(self):
        """指示需明確含檔案路徑，CLI 才知道要讀哪一份。"""
        with TemporaryDirectory() as tmp:
            p = pf.write_payload_file("topic_label", {}, root=Path(tmp), run_id=5)
            argv = pf.build_cli_command_with_payload(
                "claude", instruction="為每個 topic 命名", payload_path=p
            )
            self.assertTrue(any(str(p) in a for a in argv))


if __name__ == "__main__":
    unittest.main()
