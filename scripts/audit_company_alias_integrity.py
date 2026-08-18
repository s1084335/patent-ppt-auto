"""公司別稱／集團的一致性稽核（對正式庫，唯讀）。

⚠ 這**不是**測試，刻意不放 `tests/`：`tests/conftest.py` 會把 DATABASE_URL
強制轉成本機測試庫（2026-07-27 封的破口——曾發生測試連上正式庫並留下資料）。
本腳本要看的是**正式資料現在的狀態**，屬驗收步驟，由人執行。

用途：
- `fix-company-alias-conflicts` 動工前後各跑一次，比對是否修對
- 日後懷疑歸戶有問題時的第一個檢查

判準說明見 openspec/changes/fix-company-alias-conflicts/design.md §1：
⚠ 一家公司有多個 WIPS 代碼是**常態**（創科集團就有四個），本腳本不檢查那個；
檢查的是「一個別稱對到多個代碼」——那會讓歸戶取決於查詢順序。

用法：
    uv run python scripts/audit_company_alias_integrity.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _connect():
    import psycopg
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
    from backend.app.db.connection import get_database_url

    return psycopg.connect(get_database_url(), connect_timeout=25)


CHECKS: list[tuple[str, str, str]] = [
    (
        "別稱不得被多個代碼認領",
        """SELECT alias_lookup_key || ' → ' ||
                  array_to_string(array_agg(DISTINCT "申請人代碼"), ', ') AS detail
           FROM derived_layer.company_aliases
           WHERE review_status = 'confirmed' AND alias_lookup_key IS NOT NULL
           GROUP BY alias_lookup_key
           HAVING count(DISTINCT "申請人代碼") > 1""",
        "歸戶會依查詢順序而定",
    ),
    (
        "集團成員的代碼必須存在於別稱表",
        """SELECT m.company_code AS detail
           FROM derived_layer.company_group_members m
           WHERE NOT EXISTS (
             SELECT 1 FROM derived_layer.company_aliases a
             WHERE a."申請人代碼" = m.company_code)""",
        "集團統計會少一家且不報錯",
    ),
    (
        "同代碼的中文名／正規化名必須一致",
        """SELECT "申請人代碼" || '：' ||
                  count(DISTINCT coalesce("公司中文名稱",'')) || ' 種中文名' AS detail
           FROM derived_layer.company_aliases
           WHERE review_status = 'confirmed'
           GROUP BY "申請人代碼"
           HAVING count(DISTINCT coalesce("公司中文名稱",'')) > 1""",
        "報表會依查到哪一列而顯示不同名稱",
    ),
]


def main() -> int:
    bad = 0
    with _connect() as conn, conn.cursor() as cur:
        for name, sql, why in CHECKS:
            cur.execute(sql)
            rows = [r[0] for r in cur.fetchall()]
            if rows:
                bad += 1
                print(f"🔴 {name}（{why}）")
                for r in rows:
                    print(f"     {r}")
            else:
                print(f"OK {name}")

        # 揭露用：不是違規，但值得看一眼
        cur.execute("""
            SELECT g.group_name, count(*) AS n
            FROM derived_layer.company_group_members m
            JOIN derived_layer.company_groups g ON g.group_id = m.group_id
            GROUP BY 1 HAVING count(*) > 1 ORDER BY 2 DESC""")
        multi = cur.fetchall()
        print("\n（揭露）持有多個代碼的集團——這是合法的 WIPS 常態：")
        for name, n in multi:
            print(f"     {name}：{n} 個代碼")

    print(f"\n違規項目 {bad}／{len(CHECKS)}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
