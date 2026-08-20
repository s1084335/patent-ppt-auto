"""唯讀補查代表專利的 title／abstract／請求項，供競爭者構型頁拉到請求項顆粒度。

⚠ **這不是預設步驟**。報表判讀對統計、趨勢、定位頁的顆粒度都夠用；只有競爭者
   構型頁會不夠（報表寫成同一個構型名的兩件專利，獨立項可能是兩套完全不同的機構）。
   觸發條件與四條硬規則見 SKILL.md「何時該去資料庫補查」——特別是
   **只補敘述不補統計**：件數／家族數／授權數一律以報表為準。

唯讀怎麼保證：走專案既有的 `report_research.query_database`，它帶單句 SELECT／WITH
的語法白名單與 `SET TRANSACTION READ ONLY`（唯讀綁在交易上，因為 Supabase pooler
會忽略連線字串上的 startup options）。**不要自己接 psycopg，那會繞過這層保護。**

用法：
    python fetch_claims.py <work_dir>                    # 讀 plan.json 的 claim_lookup
    python fetch_claims.py <work_dir> --players 曾晴,祺驊   # 只取部分主體
    python fetch_claims.py <work_dir> --ids 93,94,96      # 直接指定 ID
輸出：<work_dir>/claims.json（每筆含來源文件號，撰稿時要標進簡報）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# 專案根目錄：query_database 與 DB 設定都在這裡（可用 PATENT_REPO 覆寫）
REPO = Path(os.environ.get("PATENT_REPO", r"D:\力山\專案\專利_ppt自動"))


def _load_env(repo: Path) -> None:
    """把 repo 的 .env 讀進 os.environ。

    ⚠ 這一步不能省：`get_database_url()` 取不到 `DATABASE_URL` 會退去拼 `PG*`，
    而密碼含 `@` 時拼出來的 DSN 會被解析成主機名，炸在
    `failed to resolve host '...@localhost'`——錯誤訊息完全指不到真正的原因。
    """
    env = repo / ".env"
    if not env.is_file():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


SQL = """
SELECT p.id,
       p.country_code,
       COALESCE(NULLIF(BTRIM(p."授權公告號"), ''),
                NULLIF(BTRIM(p."未審查的公開號(轉換後)"), ''),
                NULLIF(BTRIM(p."申請號(轉換後)"), '')) AS doc_no,
       p.application_year,
       p.patent_type,
       p.legal_status,
       rpb.applicant_display_name,
       p.title,
       p.abstract,
       p."主權項" AS main_claim,
       p."獨立項[KR,JP,US,CN,EP,IN]" AS indep_claims
FROM core_layer.patents p
LEFT JOIN derived_layer.report_patent_base rpb ON rpb.patent_id = p.id
WHERE p.id IN ({ids})
ORDER BY p.id
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("work_dir")
    ap.add_argument("--players", help="逗號分隔的主體名，預設全取")
    ap.add_argument("--ids", help="逗號分隔的 patent id，指定後忽略 plan.json")
    ap.add_argument("--limit", type=int, default=200)
    a = ap.parse_args()

    work = Path(a.work_dir)
    if a.ids:
        ids = [int(x) for x in a.ids.replace("、", ",").split(",") if x.strip()]
        wanted = None
    else:
        plan = json.loads((work / "plan.json").read_text(encoding="utf-8"))
        lookup = plan.get("claim_lookup") or {}
        if not lookup.get("available"):
            print("✗ plan.json 沒有 claim_lookup（報表沒有帶 patent_ids 的表）")
            return 1
        wanted = ([s.strip() for s in a.players.split(",")] if a.players else None)
        ids = []
        for pl in lookup["players"]:
            if wanted and pl["name"] not in wanted:
                continue
            ids += pl["patent_ids"]
    ids = sorted(set(ids))
    if not ids:
        print("✗ 沒有要查的 patent id（--players 是不是打錯名字？）")
        return 1
    if len(ids) > a.limit:
        print(f"✗ {len(ids)} 筆超過 --limit {a.limit}；補查是為了幾頁的敘述，"
              "不是把整個庫拉下來")
        return 1

    _load_env(REPO)
    sys.path.insert(0, str(REPO))
    from backend.app.mcp_server.report_research import query_database  # noqa: E402

    r = query_database(SQL.format(ids=",".join(str(i) for i in ids)), limit=a.limit)
    recs = [dict(zip(r["columns"], row)) for row in r["rows"]]
    out = work / "claims.json"
    out.write_text(json.dumps({
        "evidence_ref": r["evidence_ref"],
        "requested_ids": ids,
        "returned": len(recs),
        "note": ("只補敘述不補統計：件數／家族數／授權數一律以報表為準。"
                 "簡報上每個新名詞都要標 doc_no，並在該頁揭露來源。"),
        "patents": recs,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    missing = set(ids) - {x["id"] for x in recs}
    print(f"取回 {len(recs)}/{len(ids)} 筆　evidence_ref={r['evidence_ref']}")
    if missing:
        print(f"⚠ 查無資料的 id：{sorted(missing)}")
    for x in recs:
        print(f'  {x["id"]:>4} {x["country_code"]:<3} {str(x["doc_no"]):<16} '
              f'{x["application_year"]} {str(x["applicant_display_name"])[:12]:<12}'
              f'｜請求項 {len(x["main_claim"] or "")} 字'
              f'｜獨立項 {len(x["indep_claims"] or "")} 字')
    print(f"\n已寫出 {out}")
    print("⚠ 設計案通常沒有請求項（字數 0），那是正常的，不要當成查詢失敗。")
    # ⚠ 取回不等於讀過。實際踩過：取 15 件、只讀 7 件，卻寫下涵蓋三代的通則，
    #   補讀後發現最早那一代的請求項根本沒有那個要件（見 pitfalls #40）。
    with_claims = [x["id"] for x in recs if (x["main_claim"] or "").strip()]
    print(f"⚠ 有請求項的 {len(with_claims)} 件：{with_claims}")
    print("   要寫「三代都…」「各家一律…」這種跨案件通則，上面每一件都必須實際讀過；"
          "只讀部分就只能講那部分。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
