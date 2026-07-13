BEGIN;

ALTER TABLE core_layer.patents
    ADD COLUMN IF NOT EXISTS "審查的公告號" TEXT;

UPDATE core_layer.patents p
SET "審查的公告號" = source_attr."審查的公告號"
FROM (
    SELECT DISTINCT ON (patent_id)
        patent_id,
        "審查的公告號"
    FROM core_layer.patent_attributes
    WHERE NULLIF(BTRIM("審查的公告號"), '') IS NOT NULL
    ORDER BY patent_id, id DESC
) source_attr
WHERE p.id = source_attr.patent_id
  AND p."審查的公告號" IS NULL;

ALTER TABLE core_layer.patent_attributes
    DROP COLUMN IF EXISTS "審查的公告號";

CREATE INDEX IF NOT EXISTS idx_patents_examined_publication_number
    ON core_layer.patents("審查的公告號");

ALTER TABLE derived_layer.report_patent_base
    ADD COLUMN IF NOT EXISTS "審查的公告號" TEXT;

COMMIT;
