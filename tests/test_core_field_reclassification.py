"""專利欄位重分類契約（規格＝`docs/patent_core_field_reclassification_spec.md`）。

## 目標

```text
會被程式用到的欄位 -> core_layer.patents 或 core_layer.patent_people
完全沒被使用的     -> core_layer.patent_attributes
完整原始列         -> raw_records.raw_data（不動）
```

完成後 `patent_attributes` 只作為**未使用 WIPS 欄位的保存區**，
runtime 不得再為已搬出的欄位讀它。

## Preflight 實查（2026-08-06，證據見 `.agents/context/patent-db-claude-plan.md`）

- head ＝ `0045_expanded_view_columns`（新 migration 接在其後）
- 規格點名的 16 欄**全部仍在 `patent_attributes` 且全部有值**
- runtime 真實讀取點 **3 個**：`patent_queries.py`（8 欄＋衍生 grant_year）、
  `target_source.py`（PDF 連結）、`refresh_report_patent_base.py`（授權公告日／
  同族各國家數／EPC 兩欄／引用兩欄／發明人數）
- ⚠ 欄名在 DB 是**繁體**（`解決課題` 非 `解决课题`）；mapping 的 key 才是 WIPS 簡體原名
"""
from __future__ import annotations

import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# WIPS 原始（簡體）欄名 —— mapping 的 key 用這個
MOVE_TO_PATENTS = (
    "摘要(原文)",
    "未审查的公开日",
    "授权公告日",
    "优先权号",
    "优先权国家",
    "优先权日",
    "详细查看链接(登录)",
    "文图像文件(PDF)链接",
    "WIPS同族各国家文献数量(申请为准)",
    "EPC有效国家[EP]",
    "EPC无效国家[EP]",
    "(F1)引用文献数",
    "(B1)引用文献数",
    "解决课题 摘要[US,EP,PCT,JP,KR,CN,TW]",
)
MOVE_TO_PEOPLE = ("发明人数", "申请人数")

# 規格「暫不處理」：這些**不得**被順手搬進 patents
MUST_STAY_IN_ATTRIBUTES = (
    "AI摘要[US,EP,PCT,JP,KR,CN,TW]",
    "技术领域 摘要[US,EP,PCT,JP,KR,CN,TW]",
    "解决手段 摘要[US,EP,PCT,JP,KR,CN,TW]",
    "特征 摘要[US,EP,PCT,JP,KR,CN,TW]",
    "Orig. IPC(All)",
    "Orig. CPC(All)",
    "Curr. IPC(All)",
    "Curr. CPC(All)",
)


