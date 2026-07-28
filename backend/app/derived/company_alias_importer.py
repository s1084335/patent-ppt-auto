from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path
from typing import Any

from openpyxl import load_workbook

from backend.app.transforms.text import clean_text

REQUIRED_COLUMNS = ("申請人代碼", "公司名稱", "別稱")

# CJK 範圍：只服務 `canonical` 舊單欄輸入的相容判斷（見 resolve_group_names）。
# 新流程由使用者在前端分兩格填中文／英文，不靠字元類別猜。
_CJK_RE = re.compile(r"[一-鿿]")


def normalize_lookup(value: str | None) -> str | None:
    text = clean_text(value)
    if not text:
        return None
    return " ".join(text.casefold().split())


def load_alias_rows(path: Path, with_dropped: bool = False):
    """載入來源檔並正規化；with_dropped=True 時回傳 (rows, dropped)。"""
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return load_xlsx_alias_rows(path, with_dropped=with_dropped)
    if suffix == ".csv":
        return load_csv_alias_rows(path, with_dropped=with_dropped)
    raise ValueError(f"Unsupported company alias file format: {path.suffix}")


def load_csv_alias_rows(path: Path, with_dropped: bool = False):
    for encoding in ("utf-8-sig", "cp950", "big5", "gb18030"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return normalize_alias_rows(csv.DictReader(handle), with_dropped=with_dropped)
        except UnicodeError:
            continue
    raise UnicodeError(f"Cannot decode company alias CSV: {path}")


def load_xlsx_alias_rows(path: Path, with_dropped: bool = False):
    workbook = load_workbook(path, read_only=True, data_only=True)
    worksheet = workbook.worksheets[0]
    rows = worksheet.iter_rows(values_only=True)
    try:
        headers = [str(value).strip() if value is not None else "" for value in next(rows)]
    except StopIteration:
        return []
    records = []
    for row in rows:
        record = {header: row[index] if index < len(row) else None for index, header in enumerate(headers) if header}
        records.append(record)
    return normalize_alias_rows(records, with_dropped=with_dropped)


def normalize_alias_rows(records: Any, with_dropped: bool = False):
    """讀來源檔（中文表頭）→ 輸出 SQL 具名參數用的英文 key，並依 DB 唯一索引去重。

    去重 key 必須與 DB 唯一索引 ux_company_aliases_code_lookup_confirmed 同一把
    ——即 (申請人代碼, normalize_lookup("別稱"))（後者為 lower + 壓空白）。
    2026-07-23 定案「代碼是公司收斂的依據」後唯一性下放到代碼層級：
    同一別稱字面可分屬不同代碼，只有「同代碼同別稱」才算重複。
    舊版用 (代碼, 公司名稱, 別稱) 三元組，抓不到只差大小寫／空白的變體，
    整批 executemany 會撞 UniqueViolation 導致全交易 rollback。

    同 key 多筆時保留**來源檔第一筆**：來源順序穩定可重現，不需臆測哪個字面
    「較正規」（大小寫偏好因公司而異，硬選反而是另一種寫死）。被丟棄的列
    一律回報，不靜默丟棄。
    """
    rows = []
    dropped = []
    seen: dict[tuple[str, str], dict[str, str]] = {}
    for record in records:
        company_code = clean_text(record.get("申請人代碼"))
        company_name = clean_text(record.get("公司名稱"))
        alias_name = clean_text(record.get("別稱"))
        if not company_name or not alias_name:
            continue
        key = (company_code or "", normalize_lookup(alias_name))
        if key in seen:
            kept = seen[key]
            dropped.append(
                {
                    "lookup_key": key[1],
                    "company_code": company_code,
                    "company_name": company_name,
                    "alias_name": alias_name,
                    "kept_company_code": kept["company_code"],
                    "kept_alias_name": kept["alias_name"],
                }
            )
            continue
        row = {
            "company_code": company_code,
            "company_name": company_name,
            "alias_name": alias_name,
        }
        seen[key] = row
        rows.append(row)
    if with_dropped:
        return rows, dropped
    return rows


def import_company_aliases(
    path: Path,
    dry_run: bool = False,
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """匯入對照表；批內先依 DB 唯一索引同一把 key 去重，重複列記入 summary 警告。"""
    rows, dropped = load_alias_rows(path, with_dropped=True)
    summary = {
        "file": str(path),
        "file_format": path.suffix.lstrip(".").lower(),
        "rows": len(rows),
        "duplicate_dropped": len(dropped),
        "duplicate_warnings": dropped,
        "status": "dry_run" if dry_run else "pending",
    }
    if dry_run:
        return summary

    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO derived_layer.company_aliases (
                    "申請人代碼", "公司名稱", "別稱", source_file
                )
                VALUES (
                    %(company_code)s, %(company_name)s, %(alias_name)s, %(source_file)s
                )
                -- 衝突目標＝ux_company_aliases_code_lookup_confirmed（同代碼同別稱）。
                -- 重跑同一檔時更新公司名與來源，維持 idempotent。
                ON CONFLICT ("申請人代碼", alias_lookup_key) WHERE review_status = 'confirmed' DO UPDATE
                SET
                    "公司名稱" = EXCLUDED."公司名稱",
                    "別稱" = EXCLUDED."別稱",
                    source_file = EXCLUDED.source_file,
                    imported_at = now()
                """,
                [{**row, "source_file": str(path)} for row in rows],
            )
        conn.commit()
    summary["status"] = "imported"
    return summary


# curation 裁決標記：company_aliases.source_file 帶此前綴＝該代碼已完成顯示名裁決
# （含「保留原文」的裁決），名稱治理管線不再把它列入待中文化建議。
CURATION_SOURCE_PREFIX = "display_name_curation"


def govern_company_names(
    pairs: list[tuple[str | None, str | None]],
    source_label: str = "variant_intake",
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """名稱治理管線核心：變體註冊＋待中文化偵測，匯入與 sweep 共用同一套邏輯。

    輸入 (code, name) 集合（2026-07-21 定案「單一名稱治理管線」）：
    - code 在 company_aliases 對到唯一「公司名稱」→ 新變體補一列別稱，公司名稱沿用既有值；
      既有別稱（normalize 後相同）跳過。
    - code 不在表中（unknown_code）或對到多個公司名稱（conflicting_code）→ 進 manual_review，
      不自行合併、不寫表。
    - needs_zh_name：本批涉及且已在對照表的 code 中，canonical 顯示名不含 CJK 字元
      （尚無市場慣用中文名）且未經 curation 裁決者——該代碼不存在
      source_file LIKE 'display_name_curation%' 的列；「保留原文」也是正式裁決，不重複浮現。
    - 只寫 derived_layer.company_aliases；不碰 raw/core 原始專利值。
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    inserted = 0
    skipped = 0
    manual: list[dict[str, str]] = []
    needs_zh: list[dict[str, str]] = []
    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        rows = conn.execute(
            'SELECT "申請人代碼", "公司名稱", "別稱" FROM derived_layer.company_aliases '
            'WHERE "申請人代碼" IS NOT NULL'
        ).fetchall()
        # code → 既有公司名稱集合／normalize 後別稱集合
        names_by_code: dict[str, set[str]] = {}
        aliases_by_code: dict[str, set[str]] = {}
        for code, name, alias in rows:
            names_by_code.setdefault(code, set()).add(name)
            norm = normalize_lookup(alias)
            if norm:
                aliases_by_code.setdefault(code, set()).add(norm)

        batch_codes: set[str] = set()
        for raw_code, raw_variant in pairs:
            code = clean_text(raw_code)
            variant = clean_text(raw_variant)
            if not code or not variant:
                continue
            batch_codes.add(code)
            names = names_by_code.get(code)
            if names is None:
                manual.append({"company_code": code, "alias_name": variant, "reason": "unknown_code"})
                continue
            if len(names) > 1:
                manual.append({"company_code": code, "alias_name": variant, "reason": "conflicting_code"})
                continue
            norm_variant = normalize_lookup(variant)
            if norm_variant in aliases_by_code.get(code, set()):
                skipped += 1
                continue
            canonical_name = next(iter(names))  # 唯一既有正規化公司名稱，直接沿用
            conn.execute(
                # ⚠ 衝突目標改為 partial unique index（2026-07-28，0040 拆四欄）：
                # 舊三元組 UNIQUE (代碼, 公司名稱, 別稱) 已隨拆欄移除，這裡是全 repo
                # 唯一依賴它的寫入路徑。改用與 import_company_aliases／
                # apply_confirmed_display_names 同一把 key，唯一性語意不變。
                'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司名稱", "別稱", source_file) '
                "VALUES (%s, %s, %s, %s) "
                'ON CONFLICT ("申請人代碼", alias_lookup_key) '
                "WHERE review_status = 'confirmed' DO NOTHING",
                (code, canonical_name, variant, source_label),
            )
            aliases_by_code.setdefault(code, set()).add(norm_variant)
            inserted += 1

        # 待中文化偵測：只看本批涉及的 code；canonical 無 CJK（[一-鿿]）且該代碼
        # 沒有任何 curation 裁決列（source_file LIKE 'display_name_curation%'）才浮現。
        if batch_codes:
            zh_rows = conn.execute(
                'SELECT DISTINCT ca."申請人代碼", ca."公司名稱" '
                "FROM derived_layer.company_aliases ca "
                'WHERE ca."申請人代碼" = ANY(%s) '
                "  AND ca.\"公司名稱\" !~ '[一-鿿]' "
                "  AND NOT EXISTS ("
                "      SELECT 1 FROM derived_layer.company_aliases d "
                '      WHERE d."申請人代碼" = ca."申請人代碼" AND d.source_file LIKE %s'
                "  ) "
                "ORDER BY 1, 2",
                (sorted(batch_codes), f"{CURATION_SOURCE_PREFIX}%"),
            ).fetchall()
            needs_zh = [{"company_code": c, "company_name": n} for c, n in zh_rows]
        conn.commit()

    return {
        "inserted": inserted,
        "skipped_existing": skipped,
        "manual_review": manual,
        "needs_zh_name": needs_zh,
    }


def register_known_code_variants(
    pairs: list[tuple[str | None, str | None]],
    source_label: str = "variant_intake",
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """已知 WIPS code 的新名稱變體補入唯一對照表（薄包裝，委派名稱治理管線）。

    保留原簽章供 wips_importer 匯入時呼叫（importers 不需改）；實際邏輯統一在
    govern_company_names，匯入摘要因此自動帶出 needs_zh_name（新公司進庫即浮現待中文化）。
    """
    return govern_company_names(pairs, source_label=source_label, connect_kwargs=connect_kwargs)


def resolve_group_names(spec: dict[str, Any]) -> tuple[str | None, str | None]:
    """從一組 mapping spec 取出 (中文正式名, 英文正式名)。

    2026-07-28 四欄拆分後 mapping 的名稱欄有兩個鍵：
    - `zh_name`         → `公司中文名稱`（中文正式名）
    - `normalized_name` → `正規化名稱`（英文正式名）

    ⚠ 相容舊鍵 `canonical`：既有呼叫端（中文名確認端點 confirm_drafts）只給一個
    顯示名，語意上「有 CJK 就是中文名、否則是英文正式名」。這裡的字元類別判斷
    **只用於相容舊單欄輸入**，不是新流程的判斷依據——新流程由使用者在前端分兩格
    填，不靠猜（使用者第③點否決的正是「自動判斷含 CJK 就是中文名」的資料遷移）。
    兩欄都可空（使用者第②點），全空時回 (None, None)，由呼叫端決定要不要寫。
    """
    zh = clean_text(spec.get("zh_name"))
    en = clean_text(spec.get("normalized_name"))
    if zh or en:
        return zh, en
    legacy = clean_text(spec.get("canonical"))
    if not legacy:
        return None, None
    return (legacy, None) if _CJK_RE.search(legacy) else (None, legacy)


def apply_confirmed_display_names(
    mapping: dict[str, dict[str, Any]],
    source_label: str,
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """套用使用者已裁決的公司顯示名（curation 落地機制；載體＝DB 本身，不經 CSV）。

    mapping = {申請人代碼: {"zh_name": 中文正式名, "normalized_name": 英文正式名,
                            "aliases": [變體, ...]}}
    （2026-07-28 四欄拆分；舊形狀 `{"canonical": 顯示名, ...}` 仍相容，
      見 resolve_group_names）

    規則（2026-07-21 公司顯示名原則定案；2026-07-23 隨代碼收斂調整；2026-07-28 拆四欄）：
    - **兩個正式名自身也納入別稱**，確保使用者填的中文名／英文名字面在專利表可精確
      命中（原本只納入單一 canonical；拆欄後兩個都要，否則另一個字面命不中）。
    - 批內按 (代碼, normalize_lookup(別稱)) 去重，與 DB 唯一索引
      ux_company_aliases_code_lookup_confirmed 同一把 key。不同代碼可有同一別稱
      字面，故去重**不再跨 code**——跨 code 去重會誤丟別家公司的合法別稱。
    - (代碼, lookup key) 已存在 → UPDATE 該列 re-canonicalize：改掛新的中文／英文
      正式名與 source_file，review_status 一律轉 'confirmed'；不插新列，不撞唯一索引。
      查詢必須同時比對代碼，否則在「一別稱多公司」下會取到別家公司的列。
    - 不存在 → INSERT 一列 confirmed（source_type='manual'＝人工裁決）。
    - 兩個正式名皆空的組直接略過（使用者第②點：可空、不擋、不報錯；但沒有任何名字
      也就沒有東西可寫）。
    - 只寫 derived_layer.company_aliases；原始專利表不碰。
    回傳 {"inserted", "updated", "dedup_dropped"}。

    ⚠ 這是 company_aliases 唯一的 confirmed 寫入點。去重、re-canonicalize、
    review_status 轉換的規則只有這一份，其他模組一律委派，不得自寫 SQL。
    """
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for database import. Install psycopg[binary].") from exc

    from backend.app.db.connection import get_connection_kwargs

    # 批內去重：唯一索引已含代碼，去重 key 同步改為 (code, lookup key)，
    # 不同代碼的同一別稱字面各自保留，不再互相排擠。
    entries: list[tuple[str, str | None, str | None, str]] = []  # (code, 中文名, 英文名, 別稱)
    seen_keys: set[tuple[str, str]] = set()
    dedup_dropped = 0
    for raw_code, spec in mapping.items():
        code = clean_text(raw_code)
        zh_name, en_name = resolve_group_names(spec)
        if not code or not (zh_name or en_name):
            continue
        # 兩個正式名都納入別稱（各自的字面都要能命中），再接使用者填的變體。
        for raw_alias in [zh_name, en_name, *spec.get("aliases", [])]:
            alias = clean_text(raw_alias)
            if not alias:
                continue
            key = (code, normalize_lookup(alias))
            if key in seen_keys:
                dedup_dropped += 1
                continue
            seen_keys.add(key)
            entries.append((code, zh_name, en_name, alias))

    inserted = 0
    updated = 0
    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        for code, zh_name, en_name, alias in entries:
            # 以 DB 端與 generated 欄完全相同的正規化運算式找既有列。
            # 必須同時比對「申請人代碼」：唯一索引已是 (代碼, lookup key)，
            # 只用別稱會在「一別稱多公司」時取到別家公司的列並把它改掛到本代碼。
            # 多列同 key（review_required 重複）時優先更新 confirmed 列，其餘不動。
            row = conn.execute(
                "SELECT id FROM derived_layer.company_aliases "
                r"WHERE alias_lookup_key = lower(regexp_replace(btrim(%s), '\s+', ' ', 'g')) "
                'AND "申請人代碼" IS NOT DISTINCT FROM %s '
                "ORDER BY (review_status = 'confirmed') DESC, id LIMIT 1",
                (alias, code),
            ).fetchone()
            if row:
                # 舊 `公司名稱` 欄同步寫入（中文優先）：0040 之後它只是相容欄，
                # 但既有查詢／匯出仍讀得到，留空會讓那些路徑突然變空白。
                conn.execute(
                    'UPDATE derived_layer.company_aliases SET "公司中文名稱" = %s, '
                    '"正規化名稱" = %s, "公司名稱" = %s, '
                    "source_file = %s, review_status = 'confirmed', source_type = 'manual', "
                    "updated_at = now() WHERE id = %s",
                    (zh_name, en_name, zh_name or en_name, source_label, row[0]),
                )
                updated += 1
            else:
                conn.execute(
                    'INSERT INTO derived_layer.company_aliases ("申請人代碼", "公司中文名稱", '
                    '"正規化名稱", "公司名稱", "別稱", source_file, source_type, review_status) '
                    "VALUES (%s, %s, %s, %s, %s, %s, 'manual', 'confirmed')",
                    (code, zh_name, en_name, zh_name or en_name, alias, source_label),
                )
                inserted += 1
        conn.commit()

    return {"inserted": inserted, "updated": updated, "dedup_dropped": dedup_dropped}


# AI 中文名草稿清單（三態的「草稿待確認」）：只列 ai_suggested 列。
# 一併帶出：
#   - draft_name＝AI 產的中文名草稿（2026-07-28 起讀 `公司中文名稱`；keep_original
#     裁決時該欄為 NULL，前端據 verdict 顯示「查無，保留原文」）
#   - source_name＝AI 的輸入英文名（`正規化名稱`），供對照
#   - verdict＝translated／keep_original，供前端區分「有中文名」與「查無保留原文」
#   - original_name＝收斂前的原始字面（該代碼的別稱），確認中文名時的對照依據
# ⚠ 不帶「該代碼現行顯示名」：它與專利表「申請人」欄同源（皆走 code_alias_names →
# COALESCE 第一順位），而確認介面就掛在瀏覽專利頁、表格本身即顯示該欄，重複無益。
# 以代碼為單位，一代碼至多一草稿列（write_drafts 先刪後插保證）。
_LIST_DRAFTS_SQL = """
    SELECT d."申請人代碼" AS code,
           d."公司中文名稱" AS draft_name,
           d."正規化名稱" AS source_name,
           d.wips_metadata_json->>'zh_name_verdict' AS verdict,
           -- ⚠ 表沒有 created_at（實機 HTTP 500：UndefinedColumn）。時間欄是
           -- imported_at（匯入）與 updated_at（更新）；草稿為「一代碼至多一列、
           -- 重跑 UPDATE 收斂」，故顯示「何時產的」取 updated_at。
           d.updated_at AS created_at,
           (
               SELECT mode() WITHIN GROUP (ORDER BY c."別稱")
               FROM derived_layer.company_aliases c
               WHERE c."申請人代碼" = d."申請人代碼"
                 AND c.review_status = 'confirmed'
                 AND NULLIF(BTRIM(c."別稱"), '') IS NOT NULL
           ) AS original_name
    FROM derived_layer.company_aliases d
    WHERE d.review_status = 'ai_suggested'
      AND NULLIF(BTRIM(d."申請人代碼"), '') IS NOT NULL
    ORDER BY d."申請人代碼"
    LIMIT %(limit)s OFFSET %(offset)s
"""

# 總筆數：分頁時前端要知道還有多少沒看（與清單同一 WHERE，不另立條件）。
_COUNT_DRAFTS_SQL = """
    SELECT count(*) AS total
    FROM derived_layer.company_aliases d
    WHERE d.review_status = 'ai_suggested'
      AND NULLIF(BTRIM(d."申請人代碼"), '') IS NOT NULL
"""


def list_zh_name_drafts(
    *,
    limit: int = 100,
    offset: int = 0,
    connect_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """列出待確認的 AI 中文名草稿（三態的「草稿待確認」）。

    回 {"items": [{code, draft_name, verdict, created_at, original_name}, ...], "total": N}。
    verdict＝translated（AI 找到市場慣用中文名）／keep_original（查無，保留英文原文）。
    original_name＝該代碼收斂前的原始字面，供使用者判斷這個中文名對不對。

    分頁（規格 B）：代碼數量可觀時不一次全吐；total 走 count(*) 而非 len(items)，
    否則第二頁之後前端算不出還剩幾筆。清單與計數同一 WHERE 條件，不會對不上。
    """
    import psycopg
    from psycopg.rows import dict_row

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_LIST_DRAFTS_SQL, {"limit": limit, "offset": offset})
            items = cur.fetchall()
            cur.execute(_COUNT_DRAFTS_SQL)
            total = int(cur.fetchone()["total"])
    return {"items": items, "total": total}


def get_draft_names(
    code: str, connect_kwargs: dict[str, Any] | None = None
) -> tuple[str | None, str | None]:
    """取某代碼草稿列的 (中文名, 英文正式名)。

    前端只送 code＋action，草稿名由後端自己查——避免前端竄改或送到過期的草稿名。

    ⚠ 2026-07-28 拆四欄後回**兩個值**：`keep_original` 草稿的中文欄是 NULL，
    此時仍要能確認（英文正式名照樣寫進去、顯示退英文），故不能像舊版那樣
    「查不到名字就 422」。
    """
    import psycopg

    from backend.app.db.connection import get_connection_kwargs

    with psycopg.connect(**(connect_kwargs or get_connection_kwargs())) as conn:
        row = conn.execute(
            'SELECT "公司中文名稱", "正規化名稱" FROM derived_layer.company_aliases '
            "WHERE \"申請人代碼\" = %s AND review_status = 'ai_suggested' LIMIT 1",
            (code,),
        ).fetchone()
    if not row:
        return None, None
    return clean_text(row[0]), clean_text(row[1])


def main() -> None:
    parser = argparse.ArgumentParser(description="Import company alias table into derived_layer.company_aliases.")
    parser.add_argument("path", type=Path, help="Path to company alias CSV/XLSX.")
    parser.add_argument("--dry-run", action="store_true", help="Read and normalize without writing to database.")
    args = parser.parse_args()
    summary = import_company_aliases(args.path, dry_run=args.dry_run)
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
