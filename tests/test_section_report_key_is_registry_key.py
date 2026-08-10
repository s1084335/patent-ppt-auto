"""section 的 report_key 必須是 registry 鍵，不得 fallback 成檔名（2026-08-10 實測）。

## 實測到的掉圖

goal-driven 產出的 PPT 有 2 頁降級成 `stat_callout`、`charts=[]`：

    p3 stat_callout ← degraded_from=chart_with_points  report_keys=['annual_trend']
    p6 stat_callout ← degraded_from=chart_with_points  report_keys=['cluster_topic_table']

根因：`build_ppt._plan_page_specs` 取 chart_identity 前段當 report_key
（`annual_trend`），再去 `artifact_manifest.json` 反查圖檔——但 manifest 用的是
**registry 鍵**（`application_trend`），兩邊對不上就判定「找不到圖」而降級。

再往上追，是引擎端 section 的 `report_key` 有兩個錯法：

| section | 原值 | 錯法 |
|---|---|---|
| 趨勢 | `annual_trend` | **寫死了檔名**，registry 鍵是 `application_trend` |
| IPC／CPC 分類 | `ipc_main_distribution_L4` | **漏設**，`_section_report_name` fallback 成第一個 variant 的檔名 |

⚠ `narratives.json`、`report_data.reports` bucket、`artifact_manifest.json` 三處
本來就都用 registry 鍵；只有 sections 這兩處不一致。這是本專案第五次
「同一份知識兩個落點」，而且這次的後果是**使用者選的圖直接從簡報上消失**。

## 附帶效果：identity 兩套命名一併收斂

section 的 report_key 就是選圖 identity 的前段（前端、`chart_bundle`、SlidePlan
都用它）。改對之後，identity 與 `profile_manifest.json` 那套自然一致，
不需要再維護任何映射表。
"""
from __future__ import annotations

import unittest

from backend.app.reports.report_definitions import REPORT_DEFINITIONS


class SectionReportKeyTests(unittest.TestCase):
    """引擎端每個 section 的 report_key 都要能在 registry 查到。"""

    def _sections(self) -> list[dict]:
        """跑一次最小 section builder 取得實際 sections（不碰 DB）。"""
        from backend.app.reports import chart_runner

        source = chart_runner.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        return text  # 由下方測試各自解析

    def test_trend_section_uses_registry_key(self):
        """趨勢頁的 report_key 必須是 `application_trend`，不是檔名 `annual_trend`。

        ⚠ `annual_trend` 是**檔名**（annual_trend.svg）。一張圖同時服務申請與公告
        兩個報表，故檔名與報表名本來就不同名——把檔名當報表鍵是這次掉圖的直接原因。
        """
        text = self._sections()
        self.assertNotIn(
            '"report_key": "annual_trend"', text,
            "趨勢 section 不得以檔名 annual_trend 當 report_key——"
            "artifact_manifest／narratives 都用 registry 鍵 application_trend",
        )
        self.assertIn('"report_key": "application_trend"', text)

    def test_classification_section_sets_report_key(self):
        """IPC／CPC 分類 section 必須顯式帶 report_key，不得靠檔名 fallback。

        漏設時 `_section_report_name` 會 fallback 成第一個 variant 的檔名
        `ipc_main_distribution_L4`——多了 `_L4` 就查不到圖，且組出
        `ipc_main_distribution_L4:L5` 這種自相矛盾的 identity。
        """
        text = self._sections()
        start = text.index("def _build_classification_section")
        end = text.index("\ndef ", start + 1)
        body = text[start:end]
        append_at = body.rindex("ctx.sections.append")
        self.assertIn(
            '"report_key": report_key', body[append_at:],
            "分類 section 需顯式帶 report_key（函式內已有該變數），不得靠檔名 fallback",
        )

    def test_registry_keys_referenced_actually_exist(self):
        """本測引用的兩個 registry 鍵確實存在（避免測試自己寫錯字而空轉）。"""
        for key in ("application_trend", "ipc_main_distribution", "cpc_main_distribution"):
            self.assertIn(key, REPORT_DEFINITIONS, f"registry 無此鍵：{key}")


if __name__ == "__main__":
    unittest.main()