class MappingContractTests(unittest.TestCase):
    """mapping 是欄位歸屬的唯一定義處——搬欄位就是改這裡。"""

    def setUp(self):
        from backend.app.mappings import wips

        self.wips = wips

    def test_patents_fields_are_registered(self):
        """要搬到 patents 的欄位必須進 `PATENT_FIELDS`。"""
        for field in MOVE_TO_PATENTS:
            with self.subTest(field=field):
                self.assertIn(field, self.wips.PATENT_FIELDS,
                              f"{field} 沒進 PATENT_FIELDS——匯入時不會寫進 patents")

    def test_people_fields_are_registered(self):
        """要搬到 patent_people 的欄位必須進 `PEOPLE_FIELD_COLUMNS`。"""
        for field in MOVE_TO_PEOPLE:
            with self.subTest(field=field):
                self.assertIn(field, self.wips.PEOPLE_FIELD_COLUMNS,
                              f"{field} 沒進 PEOPLE_FIELD_COLUMNS")

    def test_moved_fields_drop_out_of_attributes(self):
        """🔴 搬走後**不得**還留在 `ATTRIBUTE_FIELDS`。

        ⚠ 留著會讓匯入時 `replace_attributes` 往已移除的欄位 INSERT 而爆
        （0032 移除「文獻備註」時就發生過，註解仍在 `FIELD_GROUPS["documents"]`）。
        """
        for field in MOVE_TO_PATENTS + MOVE_TO_PEOPLE:
            with self.subTest(field=field):
                self.assertNotIn(field, self.wips.ATTRIBUTE_FIELDS,
                                 f"{field} 還在 ATTRIBUTE_FIELDS——匯入會寫進不存在的欄位")

    def test_deferred_fields_are_not_moved(self):
        """⚠ 規格「暫不處理」的欄位**不得**被順手搬走。

        All classification 是多值分類碼，整串放進 patents 做 group by 會統計失真；
        其他 AI 摘要欄目前無程式使用。兩者都要等另案決策。
        """
        for field in MUST_STAY_IN_ATTRIBUTES:
            with self.subTest(field=field):
                self.assertNotIn(
                    field, self.wips.PATENT_FIELDS,
                    f"{field} 被搬進 patents 了——規格明列本次不搬（All 分類會讓統計失真）")

    def test_patent_note_has_only_one_home(self):
        """⚠ `文献备注` 0032 起已在 patents，不得出現第二份落點。

        ⚠ 本測試初版誤斷言它應在 `PATENT_FIELDS`——**錯的**：它是
        `ai:patent_note` 產出的欄位，**沒有對應的 WIPS 來源欄**，
        由 runner 直接寫 `core_layer.patents."文獻備註"`。
        `PATENT_FIELDS` 是「WIPS 來源 → patents 欄」的對照，它本來就不該在裡面。
        真正要守的是**不得回到 attributes**（0032 移除欄位後若還在 ATTRIBUTE_FIELDS，
        匯入會 INSERT 到不存在的欄而爆——那正是當初的 bug）。
        """
        self.assertNotIn("文献备注", self.wips.ATTRIBUTE_FIELDS,
                         "文獻備註回到 ATTRIBUTE_FIELDS——匯入會寫進 0032 已移除的欄位")


class ReaderContractTests(unittest.TestCase):
    """runtime 不得再為已搬出的欄位讀 `patent_attributes`。"""

    def _source(self, rel: str) -> str:
        return (PROJECT_ROOT / rel).read_text(encoding="utf-8")

    def test_patent_queries_reads_patents_not_attributes(self):
        """顯示欄位的 SQL 投影不得再出現 `patent_attributes`。

        ⚠ 本測試原本探 `_ATTRIBUTE_FIELDS.values()`——**不夠強**：那個 dict
        在 0046 連同 `_attribute_pick` 一起被刪了（清空後它只是無人使用的轉手層），
        探 dict 只會拿到 AttributeError。改驗**產出的 SQL**：無論欄位定義怎麼組織，
        只要投影裡沒有 attributes，契約就成立。
        """
        from backend.app.app_layer import patent_queries

        projection = patent_queries.display_projection()
        self.assertNotIn("patent_attributes", projection,
                         "顯示欄位投影仍讀 patent_attributes")

    def test_grant_year_no_longer_derived_from_attributes(self):
        """`grant_year` 由 patents 的授權公告日衍生，不再走 attributes 子查詢。"""
        from backend.app.app_layer import patent_queries

        projection = patent_queries.display_projection()
        self.assertIn("AS grant_year", projection, "grant_year 欄位不見了")
        # 取 grant_year 那一段，確認它讀的是 patents 別名（預設 p）而非 attributes
        segment = projection[:projection.index("AS grant_year")].rsplit(",\n", 1)[-1]
        self.assertIn('p."授權公告日"', segment,
                      "grant_year 沒有從 patents 的授權公告日衍生")
        self.assertNotIn("patent_attributes", segment)

    def test_target_source_pdf_url_from_patents(self):
        """案件比對的 PDF 連結改從 `patents` 取。"""
        src = self._source("backend/app/comparison/target_source.py")
        self.assertNotIn("core_layer.patent_attributes", src,
                         "target_source 仍 JOIN patent_attributes 取 PDF 連結")

    def test_refresh_report_base_reads_core_tables(self):
        """報表 derived 的六個欄位改從 patents／patent_people 取。"""
        src = self._source("backend/app/derived/refresh_report_patent_base.py")
        self.assertNotIn("core_layer.patent_attributes", src,
                         "refresh 仍 JOIN patent_attributes——已搬出的欄位應直接讀 core table")


