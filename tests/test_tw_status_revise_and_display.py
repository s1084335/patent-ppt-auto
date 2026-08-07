"""TW 狀態三項定案（2026-08-07 使用者裁決）。

1. 值域用詞：「已核准」→「授權」（對齊 WIPS 授權公告用語）；舊值容忍不刪。
2. 反悔機制：TW 人工線**可改可清**——已有值可改成別的值、可清回空值（未知桶），
   每次異動 append 歷程並刷新狀態分析；非 TW 案不開放。
3. 簡字顯示：mappings（唯一定義處）提供繁體顯示字面，API 帶 display 欄，
   前端只消費不自建對照；DB 原值不動。
"""
from __future__ import annotations

import unittest

from backend.app.mappings import legal_status as m


class AllowedListTests(unittest.TestCase):
    def test_granted_term_matches_wips_column(self):
        """🔴 值域＝WIPS「專利狀態」欄實測聯集（2026-08-07 同日二修定版）：
        granted 該欄標「已核准」×180——「授權」是另一欄用詞，曾誤改已回退。
        使用者原則：值要跟 WIPS 的值一樣，不然等於自加。"""
        self.assertIn("已核准", m.TW_LEGAL_STATUS_ALLOWED)
        self.assertNotIn("授權", m.TW_LEGAL_STATUS_ALLOWED)

    def test_legacy_value_still_normalizes(self):
        """已登錄過的「已核准」仍收斂到 alive——容忍不刪，報表不因遷移空窗出錯。"""
        self.assertEqual(m.normalize_legal_status("已核准"), m.STATUS_ALIVE)
        self.assertEqual(m.normalize_legal_status("授權"), m.STATUS_ALIVE)

    def test_analysis_map_covers_all_allowed(self):
        for status in m.TW_LEGAL_STATUS_ALLOWED:
            with self.subTest(status=status):
                self.assertNotEqual(
                    m.TW_LEGAL_STATUS_ANALYSIS_MAP[status], m.STATUS_UNKNOWN,
                    f"{status} 落未知桶——登錄白做")


class DisplayTests(unittest.TestCase):
    def test_simplified_terms_display_traditional(self):
        cases = {
            "授权": "授權",
            "审查中": "審查中",
            "申请": "申請",
            "放弃": "放棄",
            "无效": "無效",
        }
        for raw, disp in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(m.display_legal_status(raw), disp)

    def test_annotation_preserved(self):
        """到期的括號說明保留（本體已是繁體通用字形，不硬翻）。"""
        raw = "到期(Non-payment of Renewal / Annual fee)"
        self.assertEqual(m.display_legal_status(raw), raw)

    def test_traditional_and_unknown_pass_through(self):
        self.assertEqual(m.display_legal_status("授權"), "授權")
        self.assertEqual(m.display_legal_status("沒見過的值"), "沒見過的值")
        self.assertIsNone(m.display_legal_status(None))


class ReviseAndClearContractTests(unittest.TestCase):
    def test_register_sql_allows_overwrite(self):
        """🔴 反悔：更新不再限定原值為空——空值守門拆掉，改由 country 條件守。"""
        from backend.app.app_layer import patent_queries as q

        self.assertNotIn("NULLIF(BTRIM(p.legal_status), '') IS NULL",
                         q._REGISTER_TW_STATUS_SQL)
        self.assertIn("country_code = 'TW'", q._REGISTER_TW_STATUS_SQL)

    def test_clear_accepts_none(self):
        """清回空值＝未知桶；validate 只擋值域外字串，不擋 None。"""
        from backend.app.app_layer import patent_queries as q

        self.assertIsNone(q.normalize_tw_status_input(None))
        self.assertEqual(q.normalize_tw_status_input("已核准"), "已核准")
        with self.assertRaises(ValueError):
            q.normalize_tw_status_input("授權")

    def test_panel_lists_all_tw_not_only_pending(self):
        """面板要列**全部** TW 案（含已登錄者才有東西可反悔），items 帶現值。"""
        from backend.app.app_layer import patent_queries as q

        # 指名狀態欄的空值過濾（workspace_id 的 IS NULL 是另一回事，不誤傷）。
        self.assertNotIn("NULLIF(BTRIM(p.legal_status), '') IS NULL",
                         q._PENDING_TW_STATUS_WHERE)
        self.assertIn("country_code = 'TW'", q._PENDING_TW_STATUS_WHERE)
        self.assertIn("legal_status", q._PENDING_TW_STATUS_ITEMS_SQL)


class FrontendWiringTests(unittest.TestCase):
    HTML = None

    @classmethod
    def setUpClass(cls):
        from pathlib import Path
        cls.HTML = (Path(__file__).resolve().parents[1] / "backend" / "app"
                    / "static" / "index.html").read_text(encoding="utf-8")

    def test_patent_status_column_uses_display_field(self):
        self.assertIn("'legal_status_display'", self.HTML)

    def test_tw_panel_has_clear_action(self):
        self.assertIn("清除", self.HTML)

    def test_frontend_does_not_own_conversion_table(self):
        """簡→繁對照只能活在後端 mappings——前端出現「审查中」字面＝自建第二份。"""
        self.assertNotIn("审查中", self.HTML)


if __name__ == "__main__":
    unittest.main()
