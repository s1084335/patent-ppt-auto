"""排名／KP 表格的每一欄都要有中文欄名（2026-08-11 使用者指示修正）。

## 實機

HTML 報表逐卡驗收（2026-08-11）發現兩張卡的表頭直接印內部欄名：

- 主要申請人排名：`joint_count`、`joint_transferred_count`、
  `solo_transferred_count`、`co_applicant_names`
- Key Players 競爭定位：`country_count`、`ipc_subclass_count`、`patent_ids`、
  `granted_count`、`pending_count`、`dead_count`、`kind_summary`

違反 content_standard「內部欄名不得出現在任何頁面」。對照的唯一定義處＝
`DATA_COLUMN_LABELS`（R2 定案：前端與 index 都只消費、不自建對照），
缺鍵時表頭就原樣印出——**缺鍵不報錯**，是靜默的，故立此測試釘住。

⚠ 本測試釘「這兩張表實際會出現的欄」都有鍵，不是釘全字典——
新報表加欄時這裡不用改，該報表自己的欄要自己補。
"""
from __future__ import annotations

import unittest

from backend.app.reports.chart_runner import DATA_COLUMN_LABELS

# 兩張表實際輸出的欄（依 2026-08-11 實機 HTML 表頭盤點）
RANKING_COLUMNS = (
    "applicant_display_name", "patent_count", "recent_assignee_display_names",
    "recent_assignee_count", "joint_count", "joint_transferred_count",
    "solo_transferred_count", "co_applicant_names",
)
KP_COLUMNS = (
    "applicant_display_name", "patent_count", "family_count", "country_count",
    "topic_count", "ipc_subclass_count", "patent_ids",
    "granted_count", "pending_count", "dead_count", "kind_summary",
)


class ColumnLabelTests(unittest.TestCase):
    def test_ranking_columns_all_labelled(self):
        for key in RANKING_COLUMNS:
            with self.subTest(column=key):
                self.assertIn(key, DATA_COLUMN_LABELS,
                              f"排名表欄 {key} 缺中文欄名，表頭會印內部欄名")

    def test_kp_columns_all_labelled(self):
        for key in KP_COLUMNS:
            with self.subTest(column=key):
                self.assertIn(key, DATA_COLUMN_LABELS,
                              f"KP 表欄 {key} 缺中文欄名，表頭會印內部欄名")

    def test_patent_ids_hidden_from_kp_table(self):
        """🔴 patent_ids 整欄不顯示（2026-08-11 使用者：「修掉 patent_ids」）。

        一串內部 id 佔一大欄卻不給讀者任何判斷。⚠ 只藏**顯示**：rows 保留
        patent_ids——解讀 CLI 靠它逐件取證（2026-08-10「每家全取」定案），
        資料拿掉解讀深度就沒了。前端與 index 共用同一份排除表。
        """
        from backend.app.reports.chart_runner import DATA_TABLE_EXCLUDED_COLUMNS

        self.assertIn("patent_ids",
                      DATA_TABLE_EXCLUDED_COLUMNS.get("applicant_strength_profile", ()),
                      "KP 表仍會印整串 patent_ids")

    def test_labels_are_chinese_not_identifiers(self):
        """欄名不得只是把底線換掉的英文——那還是內部欄名。"""
        for key in RANKING_COLUMNS + KP_COLUMNS:
            label = DATA_COLUMN_LABELS.get(key, "")
            if not label:
                continue
            self.assertFalse(label.isascii(), f"{key} 的欄名 {label!r} 不是中文")


if __name__ == "__main__":
    unittest.main()
