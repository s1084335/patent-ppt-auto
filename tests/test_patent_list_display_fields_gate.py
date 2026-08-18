"""每一支專利清單都必須把顯示欄位補齊（同一份知識只能有一個定義處）。

## 這個 bug 出現過兩次

`patent_kind_display`／`legal_status_display` 不是 SQL 欄位，是**查完之後在 Python
推導**出來的。推導寫在 `list_patents` 與 `list_workspace_patents` 裡，
**漏了 `list_topic_patents`**——分類區點進某個主題後，「專利種類」「專利狀態」整欄空白。

⚠ 完全同型的事在同一支函式上已經發生過一次：`workspace_queries.py` 的註解寫著
「原本這裡完全沒有主題欄位……分通道欄當初只加在 `list_workspace_patents`」。
補一次漏一次的原因是**後處理散在各個 list 函式裡**，新增一支就要記得補。

既有的 `test_api_patents_display_fields.py` 驗了「總覽」與「workspace 清單」共用欄位，
唯獨沒驗 topic 清單——所以它綠著，bug 照樣上線。而且它需要本機 Postgres，
在沒有測試庫的機器上直接 skip。

## 本測試的判準

不驗「值對不對」（那要 DB），驗**結構**：凡是回傳專利清單的函式，
都必須走同一個補欄位的函式。新增第四支清單時，只要照命名慣例就會被抓到。
"""
from __future__ import annotations

import inspect
import unittest

from backend.app.app_layer import patent_queries, workspace_queries


#: 補顯示欄位的唯一入口。
FINISHER = "attach_display_fields"

#: 使用**共用顯示欄位組**（`display_projection()`）的清單——必須補推導欄位。
PATENT_LIST_FUNCTIONS = (
    (patent_queries, "list_patents"),
    (workspace_queries, "list_workspace_patents"),
    (workspace_queries, "list_topic_patents"),
)

#: 有自己窄欄位組、不吃共用顯示欄位的清單。⚠ 放進這裡要寫明理由——
#: 「忘了補」與「刻意不補」在程式碼上長得一模一樣，不寫下來下次就分不出來。
NON_SHARED_DISPLAY_LISTS = {
    ("backend.app.app_layer.patent_queries",
     "list_pending_tw_legal_status_patents"):
        "TW 狀態登錄面板：自己的窄欄位組，顯示原始 legal_status 供人工編輯，"
        "不是報表口徑的顯示字面",
}


class SingleFinisherTests(unittest.TestCase):
    def test_finisher_exists_in_one_place(self):
        """補欄位的邏輯只能有一個定義處。"""
        self.assertTrue(
            hasattr(patent_queries, FINISHER),
            f"缺少統一的顯示欄位補齊函式 {FINISHER}——"
            "推導散在各 list 函式裡，補一次漏一次")

    def test_finisher_derives_both_display_fields(self):
        finisher = getattr(patent_queries, FINISHER)
        rows = [{"legal_status": "審查中", "document_kind": "A", "patent_type": "P"}]
        finisher(rows)
        self.assertEqual(rows[0]["legal_status_display"], "審查中")
        self.assertEqual(rows[0]["patent_kind_display"], "發明")

    def test_finisher_is_safe_on_empty_source(self):
        """來源全空時仍要有 key（欄位一律呈現，前端不需改）。"""
        rows = [{}]
        getattr(patent_queries, FINISHER)(rows)
        for key in ("legal_status_display", "patent_kind_display"):
            self.assertIn(key, rows[0], f"來源無值時仍須保留 {key}")


class EveryListUsesTheFinisherTests(unittest.TestCase):
    def test_every_patent_list_function_attaches_display_fields(self):
        """🔴 核心：三支清單都要補欄位，漏一支就是整欄空白。"""
        for module, name in PATENT_LIST_FUNCTIONS:
            with self.subTest(function=f"{module.__name__}.{name}"):
                src = inspect.getsource(getattr(module, name))
                self.assertIn(
                    FINISHER, src,
                    f"{name} 沒有補顯示欄位——該清單的「專利種類／專利狀態」會整欄空白")

    def test_every_patent_list_function_is_classified(self):
        """⚠ 新增一支清單卻沒分類 → 直接紅。

        沒有這條，本測試只能守住今天知道的三支；明天多一支照樣漏。
        分類只有兩種：吃共用顯示欄位（要補），或有自己的窄欄位組（要寫明理由）。
        """
        registered = ({(m.__name__, n) for m, n in PATENT_LIST_FUNCTIONS}
                      | set(NON_SHARED_DISPLAY_LISTS))
        found = set()
        for module in (patent_queries, workspace_queries):
            for name, obj in vars(module).items():
                if not (inspect.isfunction(obj) and name.startswith("list_")):
                    continue
                if not name.endswith("patents"):
                    continue
                found.add((module.__name__, name))
        self.assertEqual(
            found, registered,
            f"有未分類的專利清單函式：{sorted(found - registered)}"
            "（要嘛加進 PATENT_LIST_FUNCTIONS 並呼叫 attach_display_fields，"
            "要嘛加進 NON_SHARED_DISPLAY_LISTS 並寫明理由）；"
            f"或登記了不存在的：{sorted(registered - found)}")


class NoPerFunctionDerivationTests(unittest.TestCase):
    """拆掉散落的推導，避免又出現「這支有、那支沒有」。"""

    def test_derivation_not_inlined_in_list_functions(self):
        for module, name in PATENT_LIST_FUNCTIONS:
            with self.subTest(function=name):
                src = inspect.getsource(getattr(module, name))
                self.assertNotIn(
                    'it["legal_status_display"]', src,
                    f"{name} 自己推導了一次——推導只能有一個定義處")


if __name__ == "__main__":
    unittest.main()
