"""掃描 patent_people 申請人代表碼變體，自動補入唯一 company_aliases 對照表。

本模組是「名稱治理管線」（govern_company_names）的全量觸發點：匯入時由
wips_importer 對增量 pairs 即時觸發，本模組對 patent_people 全量重掃，
兩者共用同一套核心，不各自維護第二份偵測邏輯。

使用方式：
    python -m backend.app.derived.alias_variant_sweep

輸出：
- inserted：自動補入別稱數
- skipped_existing：已存在變體數
- manual_review：unknown/conflicting code 列表
- needs_zh_name：顯示名無 CJK 且未經 curation 裁決的待中文化代碼列表
- manual_review_html：交使用者確認的單頁 HTML（存 output/，含待中文化建議節）
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError as exc:
    raise RuntimeError("psycopg is required. Install psycopg[binary].") from exc

from backend.app.db.connection import get_connection_kwargs
from backend.app.derived.company_alias_importer import govern_company_names


VARIANTS_SOURCE_LABEL = "variant_sweep"


def collect_pairs(connect_kwargs: dict[str, Any] | None = None) -> list[tuple[str | None, str | None]]:
    """掃描 patent_people，收集 (申請人代表碼, 申請人) 與 (申請人代表碼, 標準化申請人) 組合。"""
    pairs: list[tuple[str | None, str | None]] = []
    kw = dict(connect_kwargs or get_connection_kwargs())
    kw.setdefault("options", "-c search_path=core_layer,public")
    with psycopg.connect(**kw) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT "申請人代表碼", "申請人"
            FROM patent_people
            WHERE "申請人代表碼" IS NOT NULL AND "申請人" IS NOT NULL AND "申請人" != ''
            """
        ).fetchall()
        for code, name in rows:
            pairs.append((code, name))

        std_rows = conn.execute(
            """
            SELECT DISTINCT "申請人代表碼", "標準化申請人"
            FROM patent_people
            WHERE "申請人代表碼" IS NOT NULL AND "標準化申請人" IS NOT NULL AND "標準化申請人" != ''
              AND "標準化申請人" != "申請人"
            """
        ).fetchall()
        for code, name in std_rows:
            pairs.append((code, name))

    return pairs


def write_manual_review_html(
    manual: list[dict[str, str]],
    output_dir: Path,
    needs_zh_name: list[dict[str, str]] | None = None,
) -> Path:
    """寫出 manual_review 單頁 HTML（含「待中文化建議」節）。"""
    needs = needs_zh_name or []
    now = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = output_dir / f"alias_sweep_manual_review_{now}.html"
    rows_html = "\n".join(
        f"<tr><td>{r['company_code']}</td><td>{r['alias_name']}</td>"
        f"<td>{r['reason']}</td></tr>"
        for r in manual
    )
    zh_rows_html = "\n".join(
        f"<tr><td>{r['company_code']}</td><td>{r['company_name']}</td></tr>"
        for r in needs
    )
    html = f"""<!doctype html>
<html lang="zh-Hant">
<head><meta charset="utf-8"><title>Alias Variant Sweep — Manual Review</title>
<style>
  body {{ font-family: sans-serif; padding: 24px; }}
  table {{ border-collapse: collapse; width: 100%; }}
  th, td {{ border: 1px solid #ccc; padding: 6px 10px; text-align: left; }}
  th {{ background: #f5f5f5; }}
  .unknown_code {{ background: #fff3cd; }}
  .conflicting_code {{ background: #f8d7da; }}
  h2 {{ margin-top: 32px; }}
</style></head>
<body>
<h1>Alias Variant Sweep — Manual Review</h1>
<p>以下 {len(manual)} 筆無法自動合併，需人工確認後再寫入 company_aliases。</p>
<table>
<thead><tr><th>申請人代碼</th><th>名稱變體</th><th>原因</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<h2>待中文化建議</h2>
<p>以下 {len(needs)} 個代碼的顯示名不含中文（CJK）且尚未經 curation 裁決；
請依 company-name-curation 流程查證市場慣用中文名，或裁決保留原文（裁決後不再浮現）。</p>
<table>
<thead><tr><th>申請人代碼</th><th>目前顯示名</th></tr></thead>
<tbody>
{zh_rows_html}
</tbody>
</table>
</body></html>"""
    path.write_text(html, encoding="utf-8")
    return path


def sweep_and_report(
    connect_kwargs: dict[str, Any] | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """執行一次完整 sweep（名稱治理管線全量觸發），回傳統計、manual_review 與 needs_zh_name。"""
    pairs = collect_pairs(connect_kwargs=connect_kwargs)
    result = govern_company_names(
        pairs,
        source_label=VARIANTS_SOURCE_LABEL,
        connect_kwargs=connect_kwargs,
    )

    manual = result.get("manual_review", [])
    needs_zh = result.get("needs_zh_name", [])
    if (manual or needs_zh) and output_dir:
        html_path = write_manual_review_html(manual, Path(output_dir), needs_zh_name=needs_zh)
        result["manual_review_html"] = str(html_path)

    result["total_pairs"] = len(pairs)
    result["timestamp"] = datetime.now().isoformat()
    return result


def main() -> None:
    connect_kwargs = get_connection_kwargs()
    output_dir = Path(os.getenv("OUTPUT_DIR", "output"))
    output_dir.mkdir(parents=True, exist_ok=True)
    result = sweep_and_report(
        connect_kwargs=connect_kwargs,
        output_dir=output_dir,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    if result.get("manual_review_html"):
        print(f"\nManual review HTML: {result['manual_review_html']}")


if __name__ == "__main__":
    main()
