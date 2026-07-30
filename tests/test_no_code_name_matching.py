"""無代碼 → 名稱兩級比對（規格 applicant-code-grouping-spec.md 批次 a）。

## 問題

`govern_company_names` 第一道檢查 `if not code or not variant: continue`
——無代碼直接跳過。實測 60 筆專利中 **57 筆（95%）無代碼**，
使用者手動建的 20 組 TEMP 對新資料**完全沒有作用**。

## 定案（使用者「B，但分兩級」）

| 級別 | 規則 | 動作 |
|---|---|---|
| L1 完全相同 | 現有 `normalize_lookup`（小寫＋空白收斂） | ✅ 自動歸戶 |
| L2 疑似 | 再去標點／剝後綴／DBA 切斷 | ⚠ **只提示不寫入** |
| L3 無 | 都不命中 | 待補清單（現況） |

⚠ **L2 不自動寫入**：忽略後綴後「A CO., LTD.」與「A INC.」會同 key，
但那可能是兩家不同法人。誤歸戶比漏歸戶難修（要人工找出並拆開）。
"""
from __future__ import annotations

import sys
import unittest
from unittest import mock


class L2NormalizeTests(unittest.TestCase):
    """L2 正規化：去標點、剝結尾後綴、DBA 切斷。"""

    @staticmethod
    def _key(name: str):
        from backend.app.derived.company_alias_importer import normalize_loose

        return normalize_loose(name)

    def test_strips_punctuation(self):
        self.assertEqual(self._key("Co.,Ltd."), self._key("Co Ltd"))

    def test_case_and_space_insensitive(self):
        """L1 已處理的大小寫／空白，L2 也要維持。"""
        self.assertEqual(self._key("OxeFit,  Inc."), self._key("OXEFIT INC"))

    def test_strips_trailing_suffix(self):
        """結尾公司後綴剝除——這是 L2 與 L1 的主要差別。"""
        self.assertEqual(self._key("MOTIOFY AB"), self._key("Motiofy"))
        self.assertEqual(self._key("NPD Team, LLC"), self._key("NPD Team"))

    def test_dba_cut_not_stripped(self):
        """🔴 `DBA` 要**切斷**不是剝除（實測資料驅動）。

        `SKI-ROW INC DBA ENERGYFIT` 的 DBA 在**中間**不在結尾。
        當後綴剝掉會得到 `SKI-ROW INC ENERGYFIT`——**不存在的名稱**，比不處理更糟。
        切斷後取 `SKI-ROW`（法人本名，INC 再被剝除）。
        """
        key = self._key("SKI-ROW INC DBA ENERGYFIT")
        self.assertNotIn("energyfit", key, "DBA 後段未切斷")
        self.assertEqual(key, self._key("SKI-ROW INC"),
                         "DBA 切斷後應等同法人本名")

    def test_different_companies_still_differ(self):
        """⚠ 不同公司不得因剝後綴而同 key（L2 的誤歸風險上限）。"""
        self.assertNotEqual(self._key("ALPHA CO LTD"), self._key("BETA CO LTD"))

    def test_empty_safe(self):
        self.assertIn(self._key(""), (None, ""))
        self.assertIn(self._key(None), (None, ""))


class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchall(self):
        return self._rows


