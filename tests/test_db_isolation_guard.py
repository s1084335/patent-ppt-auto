"""測試環境資料庫隔離守門（2026-07-27）。

動因（實測）：`backend/app/clustering/runner.py` 於 **import 時**呼叫
`load_dotenv(專案根/.env, override=False)`，把 .env 內的正式庫（Supabase）
DATABASE_URL 灌進 os.environ。50 個 DB 測試檔的 setUpModule 只做
`os.environ.pop("DATABASE_URL", None)`，而 pop 發生在 import backend.app.main **之前**，
於是 app 實際連的是**正式庫**：

- 讀取類測試看似「查無資料」而失敗（workspace not found），掩蓋真正的回歸；
- 寫入類測試（例如 finalize 建 job）會**真的在正式庫留下資料**。

修法落在 conftest.py：整個 pytest session 開始時就把 DATABASE_URL 釘到本機測試庫，
`load_dotenv(override=False)` 便無法覆蓋。本檔守住這條線不被改回去。
"""
from __future__ import annotations

import os
import unittest


class DatabaseIsolationGuardTests(unittest.TestCase):
    """測試進程內絕不可指向正式庫。"""

    def test_database_url_is_not_production(self):
        """DATABASE_URL 不得指向 Supabase／正式主機。"""
        url = os.getenv("DATABASE_URL", "")
        for forbidden in ("supabase.com", "supabase.co"):
            self.assertNotIn(
                forbidden, url,
                f"測試進程的 DATABASE_URL 指向正式庫（{forbidden}），"
                "conftest.py 的隔離失效——測試會讀寫正式資料",
            )

    def test_importing_app_does_not_restore_production_url(self):
        """import backend.app.main（內含 load_dotenv）後仍不得變回正式庫。

        這正是原本的破口：dotenv 以 override=False 灌入，只要環境內已有值就蓋不掉，
        所以隔離必須在 import 之前就設好值（而非 pop 成空）。
        """
        import backend.app.main  # noqa: F401  （只為觸發 import 時的 load_dotenv）

        url = os.getenv("DATABASE_URL", "")
        self.assertNotIn("supabase", url, "import 後 DATABASE_URL 被 .env 灌回正式庫")


if __name__ == "__main__":
    unittest.main()
