"""選圖包要保存 web／PPT 雙 profile 的 checksum lineage（A2，2026-08-09）。

## 為什麼需要

使用者在**網頁**上看圖選圖，簡報用的是**同一 identity 的 PPT profile**——兩者
是不同的檔案。目前 bundle 只有一個 checksum（綁 PPT 圖＋數據），所以：

- 使用者看到的那張 web 圖有沒有跟著換版本，**無從查核**
- 兩個 profile 若來自不同次產圖（例如只重產了一半），也**沒有東西攔得住**

EXP-018 要求「evidence manifest 為每個選項列出 web 與 PPT profile checksum
lineage」。⚠ 這不改變 PPT 的內容，但少了它，錯配發生時不會有人發現。
"""
from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from backend.app.reports import chart_bundle as cb
from backend.app.reports import chart_profiles as cp


def _make_run_dir(root: Path, *, with_web: bool = True) -> Path:
    run_dir = root / "report_trial_x"
    run_dir.mkdir()
    (run_dir / "lifecycle.svg").write_text("<svg>ppt</svg>", encoding="utf-8")
    if with_web:
        (run_dir / cp.profile_filename("lifecycle.svg", "web")).write_text(
            "<svg>web-bigger</svg>", encoding="utf-8")
    (run_dir / "report_data.json").write_text(json.dumps({
        "version": "report_trial_x",
        "chart_rows": {"lifecycle": [{"applicant_display_name": "甲", "patent_count": 3}]},
        "sections": [{"title": "專利狀態分析", "report_key": "lifecycle",
                      "variants": [{"label": "專利狀態分析", "variant_key": "default",
                                    "file": "lifecycle.svg"}]}],
    }, ensure_ascii=False), encoding="utf-8")
    (run_dir / "artifact_manifest.json").write_text(json.dumps({
        "artifacts": [{"file": "lifecycle.svg", "report_name": "lifecycle",
                       "report_names": ["lifecycle"]}],
    }, ensure_ascii=False), encoding="utf-8")
    return run_dir


class ProfileLineageTests(unittest.TestCase):
    def test_bundle_records_both_profile_checksums(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = _make_run_dir(root)
            bundles = cb.build_selected_bundles(
                run_dir, {"lifecycle:default"}, root / "work")
            lineage = bundles[0]["profile_lineage"]
            self.assertEqual(sorted(lineage), ["ppt", "web"])
            self.assertTrue(lineage["ppt"]["checksum"])
            self.assertNotEqual(lineage["ppt"]["checksum"], lineage["web"]["checksum"],
                                "兩 profile 的 checksum 相同代表根本沒分流")
            self.assertEqual(lineage["ppt"]["path"], "lifecycle.svg")
            self.assertEqual(lineage["web"]["path"], "lifecycle.web.svg")

    def test_legacy_version_without_web_profile_is_disclosed(self):
        """⚠ 舊版本沒有 web profile：要如實標示，不是靜默省略。

        2026-08-09 之前的每個報表版本都只有一份圖。這種情況不該讓打包失敗
        （使用者仍該產得出簡報），但 lineage 必須看得出「web 這一份不存在」。
        """
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = _make_run_dir(root, with_web=False)
            bundles = cb.build_selected_bundles(
                run_dir, {"lifecycle:default"}, root / "work")
            lineage = bundles[0]["profile_lineage"]
            self.assertIn("ppt", lineage)
            self.assertIsNone(lineage.get("web"),
                              "缺 web profile 要留成 None，讓它現形而不是消失")

    def test_ppt_checksum_still_binds_image_and_data(self):
        """⚠ 對照組：既有的 checksum 語意（圖＋數據）不得因為新增 lineage 而改變。"""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = _make_run_dir(root)
            first = cb.build_selected_bundles(
                run_dir, {"lifecycle:default"}, root / "work1")[0]["checksum"]
            (run_dir / "lifecycle.svg").write_text("<svg>ppt-changed</svg>", encoding="utf-8")
            second = cb.build_selected_bundles(
                run_dir, {"lifecycle:default"}, root / "work2")[0]["checksum"]
            self.assertNotEqual(first, second, "圖片變了 checksum 必須跟著變")


if __name__ == "__main__":
    unittest.main()
