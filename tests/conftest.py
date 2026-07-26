"""pytest session 級的資料庫隔離（2026-07-27）。

問題：`backend/app/clustering/runner.py` 等模組在 **import 時** 執行
`load_dotenv(專案根/.env, override=False)`，會把 .env 內的正式庫（Supabase）
DATABASE_URL 灌進 os.environ。各 DB 測試檔的 setUpModule 只做
`os.environ.pop("DATABASE_URL", None)`，但 pop 在 import 之前，pop 完再 import
就被 dotenv 補回去，於是測試實際連上**正式庫**——讀取類測試假性失敗，
寫入類測試會在正式庫留資料。

修法：在收集任何測試前就把 DATABASE_URL **設成**本機測試庫。
dotenv 的 override=False 遇到已存在的值不會覆蓋，破口即封閉。
各測試檔的 setUpModule 若要指定自己的拋棄式 DB，照樣可以覆寫這個值。

覆寫方式：跑測試前自行 export DATABASE_URL 指向其他測試庫即可（不含 supabase 才會被採用）。
"""
from __future__ import annotations

import os


def _local_test_database_url() -> str:
    """組出本機測試庫連線字串（與各測試檔 _kw() 預設同一組參數）。"""
    host = os.getenv("PGHOST", "127.0.0.1")
    port = os.getenv("PGPORT", "5433")
    user = os.getenv("PGUSER", "postgres")
    password = os.getenv("PGPASSWORD")
    dbname = os.getenv("PGDATABASE", "patent_ppt_test")
    auth = f"{user}:{password}" if password else user
    return f"postgresql://{auth}@{host}:{port}/{dbname}"


# 在 conftest 載入（早於所有測試模組 import）時就釘住，避免 load_dotenv 灌回正式庫。
# 已由外部指定且非正式庫者尊重原值；指向 supabase 者一律改寫。
_current = os.environ.get("DATABASE_URL", "")
if not _current or "supabase" in _current:
    os.environ["DATABASE_URL"] = _local_test_database_url()
