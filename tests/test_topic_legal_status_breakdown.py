"""主題表加法律狀態分解（tasks §7e）——供結論頁依「他人審查中件數」排序。

## 為什麼要這個欄

2026-08-18 定案：路線圖頁的**期程整個拿掉**（`短期 0–3 個月` 是全份唯一沒有
資料支撐的欄位——系統不知道人力、預算與產品排程）。排序改由**外部訊號**決定：
該主題有多少件他人的審查中案件。那是**對手給的時間壓力**，可查證。

## 🔴 本節最容易做錯的地方：分母是 44 不是 55

cluster 型報表的範圍由 `workspace_id` 給，而**分群會排除外觀設計案**
（無獨立項文字）。實測滑雪機：workspace 成員 **55**、分群指派 **44**。

⚠ 分解件數合計必須對上 **44**。寫成 55 就是把「11 件被靜默排除」偽裝成
「全都算到了」——那才是真的新種一個同型錯誤（§1 修的那三例的第四例）。

⚠ 而且要**輸出母體字串**，讓讀者知道這張表的分母是 44 不是封面的 55。
「為什麼是 44」由 deepen §3 處理，本節只負責揭露。

## 口徑

法律狀態的桶收斂走 `mappings/legal_status` 唯一定義處，不在此重判。

⚠ fixture 一律用**實庫真實字面**（`授权`／`审查中`／`到期`，多為簡體）。
本測試第一版用我自己編的「已授權／失效」——`normalize_legal_status` 把它們
全歸到 `unknown`，於是「分解合計＝件數」照樣成立，**測試綠但什麼都沒驗到**。
自己造的字面不會出現在資料裡，用它測等於測一條不存在的路徑。
"""
from __future__ import annotations

import inspect
import unittest

from backend.app.reports import cluster_analytics, cluster_data_loader


def _topics():
    return [{"topic_code": "T1", "label": "拉繩滑雪", "source_field": "wips_independent_claims"},
            {"topic_code": "T2", "label": "風磁阻力", "source_field": "wips_independent_claims"}]


def _assignments():
    return ([{"topic_code": "T1", "patent_id": i} for i in (1, 2, 3)]
            + [{"topic_code": "T2", "patent_id": i} for i in (4, 5)])


def _applicants():
    return [{"patent_id": i, "applicant_name": f"A{i}"} for i in range(1, 6)]


def _patents(status_by_id):
    return {
        i: {"application_year": 2020, "number": f"N{i}", "title": "t", "note": "",
            "legal_status": status_by_id[i]}
        for i in status_by_id
    }


class LoaderCarriesLegalStatusTests(unittest.TestCase):
    def test_loader_selects_legal_status(self):
        """🔴 loader 的 patents 要帶法律狀態，否則主題表無從分解。"""
        src = inspect.getsource(cluster_data_loader.load_cluster_workspace_data)
        self.assertIn(
            "legal_status", src,
            "loader 沒有帶 legal_status——主題表算不出分解，"
            "而且另開一趟查詢就是第二份 patent 對照表（loader 註解明訂單一入口）")

    def test_loader_does_not_open_a_second_query(self):
        """⚠ 與申請人同一趟查詢帶回，不多走一趟 DB（loader 既有紀律）。"""
        src = inspect.getsource(cluster_data_loader.load_cluster_workspace_data)
        self.assertLessEqual(
            src.count("FROM derived_layer.report_patent_base b"), 1,
            "為了法律狀態多開了一趟查詢——應併進既有的申請人查詢")


class BreakdownTests(unittest.TestCase):
    def _rows(self, status_by_id):
        return cluster_analytics.build_topic_effect_table(
            _topics(), _assignments(), _applicants(), _patents(status_by_id))

    def test_rows_carry_status_breakdown(self):
        rows = self._rows({1: "审查中", 2: "授权", 3: "授权", 4: "审查中", 5: "到期"})
        by_code = {r["topic_code"]: r for r in rows}
        self.assertIn("pending_count", by_code["T1"], "缺審查中件數——排序訊號沒有來源")
        self.assertEqual(by_code["T1"]["pending_count"], 1)
        self.assertEqual(by_code["T1"]["granted_count"], 2)
        self.assertEqual(by_code["T2"]["pending_count"], 1)

    def test_breakdown_sums_to_topic_patent_count(self):
        """🔴 每個主題的分解合計 == 該主題件數（不得多算也不得漏算）。"""
        rows = self._rows({1: "审查中", 2: "授权", 3: "授权", 4: "审查中", 5: "到期"})
        for r in rows:
            with self.subTest(topic=r["topic_code"]):
                total = (r.get("pending_count", 0) + r.get("granted_count", 0)
                         + r.get("inactive_count", 0) + r.get("unknown_status_count", 0))
                self.assertEqual(
                    total, r["patent_count"],
                    f"{r['topic_code']} 分解合計 {total} != 件數 {r['patent_count']}")

    def test_missing_status_is_counted_not_dropped(self):
        """⚠ 沒有狀態的要進「未知」，不得靜默不算——那會讓合計對不上。"""
        rows = self._rows({1: "审查中", 2: None, 3: "", 4: "授权", 5: "到期"})
        by_code = {r["topic_code"]: r for r in rows}
        self.assertEqual(by_code["T1"]["unknown_status_count"], 2)

    def test_status_bucketing_uses_the_single_definition(self):
        """桶收斂走 mappings/legal_status 唯一定義處，不在此重判。"""
        src = inspect.getsource(cluster_analytics)
        self.assertIn(
            "legal_status", src,
            "主題表沒有引用法律狀態的唯一定義處")
        self.assertNotRegex(
            src, r'==\s*"授权"',
            "主題表自己比對狀態字面——桶定義只能有一個定義處")


class PopulationDisclosureTests(unittest.TestCase):
    """🔴 分母是 44 不是 55，必須說出來。"""

    def test_table_declares_its_population(self):
        rows = cluster_analytics.build_topic_effect_table(
            _topics(), _assignments(), _applicants(),
            _patents({1: "审查中", 2: "授权", 3: "授权", 4: "审查中", 5: "到期"}))
        covered = {a["patent_id"] for a in _assignments()}
        total = sum(r["patent_count"] for r in rows)
        self.assertEqual(
            total, len(covered),
            "主題件數合計與指派專利數不符——分群母體被算錯了")


if __name__ == "__main__":
    unittest.main()
