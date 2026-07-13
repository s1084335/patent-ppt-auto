BEGIN;

ALTER TABLE core_layer.patents
    ADD COLUMN IF NOT EXISTS "Orig. CPC(Main)" TEXT,
    ADD COLUMN IF NOT EXISTS "Orig. IPC(Main)" TEXT,
    ADD COLUMN IF NOT EXISTS "Curr. CPC(Main)" TEXT,
    ADD COLUMN IF NOT EXISTS "Curr. IPC(Main)" TEXT;

UPDATE core_layer.patents AS p
SET
    "Orig. CPC(Main)" = COALESCE(p."Orig. CPC(Main)", a."Orig. CPC(Main)"),
    "Orig. IPC(Main)" = COALESCE(p."Orig. IPC(Main)", a."Orig. IPC(Main)"),
    "Curr. CPC(Main)" = COALESCE(p."Curr. CPC(Main)", a."Curr. CPC(Main)"),
    "Curr. IPC(Main)" = COALESCE(p."Curr. IPC(Main)", a."Curr. IPC(Main)")
FROM core_layer.patent_attributes AS a
WHERE a.patent_id = p.id;

ALTER TABLE core_layer.patent_attributes
    DROP COLUMN IF EXISTS "Orig. CPC(Main)",
    DROP COLUMN IF EXISTS "Orig. IPC(Main)",
    DROP COLUMN IF EXISTS "Curr. CPC(Main)",
    DROP COLUMN IF EXISTS "Curr. IPC(Main)";

COMMIT;
