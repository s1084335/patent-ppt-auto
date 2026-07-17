"""legal_status 正規化的契約測試。

契約：WIPS 状态原始值（簡體中文＋可選括號註記）→ alive/dead/pending/unknown；
未知值必須回 unknown 現形，不可誤判成其他狀態。
"""
from __future__ import annotations

import unittest

from backend.app.mappings.legal_status import (
    STATUS_ALIVE,
    STATUS_DEAD,
    STATUS_PENDING,
    STATUS_UNKNOWN,
    normalize_legal_status,
)


class LegalStatusNormalizationTests(unittest.TestCase):
    """normalize_legal_status 的輸入輸出契約。"""

    def test_alive_values(self) -> None:
        """授权（含繁體/英文別名）判為 alive。"""
        for raw in ("授权", "授權", "Registered", "GRANTED", "  授权  "):
            self.assertEqual(normalize_legal_status(raw), STATUS_ALIVE, raw)

    def test_dead_values_with_paren_annotations(self) -> None:
        """到期系列帶括號註記（DB 實際觀測值）一律判 dead。"""
        for raw in (
            "到期(Non-payment of Renewal / Annual fee)",
            "到期(Termination of patent right due to unpaid annual fee)",
            "到期(Expiration of the term)",
            "到期(Reissued)",
            "到期",
            "放弃",
            "撤回",
            "拒绝",
            "删除",
        ):
            self.assertEqual(normalize_legal_status(raw), STATUS_DEAD, raw)

    def test_fullwidth_paren_annotation(self) -> None:
        """全形括號註記同樣要能剝除。"""
        self.assertEqual(normalize_legal_status("到期（年費未繳）"), STATUS_DEAD)

    def test_pending_values(self) -> None:
        """审查中/申请/公开 判為 pending（尚未取得保護）。"""
        for raw in ("审查中", "申请", "公开", "Pending"):
            self.assertEqual(normalize_legal_status(raw), STATUS_PENDING, raw)

    def test_unknown_values_surface(self) -> None:
        """空值與未知詞回 unknown，不可無聲歸類。"""
        for raw in (None, "", "   ", "沒看過的狀態", "weird-status"):
            self.assertEqual(normalize_legal_status(raw), STATUS_UNKNOWN, repr(raw))


if __name__ == "__main__":
    unittest.main()
