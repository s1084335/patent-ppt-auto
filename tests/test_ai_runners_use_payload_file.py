"""三支高風險 AI runner 一律走資料檔，不把大量資料塞進命令列（2026-07-27）。

風險盤點（實測）：
| runner            | 塞進 argv 的內容            | 長度      | 狀態 |
|-------------------|----------------------------|-----------|------|
| topic_label       | 10 主題 × 5 篇獨立項全文    | 128,101   | 已爆 |
| patent_note       | 主權項全文（CHAR_BUDGET 12k）| ~12k+    | 逼近 |
| irrelevant_filter | 50 筆文獻備註               | 6,589     | 會漲 |

Windows CreateProcess 上限 32,767。三支同屬「靠剛好塞得下」的脆弱設計，
故一併改走 `ai_payload_file`（使用者定案：不要散落）。

本測試以「實際跑一次、攔截 argv」驗證，不只看程式碼字串。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.worker import ai_payload_file as pf

WINDOWS_CMDLINE_LIMIT = 32_767


def _argv_len(argv) -> int:
    return sum(len(a) for a in argv) + len(argv)


class TopicLabelUsesPayloadFileTests(unittest.TestCase):
    """topic_label：實測 128K，改檔案後 argv 必須遠低於上限。"""

    def test_argv_stays_small_with_large_payload(self):
        from backend.app.worker import ai_topic_label_runner as r

        topics = [
            {"topic_code": f"T{i:03d}",
             "representative_patents": ["專利獨立項全文 " * 300 for _ in range(5)]}
            for i in range(10)
        ]
        payload = {"workspace_id": 1, "source_field": "wips_independent_claims",
                   "run_id": 1, "instruction": "為每個主題命名", "topics": topics}

        captured = {}

        def fake_cli(argv, timeout):
            captured["argv"] = list(argv)
            return r.CliResult(exit_code=0, stdout=json.dumps(
                {"topics": [{"topic_code": t["topic_code"], "label": "測試",
                             "summary": "測試摘要"} for t in topics]}), stderr="")

        with TemporaryDirectory() as tmp:
            r.run_topic_label(
                workspace_id=1, source_field="wips_independent_claims",
                cli_runner=fake_cli,
                payload_builder=lambda **kw: payload,
                apply_labels=lambda **kw: {"updated": len(topics)},
                payload_root=Path(tmp),
            )
            total = _argv_len(captured["argv"])
            self.assertLess(total, WINDOWS_CMDLINE_LIMIT,
                            f"argv {total:,} 仍超過 Windows 上限")
            self.assertLess(total, 4000, f"argv 應維持短小，實測 {total:,}")
            # 資料確實落檔；128K 在 150K 預算內故維持單批（現行資料量不分批），
            # 無論批數多少，主題總數都須守恆
            files = sorted((Path(tmp) / "topic_label").glob("*.json"))
            self.assertEqual(len(files), 1, "128K 在 150K 預算內，不應分批")
            total_topics = sum(
                len(json.loads(f.read_text(encoding="utf-8"))["topics"]) for f in files)
            self.assertEqual(total_topics, 10, "分批不得遺漏主題")

    def test_splits_when_over_budget(self):
        """超過 150K 預算才分批（例如 5000 筆專利／40 主題約 512KB）。"""
        from backend.app.worker import ai_topic_label_runner as r
        from tests.ai_payload_test_helpers import topic_codes_from_argv

        topics = [
            {"topic_code": f"T{i:03d}",
             "representative_patents": ["專利獨立項全文 " * 300 for _ in range(5)]}
            for i in range(40)
        ]

        def fake_cli(argv, timeout):
            codes = topic_codes_from_argv(argv)
            return r.CliResult(exit_code=0, stdout=json.dumps(
                {"topics": [{"topic_code": c, "label": "測試", "summary": "摘要"}
                            for c in codes]}), stderr="")

        with TemporaryDirectory() as tmp:
            r.run_topic_label(
                workspace_id=1, source_field="wips_independent_claims",
                cli_runner=fake_cli,
                payload_builder=lambda **kw: {"topics": topics},
                apply_labels=lambda **kw: {"updated_count": len(kw["labels"])},
                payload_root=Path(tmp),
            )
            files = sorted((Path(tmp) / "topic_label").glob("*.json"))
            self.assertGreater(len(files), 1, "超過 150K 應分批")
            total = sum(len(json.loads(f.read_text(encoding="utf-8"))["topics"])
                        for f in files)
            self.assertEqual(total, 40, "分批不得遺漏主題")

    def test_cli_gets_read_only_permission(self):
        """權限只放寬到 Read；不得出現 Write／Bash。"""
        from backend.app.worker import ai_topic_label_runner as r
        captured = {}

        def fake_cli(argv, timeout):
            captured["argv"] = list(argv)
            return r.CliResult(exit_code=0, stdout=json.dumps(
                {"topics": [{"topic_code": "T001", "label": "測試", "summary": "摘要"}]}),
                stderr="")

        with TemporaryDirectory() as tmp:
            r.run_topic_label(
                workspace_id=1, source_field="wips_independent_claims",
                cli_runner=fake_cli,
                payload_builder=lambda **kw: {
                    "topics": [{"topic_code": "T001", "representative_patents": ["x"]}]},
                apply_labels=lambda **kw: {"updated_count": 1},
                payload_root=Path(tmp),
            )
        argv = captured["argv"]
        idx = argv.index("--allowedTools")
        self.assertEqual(argv[idx + 1], "Read")

    def test_forbidden_keys_still_stripped(self):
        """🔴 紅線不得因改用檔案而失效：keywords 絕不能出現在資料檔內。"""
        from backend.app.worker import ai_topic_label_runner as r

        def fake_cli(argv, timeout):
            return r.CliResult(exit_code=0, stdout=json.dumps(
                {"topics": [{"topic_code": "T001", "label": "測試", "summary": "摘要"}]}),
                stderr="")

        with TemporaryDirectory() as tmp:
            r.run_topic_label(
                workspace_id=1, source_field="wips_independent_claims",
                cli_runner=fake_cli,
                payload_builder=lambda **kw: {
                    "topics": [{"topic_code": "T001", "keywords": ["洩漏", "禁止"],
                                "representative_patents": ["x"]}]},
                apply_labels=lambda **kw: {"updated_count": 1},
                payload_root=Path(tmp),
            )
            written = (Path(tmp) / "topic_label").glob("*.json")
            text = next(written).read_text(encoding="utf-8")
            self.assertNotIn("keywords", text)
            self.assertNotIn("洩漏", text)


class PatentNoteUsesPayloadFileTests(unittest.TestCase):
    """patent_note：主權項全文不得進 argv。"""

    def test_argv_stays_small(self):
        from backend.app.worker import ai_patent_note_runner as r
        captured = {}

        def fake_cli(argv, timeout):
            captured["argv"] = list(argv)
            return r.CliResult(exit_code=0, stdout=json.dumps(
                {"notes": [{"patent_id": 1, "note": "測試備註"}]}), stderr="")

        batch = [(1, "主權項全文 " * 2000)]
        with TemporaryDirectory() as tmp:
            r.run_patent_note(
                patents=batch, cli_runner=fake_cli,
                apply_notes=lambda **kw: {"updated": 1},
                payload_root=Path(tmp),
            )
        total = _argv_len(captured["argv"])
        self.assertLess(total, 4000, f"argv 應維持短小，實測 {total:,}")


class IrrelevantFilterUsesPayloadFileTests(unittest.TestCase):
    """irrelevant_filter：50 筆備註不得進 argv。"""

    def test_argv_stays_small(self):
        from backend.app.worker import ai_irrelevant_filter_runner as r
        from backend.app.worker.cli_gateway import CliResult
        captured = {}

        def fake_cli(argv, timeout):
            captured["argv"] = list(argv)
            return CliResult(exit_code=0, stdout=json.dumps(
                {"results": [{"patent_id": i, "verdict": "相干"} for i in range(50)]}),
                stderr="")

        cands = [(i, "文獻備註內容 " * 100) for i in range(50)]
        with TemporaryDirectory() as tmp:
            r.run_irrelevant_filter(
                workspace_id=1, candidates=cands, cli_runner=fake_cli,
                payload_root=Path(tmp),
            )
        total = _argv_len(captured["argv"])
        self.assertLess(total, 4000, f"argv 應維持短小，實測 {total:,}")


if __name__ == "__main__":
    unittest.main()
