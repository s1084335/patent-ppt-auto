"""無代碼備用方案（乙）＋ 變體維護操作（丙）契約（2026-07-28）。

## 乙、無代碼備用方案

背景：WIPS 要一定數量專利才給代碼，待補的 33 間多是 1–3 件的小廠，可能永遠沒代碼。
實測「別稱路徑完全不看代碼」——無代碼也能收斂——但擋在三處：
前端 `confirmCompanyCodes()` 過濾 `g.code &&`、後端 `CodeGroup.code` 有 `min_length=1`、
`apply_confirmed_display_names` 的 mapping key 是代碼（多組 NULL 會撞在一起）。

方案：**臨時代碼 `TEMP:<正規化名 slug>`**
- 不改唯一寫入路徑的 mapping 結構（key 仍是字串代碼）
- 多組無代碼彼此可區分
- 前綴明示是臨時的，UI 標「尚無 WIPS 代碼」
- 補真代碼＝一句 UPDATE 把 TEMP:xxx 換成真代碼，該組所有變體一起換
- ⚠ **不冒充 WIPS 代碼**：使用者定「代碼只能查 WIPS 給」，TEMP 是系統標記不是假代碼

## 丙、變體維護操作

使用者問「按完新增了，想把特定變體解除，怎麼做」→ 目前**做不到**
（既有 DELETE 只有兩處，都是清 AI 草稿 ai_suggested，不含 confirmed）。

三個操作的護欄：
- 移除單一變體：⚠ **不得刪到 canonical 那列**（apply_confirmed_display_names 會把
  canonical 自己也加進別稱；刪了整組顯示名會壞）
- 改公司名：**委派 apply_confirmed_display_names 的 re-canonicalize，不自寫 UPDATE**
- 刪整組：該組專利退回原始字面
- 三者都**必須 enqueue refresh_derived**（本專案既有教訓：「確認完看到表格沒變」）
"""
from __future__ import annotations

import inspect
import unittest
from unittest import mock


# ══════════════ 乙、無代碼備用方案 ══════════════