class ImporterContractTests(unittest.TestCase):
    """匯入端必須把搬出的欄位寫進 core table，而不是繼續寫 attributes。

    ⚠ 這一組是本次最容易漏的地方：`normalize_record` 由 `PATENT_FIELDS` 驅動，
    改 mapping 就自動跟上；但 **upsert 的欄位清單是另一份手維護的表**
    （`_UPDATE_COLUMN_PARAMS`）。只改 mapping 而漏改它，症狀是
    「欄位存在、匯入不報錯、值永遠是 NULL」——不會有任何錯誤訊息。
    """

    def setUp(self):
        from backend.app.importers import wips_importer
        from backend.app.mappings import wips

        self.imp = wips_importer
        self.wips = wips

    def test_upsert_covers_every_patents_column(self):
        """🔴 `_UPDATE_COLUMN_PARAMS` 必須覆蓋 `PATENT_FIELDS` 的每一個目標欄。

        ⚠ 這兩份是**同一份知識的兩個落點**（哪些欄要寫進 patents）。
        `主附圖` 例外：bytea 由代表圖流程另存，不走文字 upsert。
        """
        covered = {c.strip('"') for c, _ in self.imp._UPDATE_COLUMN_PARAMS}
        expected = set(self.wips.PATENT_FIELDS.values()) - {self.imp.FIGURE_COLUMN}
        missing = expected - covered
        self.assertEqual(missing, set(),
                         f"這些欄在 PATENT_FIELDS 但 upsert 不會寫：{sorted(missing)}")

    def test_insert_and_update_share_one_column_list(self):
        """INSERT 與 UPDATE 不得各自維護欄位清單（本次前是兩份字面）。"""
        sql = self.imp._patent_insert_sql()
        for column, param in self.imp._UPDATE_COLUMN_PARAMS:
            with self.subTest(column=column):
                self.assertIn(column, sql)
                self.assertIn(f"%({param})s", sql)

    def test_param_names_are_psycopg_safe(self):
        """🔴 regression：具名參數名**不得含括號**。

        2026-08-06 實機重現：0046 初版把 14 個新欄的參數名直接用欄名，其中 6 個含
        括號（`摘要(原文)`／`詳細查看連結(登入)`／`文圖像文件(PDF)連結`／
        `WIPS同族各國家文獻數量(申請為準)`／`(F1)引用文獻數`／`(B1)引用文獻數`）。
        psycopg 解析 `%(name)s` 時**以第一個 `)` 為結尾**，於是
        `%(摘要(原文)_cmp)s` 被截成 `%(摘要(原文)`，整句 UPDATE 拋
        `ProgrammingError: only '%s', '%b', '%t' are allowed as placeholders`。

        ⚠ 這也解釋了既有欄位為何一律用英文別名（`claim_count`／`main_claim_original`…）
        ——那不是歷史包袱，是這條限制的產物。初版註解誤判為包袱，一併更正。

        ⚠ 為什麼上面那條 `test_insert_and_update_share_one_column_list` 沒擋住：
        它斷言 `"%(param)s" in sql`，是**字串比對**——字串確實在，但 psycopg 解析不了。
        字串契約驗不到真 SQL 能不能跑，這條才是。
        """
        for column, param in self.imp._UPDATE_COLUMN_PARAMS:
            with self.subTest(column=column):
                self.assertNotIn("(", param, f"{column} 的參數名 {param} 含括號")
                self.assertNotIn(")", param, f"{column} 的參數名 {param} 含括號")

    def test_every_param_resolves_to_a_value(self):
        """每個參數名都要能從 normalize_record 的 patent dict 取到值。

        ⚠ 參數名一旦不等於欄名，`patent_params` 就必須補上對應——漏補的症狀是
        該欄永遠寫入 NULL（`.get(param)` 回 None），而且**不會報錯**。
        """
        from backend.app.mappings import wips

        patent = {target: f"v-{target}" for target in wips.PATENT_FIELDS.values()}
        params = self.imp.build_patent_params(patent)
        for column, param in self.imp._UPDATE_COLUMN_PARAMS:
            expected_col = column.strip('"')
            if expected_col not in patent:
                continue        # 衍生欄（application_date 等）不由 PATENT_FIELDS 提供
            with self.subTest(column=column):
                self.assertEqual(params.get(param), patent[expected_col],
                                 f"參數 {param} 取不到 {expected_col} 的值")

    def test_people_stat_fields_are_inserted(self):
        """發明人數／申請人數要真的寫進 patent_people。"""
        for field in MOVE_TO_PEOPLE:
            with self.subTest(field=field):
                self.assertIn(field, self.imp.PEOPLE_FIELDS,
                              f"{field} 不在 PEOPLE_FIELDS——insert_people 不會寫它")


