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
from backend.app.transforms.legal_status import (
    BUCKET_DEAD,
    BUCKET_GRANTED,
    BUCKET_PENDING,
    BUCKET_UNKNOWN,
    STATUS_BUCKET_ORDER,
    status_bucket,
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

    def test_tw_manual_curation_values_feed_existing_report_normalizer(self) -> None:
        """TW 人工登錄九項必須進同一個 normalize_legal_status，報表才不會落 unknown。"""
        expected = {
            "已申請": STATUS_PENDING,
            "已公開": STATUS_PENDING,
            "審查中": STATUS_PENDING,
            "已核准": STATUS_ALIVE,
            "放棄": STATUS_DEAD,
            "核駁": STATUS_DEAD,
            "撤回": STATUS_DEAD,
            "已失效": STATUS_DEAD,
            "屆滿失效": STATUS_DEAD,
        }
        for raw, bucket in expected.items():
            self.assertEqual(normalize_legal_status(raw), bucket, raw)

    def test_tw_manual_curation_values_match_report_buckets(self) -> None:
        """TW 九項收斂到 Claude 報表線使用的中文四桶。"""
        expected = {
            "已申請": BUCKET_PENDING,
            "已公開": BUCKET_PENDING,
            "審查中": BUCKET_PENDING,
            "已核准": BUCKET_GRANTED,
            "放棄": BUCKET_DEAD,
            "核駁": BUCKET_DEAD,
            "撤回": BUCKET_DEAD,
            "已失效": BUCKET_DEAD,
            "屆滿失效": BUCKET_DEAD,
        }
        for raw, bucket in expected.items():
            self.assertEqual(status_bucket(raw), bucket, raw)

    def test_report_bucket_order_matches_lifecycle_matrix_contract(self) -> None:
        """報表圖例與矩陣欄序固定，避免登錄端與報表端順序漂移。"""
        self.assertEqual(
            STATUS_BUCKET_ORDER,
            (BUCKET_GRANTED, BUCKET_PENDING, BUCKET_DEAD, BUCKET_UNKNOWN),
        )

    def test_report_buckets_match_claude_lifecycle_status_contract(self) -> None:
        """Claude lifecycle report buckets must stay aligned with TW curation."""
        expected = {
            "\u6388\u6743": BUCKET_GRANTED,
            "\u6388\u6b0a": BUCKET_GRANTED,
            "\u5df2\u6838\u51c6": BUCKET_GRANTED,
            "\u5ba1\u67e5\u4e2d": BUCKET_PENDING,
            "\u5be9\u67e5\u4e2d": BUCKET_PENDING,
            "\u5df2\u7533\u8acb": BUCKET_PENDING,
            "\u5df2\u516c\u958b": BUCKET_PENDING,
            "\u7533\u8bf7": BUCKET_PENDING,
            "\u7533\u8acb": BUCKET_PENDING,
            "\u5230\u671f(Expiration of the term)": BUCKET_DEAD,
            "\u653e\u5f03": BUCKET_DEAD,
            "\u653e\u68c4": BUCKET_DEAD,
            "\u65e0\u6548": BUCKET_DEAD,
            "\u7121\u6548": BUCKET_DEAD,
            "\u64a4\u56de": BUCKET_DEAD,
            "\u6838\u99c1": BUCKET_DEAD,
            "\u5df2\u5931\u6548": BUCKET_DEAD,
            "\u5c46\u6eff\u5931\u6548": BUCKET_DEAD,
            "": BUCKET_UNKNOWN,
            "unknown literal": BUCKET_UNKNOWN,
        }
        for raw, bucket in expected.items():
            self.assertEqual(status_bucket(raw), bucket, raw)

    def test_unknown_values_surface(self) -> None:
        """空值與未知詞回 unknown，不可無聲歸類。"""
        for raw in (None, "", "   ", "沒看過的狀態", "weird-status"):
            self.assertEqual(normalize_legal_status(raw), STATUS_UNKNOWN, repr(raw))


if __name__ == "__main__":
    unittest.main()