class TempCodeTests(unittest.TestCase):
    def test_temp_code_helper_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "make_temp_code"), "缺 make_temp_code——無代碼組無法區分")

    def test_temp_code_prefix_is_explicit(self):
        """前綴明示臨時，肉眼即可與 WIPS 代碼區分（不冒充真代碼）。"""
        from backend.app.api import company_aliases as api

        code = api.make_temp_code("Mario Contenti S.r.l.")
        self.assertTrue(code.startswith("TEMP:"), f"臨時代碼未帶 TEMP: 前綴：{code}")

    def test_distinct_names_get_distinct_codes(self):
        """多組無代碼彼此必須可區分，否則全撞成同一組。"""
        from backend.app.api import company_aliases as api

        a = api.make_temp_code("Mario Contenti S.r.l.")
        b = api.make_temp_code("Some Other Co Ltd")
        self.assertNotEqual(a, b)

    def test_same_name_is_stable(self):
        """同一名稱重複送出要落到同一組（否則每次按都長一組新的）。"""
        from backend.app.api import company_aliases as api

        self.assertEqual(
            api.make_temp_code("Mario Contenti S.r.l."),
            api.make_temp_code("  mario   contenti s.r.l. "),
            "同一公司名的臨時代碼不穩定——重複送出會分裂成多組")

    def test_is_temp_code_predicate(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(api.is_temp_code("TEMP:mario-contenti"))
        self.assertFalse(api.is_temp_code("TW-CHIHUA"))

    def test_code_group_allows_blank_code(self):
        """後端 model 放行空代碼（原 min_length=1 擋死無代碼組）。"""
        from backend.app.api import company_aliases as api

        group = api.CodeGroup(code="", zh_name="馬力歐", variants=["MARIO CONTENTI"])
        self.assertEqual(group.code, "")

    def test_blank_code_group_gets_temp_code_in_mapping(self):
        """轉 mapping 時無代碼組自動掛臨時代碼，兩組不得撞在一起。"""
        from backend.app.api import company_aliases as api

        mapping = api.groups_to_alias_mapping([
            {"code": "", "normalized_name": "Mario Contenti S.r.l.", "variants": ["MARIO CONTENTI"]},
            {"code": "", "normalized_name": "Other Co", "variants": ["OTHER CO LTD"]},
        ])
        self.assertEqual(len(mapping), 2, "兩組無代碼撞成一組——臨時代碼未生效")
        self.assertTrue(all(k.startswith("TEMP:") for k in mapping))

    def test_conflict_check_skips_blank_code(self):
        """空代碼不得被當成「同一個代碼」而誤報衝突。"""
        from backend.app.api import company_aliases as api

        conflicts = api.find_code_conflicts([
            {"code": "", "zh_name": "甲", "variants": ["A"]},
            {"code": "", "zh_name": "乙", "variants": ["B"]},
        ], existing={})
        self.assertEqual(conflicts, [], "兩組空代碼被誤判為代碼衝突")


class PromoteTempCodeTests(unittest.TestCase):
    """補真代碼入口：既有代碼區每組可填 WIPS 代碼取代 TEMP。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "promote_company_code"), "缺補真代碼入口")

    def test_updates_all_rows_of_group(self):
        """該組所有變體一起換代碼（一句 UPDATE，不逐列往返）。"""
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.promote_company_code)
        self.assertRegex(src.upper(), r"UPDATE\s+DERIVED_LAYER\.COMPANY_ALIASES")

    def test_refresh_enqueued(self):
        from backend.app.api import company_aliases as api

        self.assertIn("refresh_derived", inspect.getsource(api.promote_company_code),
                      "換代碼後未 refresh——顯示名不會更新")


# ══════════════ 丙、變體維護操作 ══════════════


class RemoveVariantTests(unittest.TestCase):
    """移除單一變體：⚠ 不得刪到 canonical 那列。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "remove_company_variant"))

    def test_refuses_to_delete_canonical_row(self):
        """刪 canonical 列＝整組顯示名壞掉，必須擋（回 409）。

        鎖真實行為：實際呼叫端點，DB 回「這一列的別稱等於該組正式名」，
        端點必須 raise 而非照刪。
        """
        from fastapi import HTTPException

        from backend.app.api import company_aliases as api

        row = {"申請人代碼": "TW-CHIHUA", "別稱": "喬山健康科技",
               "公司中文名稱": "喬山健康科技", "正規化名稱": None}
        deleted: list = []

        class Cur:
            def execute(self, sql, params=None):
                if "DELETE" in sql.upper():
                    deleted.append((sql, params))
                return self

            def fetchone(self):
                return row

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn(Cur):
            def cursor(self, **kw):
                return Cur()

            def commit(self):
                pass

        with mock.patch("psycopg.connect", return_value=Conn()), \
             mock.patch.object(api, "create_job", return_value=1):
            with self.assertRaises(HTTPException) as ctx:
                api.remove_company_variant(alias_id=1)
        self.assertEqual(ctx.exception.status_code, 409)
        self.assertEqual(deleted, [], "已對 canonical 列送出 DELETE——整組顯示名會壞")

    def test_refresh_enqueued(self):
        from backend.app.api import company_aliases as api

        self.assertIn("refresh_derived", inspect.getsource(api.remove_company_variant),
                      "移除變體後未 refresh——畫面不會變")


