"""raw layer 精簡：移除 source_files，來源 metadata 併入 raw_records

Revision ID: 0019_raw_layer_simplify
Revises: 0018_compose_created_at_comment
Create Date: 2026-07-20

移除 raw_layer.source_files，把必要來源 metadata（source_system、來源檔 hash、匯入時間）
併進 raw_layer.raw_records；patent_sources／patent_attributes 不再存 source_file_id，改
透過 raw_record_id 追到 raw_records 再取 source_system/hash。business 追蹤不靠 id（raw_records.id
只當 DB 技術 PK/FK）。file_name／file_path／record_count 不保留，hash/name/path 三者只留
source_file_hash。

upgrade 順序：先加欄→從 source_files 回填→驗證無 NULL→設 NOT NULL/DEFAULT→重建相依 view→
移除三個指向 source_files 的 FK→移除舊 unique/index→drop source_file_id 欄→建新 unique/index→
重建 patent_source_summary（改走 raw_record_id）→drop source_files。downgrade 能重建可用的
source_files 關聯；無原檔名/路徑時以 hash 衍生占位值。不動 patents/patent_people 原始欄位。
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0019_raw_layer_simplify"
down_revision = "0018_compose_created_at_comment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """來源 metadata 併入 raw_records 並移除 source_files 與 source_file_id 關聯。"""
    # 1. raw_records 加 3 個新欄（先可空，回填後再設 NOT NULL）。
    op.execute("ALTER TABLE raw_layer.raw_records ADD COLUMN source_system TEXT;")
    op.execute("ALTER TABLE raw_layer.raw_records ADD COLUMN source_file_hash TEXT;")
    op.execute("ALTER TABLE raw_layer.raw_records ADD COLUMN imported_at TIMESTAMPTZ;")

    # 2. 從 source_files 經 source_file_id 回填來源 metadata。
    op.execute(
        """
        UPDATE raw_layer.raw_records r
        SET source_system = sf.source_system,
            source_file_hash = sf.file_hash,
            imported_at = sf.imported_at
        FROM raw_layer.source_files sf
        WHERE sf.id = r.source_file_id;
        """
    )
    # sheet_name 目標為 NOT NULL；既有若有 NULL 先補占位（importer 一律有值，僅防禦）。
    op.execute("UPDATE raw_layer.raw_records SET sheet_name = '(unknown)' WHERE sheet_name IS NULL;")

    # 3. 驗證回填無 NULL；不完整就中止 migration（不留半套）。
    missing = op.get_bind().execute(
        sa.text(
            "SELECT count(*) FROM raw_layer.raw_records "
            "WHERE source_system IS NULL OR source_file_hash IS NULL "
            "OR imported_at IS NULL OR sheet_name IS NULL"
        )
    ).scalar()
    if missing:
        raise RuntimeError(
            f"raw_records backfill incomplete: {missing} rows still have NULL source metadata"
        )

    # 4. 設 NOT NULL 與 imported_at 預設（同一 transaction 內 now() 為固定 transaction time）。
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN source_system SET NOT NULL;")
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN source_file_hash SET NOT NULL;")
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN imported_at SET NOT NULL;")
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN imported_at SET DEFAULT now();")
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN sheet_name SET NOT NULL;")

    # 5. 先移除相依 view（引用 patent_sources.source_file_id 與 source_files）。
    op.execute("DROP VIEW IF EXISTS core_layer.patent_source_summary;")

    # 6. 移除三個指向 source_files 的 FK。
    op.execute("ALTER TABLE raw_layer.raw_records DROP CONSTRAINT raw_records_source_file_id_fkey;")
    op.execute("ALTER TABLE core_layer.patent_sources DROP CONSTRAINT patent_sources_source_file_id_fkey;")
    op.execute("ALTER TABLE core_layer.patent_attributes DROP CONSTRAINT patent_attributes_source_file_id_fkey;")

    # 7. 移除 raw_records 舊 unique 與舊 index（皆含 source_file_id）。
    op.execute(
        "ALTER TABLE raw_layer.raw_records "
        "DROP CONSTRAINT raw_records_source_file_id_sheet_name_row_number_key;"
    )
    op.execute("DROP INDEX IF EXISTS raw_layer.idx_raw_records_source_file_id;")

    # 8. 移除三張表的 source_file_id 欄。
    op.execute("ALTER TABLE raw_layer.raw_records DROP COLUMN source_file_id;")
    op.execute("ALTER TABLE core_layer.patent_sources DROP COLUMN source_file_id;")
    op.execute("ALTER TABLE core_layer.patent_attributes DROP COLUMN source_file_id;")

    # 9. 新 unique（來源系統＋檔 hash＋sheet＋列號）與重複檔判斷索引。
    op.execute(
        "ALTER TABLE raw_layer.raw_records "
        "ADD CONSTRAINT raw_records_source_sheet_row_key "
        "UNIQUE (source_system, source_file_hash, sheet_name, row_number);"
    )
    op.execute(
        "CREATE INDEX idx_raw_records_source_system_hash "
        "ON raw_layer.raw_records (source_system, source_file_hash);"
    )

    # 10. 重建 patent_source_summary：改由 raw_record_id → raw_records 取來源（去除 file_name）。
    op.execute(
        """
        CREATE VIEW core_layer.patent_source_summary AS
         SELECT patent_id,
                jsonb_agg(source_obj ORDER BY imported_at, source_file_hash) AS source_summary
           FROM (
                SELECT DISTINCT
                    ps.patent_id,
                    r.imported_at,
                    r.source_file_hash,
                    jsonb_build_object(
                        'source_system', r.source_system,
                        'source_file_hash', r.source_file_hash,
                        'imported_at', r.imported_at
                    ) AS source_obj
                FROM core_layer.patent_sources ps
                JOIN raw_layer.raw_records r ON r.id = ps.raw_record_id
           ) d
          GROUP BY patent_id;
        """
    )

    # 11. drop source_files（其索引與 sequence 一併移除）。
    op.execute("DROP TABLE raw_layer.source_files;")

    # 12. 更新 schema 註解（新欄＋表級＋view 級）。
    op.execute("COMMENT ON COLUMN raw_layer.raw_records.source_system IS '來源系統（例如 WIPS）'")
    op.execute("COMMENT ON COLUMN raw_layer.raw_records.source_file_hash IS '來源檔內容 SHA-256，供重複檔判斷與來源追溯'")
    op.execute("COMMENT ON COLUMN raw_layer.raw_records.imported_at IS '匯入時間；同一批次同一 DB transaction time'")
    op.execute(
        "COMMENT ON TABLE raw_layer.raw_records IS "
        "'原始匯入紀錄：每列保存來源檔整筆原始資料與來源 metadata（source_system/檔 hash/匯入時間），不做清洗'"
    )
    op.execute(
        "COMMENT ON VIEW core_layer.patent_source_summary IS "
        "'每件專利的來源彙總檢視：經 raw_record 追出該專利來自哪些匯入（來源系統/檔 hash/匯入時間）'"
    )


def downgrade() -> None:
    """重建 source_files 與 source_file_id 關聯；無原檔名/路徑時以 hash 衍生占位值。"""
    # 1. 先移除新版 view（引用 raw_records 的來源欄）。
    op.execute("DROP VIEW IF EXISTS core_layer.patent_source_summary;")

    # 2. 重建 source_files 表（id 用 IDENTITY；欄位對齊原 baseline）。
    op.execute(
        """
        CREATE TABLE raw_layer.source_files (
            id BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
            source_system TEXT NOT NULL,
            file_name TEXT NOT NULL,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            record_count INTEGER,
            imported_at TIMESTAMPTZ NOT NULL DEFAULT now()
        );
        """
    )
    op.execute("CREATE INDEX idx_source_files_file_hash ON raw_layer.source_files (file_hash);")
    op.execute("CREATE INDEX idx_source_files_imported_at ON raw_layer.source_files (imported_at);")

    # 3. 由 raw_records 反推每個來源檔（source_system＋hash 為一個檔），檔名/路徑以 hash 衍生占位。
    op.execute(
        """
        INSERT INTO raw_layer.source_files
            (source_system, file_name, file_path, file_hash, record_count, imported_at)
        SELECT source_system,
               'restored_' || source_file_hash || '.dat',
               'restored/' || source_system || '/' || source_file_hash,
               source_file_hash,
               count(*),
               min(imported_at)
        FROM raw_layer.raw_records
        GROUP BY source_system, source_file_hash;
        """
    )

    # 4. 三張表加回 source_file_id（先可空）。
    op.execute("ALTER TABLE raw_layer.raw_records ADD COLUMN source_file_id BIGINT;")
    op.execute("ALTER TABLE core_layer.patent_sources ADD COLUMN source_file_id BIGINT;")
    op.execute("ALTER TABLE core_layer.patent_attributes ADD COLUMN source_file_id BIGINT;")

    # 5. 回填 source_file_id：raw_records 依 system+hash 對回 source_files；子表依 raw_record_id 對回。
    op.execute(
        """
        UPDATE raw_layer.raw_records r
        SET source_file_id = sf.id
        FROM raw_layer.source_files sf
        WHERE sf.source_system = r.source_system AND sf.file_hash = r.source_file_hash;
        """
    )
    op.execute(
        """
        UPDATE core_layer.patent_sources ps
        SET source_file_id = r.source_file_id
        FROM raw_layer.raw_records r
        WHERE r.id = ps.raw_record_id;
        """
    )
    op.execute(
        """
        UPDATE core_layer.patent_attributes pa
        SET source_file_id = r.source_file_id
        FROM raw_layer.raw_records r
        WHERE r.id = pa.raw_record_id;
        """
    )

    # 6. 還原 NOT NULL（raw_records/patent_sources 原為 NOT NULL；patent_attributes 原為可空 SET NULL）。
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN source_file_id SET NOT NULL;")
    op.execute("ALTER TABLE core_layer.patent_sources ALTER COLUMN source_file_id SET NOT NULL;")

    # 7. 還原 FK。
    op.execute(
        "ALTER TABLE raw_layer.raw_records "
        "ADD CONSTRAINT raw_records_source_file_id_fkey "
        "FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE CASCADE;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_sources "
        "ADD CONSTRAINT patent_sources_source_file_id_fkey "
        "FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE CASCADE;"
    )
    op.execute(
        "ALTER TABLE core_layer.patent_attributes "
        "ADD CONSTRAINT patent_attributes_source_file_id_fkey "
        "FOREIGN KEY (source_file_id) REFERENCES raw_layer.source_files(id) ON DELETE SET NULL;"
    )

    # 8. 移除新版 unique/index 與新欄。
    op.execute("DROP INDEX IF EXISTS raw_layer.idx_raw_records_source_system_hash;")
    op.execute(
        "ALTER TABLE raw_layer.raw_records DROP CONSTRAINT raw_records_source_sheet_row_key;"
    )
    op.execute("ALTER TABLE raw_layer.raw_records DROP COLUMN source_system;")
    op.execute("ALTER TABLE raw_layer.raw_records DROP COLUMN source_file_hash;")
    op.execute("ALTER TABLE raw_layer.raw_records DROP COLUMN imported_at;")
    # sheet_name 還原為可空（原 baseline 為 nullable；'(unknown)' 占位無法逐列還原，保留現值）。
    op.execute("ALTER TABLE raw_layer.raw_records ALTER COLUMN sheet_name DROP NOT NULL;")

    # 9. 還原舊 unique 與舊 index。
    op.execute(
        "ALTER TABLE raw_layer.raw_records "
        "ADD CONSTRAINT raw_records_source_file_id_sheet_name_row_number_key "
        "UNIQUE (source_file_id, sheet_name, row_number);"
    )
    op.execute(
        "CREATE INDEX idx_raw_records_source_file_id "
        "ON raw_layer.raw_records (source_file_id);"
    )

    # 10. 還原原始 patent_source_summary（含 file_name，走 source_files）。
    op.execute(
        """
        CREATE VIEW core_layer.patent_source_summary AS
         SELECT ps.patent_id,
            jsonb_agg(jsonb_build_object(
                'source_system', sf.source_system,
                'file_name', sf.file_name,
                'file_hash', sf.file_hash,
                'imported_at', sf.imported_at) ORDER BY sf.imported_at, sf.id) AS source_summary
           FROM (SELECT DISTINCT patent_sources.patent_id, patent_sources.source_file_id
                   FROM core_layer.patent_sources) ps
             JOIN raw_layer.source_files sf ON sf.id = ps.source_file_id
          GROUP BY ps.patent_id;
        """
    )