class MigrationContractTests(unittest.TestCase):
    """0046 的契約：加欄、回填、移除、downgrade 可回復。"""

    def _module(self, tag: str):
        import importlib.util

        path = (PROJECT_ROOT / "alembic" / "versions"
                / "0046_core_field_reclassification.py")
        spec = importlib.util.spec_from_file_location(f"mig0046_{tag}", path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_chains_after_current_head(self):
        """⚠ head 實查為 `0045`（`0044` 本日才補套用）——不得接錯。"""
        self.assertEqual(self._module("head").down_revision,
                         "0045_expanded_view_columns")

    def test_declares_every_moved_column(self):
        """migration 的欄位清單必須與 mapping 一致。

        ⚠ 兩層用**不同的名字**：mapping 的 key 是 WIPS 原始**簡體**欄名，
        DB 欄名是 `display_field_name()` 轉過的**繁體**。比對前必須經 mapping 轉譯
        ——這條測試因此同時守住「mapping 與 migration 不會各改各的」。
        """
        from backend.app.mappings import wips

        mod = self._module("cols")
        for field in MOVE_TO_PATENTS:
            with self.subTest(field=field):
                db_col = wips.PATENT_FIELDS[field]
                self.assertIn(db_col, mod.PATENT_MOVES,
                              f"{field} → {db_col} 沒列進 migration 的 patents 搬移清單")
        for field in MOVE_TO_PEOPLE:
            with self.subTest(field=field):
                db_col = wips.PEOPLE_FIELD_COLUMNS[field]
                self.assertIn(db_col, mod.PEOPLE_MOVES,
                              f"{field} → {db_col} 沒列進 migration 的 people 搬移清單")

    def test_backfill_uses_latest_nonblank(self):
        """🔴 回填取「每件專利的最新非空值」，空值不得覆蓋非空。

        ⚠ `patent_attributes` 是一 raw_record 一列，同一專利可能多列；
        取錯列會讓 canonical value 變成舊值或空值。
        """
        src = (PROJECT_ROOT / "alembic" / "versions"
               / "0046_core_field_reclassification.py").read_text(encoding="utf-8")
        self.assertIn("NULLIF(BTRIM", src, "回填沒有做空值判定")
        self.assertIn("raw_record_id DESC", src, "回填沒有取最新 raw_record")

    def test_paired_epc_columns_come_from_same_raw_record(self):
        """🔴 成對欄位（EPC 有效／無效國家）必須取自**同一** raw_record。

        分開各取「最新非空」會讓兩欄來自不同匯入批次，語意不一致。
        """
        src = (PROJECT_ROOT / "alembic" / "versions"
               / "0046_core_field_reclassification.py").read_text(encoding="utf-8")
        self.assertIn("PAIRED_GROUPS", src,
                      "沒有成對欄位的處理——EPC 兩欄可能取到不同 raw_record")

    def test_downgrade_restores_attributes(self):
        """downgrade 要把欄位加回 attributes 並從 core table 回填。"""
        mod = self._module("down")
        executed: list[str] = []

        class _Op:
            @staticmethod
            def execute(stmt):
                executed.append(" ".join(str(stmt).split()))

        mod.op = _Op
        mod.downgrade()
        joined = " ".join(executed)
        self.assertIn("patent_attributes", joined, "downgrade 沒有動到 attributes")
        self.assertIn("ADD COLUMN", joined, "downgrade 沒有把欄位加回 attributes")


if __name__ == "__main__":
    unittest.main()