class RenameGroupTests(unittest.TestCase):
    """改公司名：委派 apply_confirmed_display_names 的 re-canonicalize。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "rename_company_group"))

    def test_delegates_not_self_written_update(self):
        """⚠ 不得自寫 UPDATE ... SET 公司名 —— 規則只有 apply_confirmed_display_names 那份。"""
        from backend.app.api import company_aliases as api

        src = inspect.getsource(api.rename_company_group)
        self.assertIn("apply_confirmed_display_names", src, "未委派唯一寫入路徑")
        self.assertNotRegex(
            src.upper(), r'UPDATE\s+DERIVED_LAYER\.COMPANY_ALIASES\s+SET\s+"?公司',
            "自寫 UPDATE 改名——必然與 re-canonicalize 規則漂移")

    def test_passes_both_names_and_existing_variants(self):
        """改名要把該組**既有變體**一起帶進 mapping，否則只有 canonical 那列被改名。

        鎖真實行為：呼叫端點，側錄交給 writer 的 mapping。
        """
        from backend.app.api import company_aliases as api

        captured = {}

        def fake_writer(mapping, source_label, connect_kwargs=None):
            captured["mapping"] = mapping
            return {"inserted": 0, "updated": 3}

        class Cur:
            def execute(self, sql, params=None):
                return self

            def fetchall(self):
                return [{"別稱": "CHI HUA FITNESS CO LTD"}, {"別稱": "Chi Hua Fitness Co., Ltd."}]

            def fetchone(self):
                return {"n": 2}

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn(Cur):
            def cursor(self, **kw):
                return Cur()

            def commit(self):
                pass

        body = api.RenameGroupRequest(zh_name="喬山健康科技",
                                      normalized_name="Chi Hua Fitness Co., Ltd.")
        with mock.patch("psycopg.connect", return_value=Conn()), \
             mock.patch("backend.app.derived.company_alias_importer.apply_confirmed_display_names",
                        fake_writer), \
             mock.patch.object(api, "create_job", return_value=1):
            api.rename_company_group(code="TW-CHIHUA", body=body)

        spec = captured["mapping"]["TW-CHIHUA"]
        self.assertEqual(spec["zh_name"], "喬山健康科技")
        self.assertEqual(spec["normalized_name"], "Chi Hua Fitness Co., Ltd.")
        self.assertIn("CHI HUA FITNESS CO LTD", spec["aliases"],
                      "既有變體未帶入——改名後其他變體會掛在舊名下")

    def test_refresh_enqueued(self):
        from backend.app.api import company_aliases as api

        self.assertIn("refresh_derived", inspect.getsource(api.rename_company_group))


class DeleteGroupTests(unittest.TestCase):
    """刪整組：該組專利退回原始字面。"""

    def test_endpoint_exists(self):
        from backend.app.api import company_aliases as api

        self.assertTrue(hasattr(api, "delete_company_group"))

    def test_deletes_only_that_code(self):
        """鎖真實行為：DELETE 必須帶代碼條件，不得整表清空。"""
        from backend.app.api import company_aliases as api

        sink: list = []

        class Cur:
            def execute(self, sql, params=None):
                sink.append((sql, params))
                self.rowcount = 4
                return self

            def fetchone(self):
                return (4,)

            def fetchall(self):
                return []

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Conn(Cur):
            def cursor(self, **kw):
                return Cur()

            def commit(self):
                pass

        with mock.patch("psycopg.connect", return_value=Conn()), \
             mock.patch.object(api, "create_job", return_value=1):
            api.delete_company_group(code="TW-CHIHUA")

        deletes = [(s, p) for s, p in sink if "DELETE" in s.upper()]
        self.assertTrue(deletes, "沒有送出 DELETE")
        sql, params = deletes[0]
        self.assertIn("申請人代碼", sql, "DELETE 未限定代碼——會清掉整張對照表")
        flat = list(params) if isinstance(params, (list, tuple)) else list(params.values())
        self.assertIn("TW-CHIHUA", flat)

    def test_refresh_enqueued(self):
        from backend.app.api import company_aliases as api

        self.assertIn("refresh_derived", inspect.getsource(api.delete_company_group))


class ExistingGroupsExposeIdsTests(unittest.TestCase):
    """維護操作要能指到「哪一列」，既有代碼區必須回傳 alias id 與兩個名稱欄。"""

    def test_group_rows_carry_variant_ids(self):
        from backend.app.api import company_aliases as api

        grouped = api.group_aliases_by_code([
            {"id": 2, "申請人代碼": "C1", "公司中文名稱": "甲", "正規化名稱": "A Co",
             "別稱": "A CORP"},
            {"id": 3, "申請人代碼": "C1", "公司中文名稱": "甲", "正規化名稱": "A Co",
             "別稱": "A CO LTD"},
        ])
        variants = grouped[0]["variants"]
        self.assertEqual(
            [v["id"] for v in variants], [2, 3],
            "變體未帶 id——前端無從指定要移除哪一個")
        self.assertEqual(grouped[0]["zh_name"], "甲")
        self.assertEqual(grouped[0]["normalized_name"], "A Co")


if __name__ == "__main__":
    unittest.main()