class _Conn:
    """假連線（不碰真實 DB——使用者紅線）。"""

    def __init__(self, existing):
        self.existing = existing
        self.inserts: list[tuple] = []

    def execute(self, sql, params=()):
        if "SELECT DISTINCT" in sql:
            return _Cur([])
        if "SELECT" in sql:
            return _Cur(self.existing)
        if "INSERT" in sql:
            self.inserts.append(params)
        return _Cur([])

    def commit(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _run(existing, pairs):
    from backend.app.derived import company_alias_importer as m

    conn = _Conn(existing)
    fake = mock.MagicMock()
    fake.connect.return_value = conn
    with mock.patch.dict(sys.modules, {"psycopg": fake}):
        return m.govern_company_names(pairs, connect_kwargs={}), conn


# 既有組：TEMP 代碼（使用者手建）。
# ⚠ `TEMP:oxefit` 的**正式名與別稱字面不同**——這才驗得到「命中正式名 → 補別稱」；
# 若兩者同字面，新寫法 normalize 後會與既有別稱相同而走 skipped（那是另一條路徑）。
EXISTING = [
    ("TEMP:motiofy", "", "MOTIOFY AB", "MOTIOFY AB"),
    ("TEMP:oxefit", "", "OxeFit, Inc.", "OXEFIT"),
]


class NoCodeMatchingTests(unittest.TestCase):
    """無代碼時走名稱比對。"""

    def test_l1_exact_match_auto_groups(self):
        """L1：normalize 後與該組**正式名**相同 → 自動補一列別稱。

        ⚠ 用大小寫／空白不同的寫法（`oxefit,  inc.`）而非既有別稱字面：
        既有別稱 normalize 後相同會走 skipped 分支（那是 test_l1_existing_variant_skipped
        驗的）。這裡要驗的是「新寫法命中正式名 → 補進該組」。
        """
        result, conn = _run(EXISTING, [(None, "oxefit,   INC.")])
        self.assertEqual(result["inserted"], 1,
                         "L1 完全命中未自動歸戶——TEMP 組對新資料仍無作用")
        self.assertTrue(any(p[0] == "TEMP:oxefit" for p in conn.inserts),
                        "歸到錯的組")

    def test_l1_existing_variant_skipped(self):
        """已存在的別稱不重複寫入。"""
        result, _conn = _run(EXISTING, [(None, "MOTIOFY AB")])
        self.assertEqual(result["inserted"], 0)
        self.assertEqual(result["skipped_existing"], 1)

    def test_l2_suspected_not_written(self):
        """🔴 L2 疑似**不得自動寫入**，只回報供使用者確認。"""
        result, conn = _run(EXISTING, [(None, "OXEFIT INC")])
        self.assertEqual(result["inserted"], 0,
                         "L2 疑似被自動寫入——誤歸戶比漏歸戶難修")
        suspected = result.get("suspected") or []
        self.assertTrue(suspected, "L2 命中未回報")
        self.assertEqual(suspected[0]["alias_name"], "OXEFIT INC")
        self.assertEqual(suspected[0]["company_code"], "TEMP:oxefit",
                         "未指出疑似哪一組")

    def test_l3_no_match_untouched(self):
        """都不命中 → 不寫入、不回報疑似（維持進待補清單）。"""
        result, conn = _run(EXISTING, [(None, "COMPLETELY UNRELATED GMBH")])
        self.assertEqual(result["inserted"], 0)
        self.assertFalse(result.get("suspected"))
        self.assertFalse(conn.inserts)

    def test_code_path_unchanged(self):
        """⚠ 有代碼的路徑不得受影響（批次 b 的回歸）。"""
        result, _conn = _run(EXISTING, [("TEMP:motiofy", "Motiofy Sweden AB")])
        self.assertEqual(result["inserted"], 1)
        self.assertFalse(result.get("suspected"),
                         "有代碼時不該走 L2 疑似路徑")


if __name__ == "__main__":
    unittest.main()


class SuspectedSurfacingTests(unittest.TestCase):
    """L2 疑似要讓使用者看得到（否則算了等於白做）。

    ⚠ 實測（2026-07-30）：目前待補 11 項**全部無命中**——它們是自然人與一個
    DBA 機構，與既有 21 組無任何字面關聯。故 L1/L2 不會誤動現有資料；
    L2 的價值在**未來新資料**（如新匯入 `MOTIOFY AB CO` 這種變體）。
    """

    def test_import_summary_shows_suspected(self):
        """匯入結果要顯示疑似筆數，並指出去哪確認。"""
        import re
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1]
                / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        body = re.search(r"function aliasVariantsHtml\(av\) \{.*?\n\}", html, re.S)
        self.assertIsNotNone(body, "找不到 aliasVariantsHtml")
        self.assertIn("suspected", body.group(0),
                      "匯入結果未顯示 L2 疑似——算了卻沒人看得到")

    def test_suspected_wording_is_cautious(self):
        """⚠ 措辭要表達「需你確認」而非「已歸戶」——L2 本來就不寫入。"""
        import re
        from pathlib import Path

        html = (Path(__file__).resolve().parents[1]
                / "backend" / "app" / "static" / "index.html").read_text(encoding="utf-8")
        body = re.search(r"function aliasVariantsHtml\(av\) \{.*?\n\}", html, re.S).group(0)
        code = "\n".join(l for l in body.split("\n") if not l.strip().startswith("//"))
        self.assertIn("疑似", code, "缺疑似標示")
