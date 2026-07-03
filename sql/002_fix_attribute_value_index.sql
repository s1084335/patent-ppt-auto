-- Replace the oversized btree index on patent_attributes.attribute_value.
-- Long WIPS fields can exceed PostgreSQL's btree index row size limit.

BEGIN;

DROP INDEX IF EXISTS idx_patent_attributes_key_value;

CREATE INDEX IF NOT EXISTS idx_patent_attributes_key_value_hash
    ON patent_attributes(attribute_key, md5(attribute_value))
    WHERE attribute_value IS NOT NULL;

COMMIT;
