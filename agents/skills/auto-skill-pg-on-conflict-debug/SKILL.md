---
name: pg-on-conflict-debug
description: Diagnose PostgreSQL "ON CONFLICT" errors by comparing code-level index_elements against actual database constraints via pg_constraint
source: auto-skill
extracted_at: '2026-06-24T06:39:52.254Z'
---

# Debugging PostgreSQL ON CONFLICT Constraint Mismatch

When you encounter `psycopg2.errors.InvalidColumnReference: there is no unique or exclusion constraint matching the ON CONFLICT specification`, follow this diagnostic procedure.

## Root Cause

The `ON CONFLICT` clause in an upsert (e.g., SQLAlchemy's `on_conflict_do_update(index_elements=[...])`) requires an **exact match** against a unique or exclusion constraint in the database. Common causes:

1. **Column count mismatch** — DB constraint has more/fewer columns than the code specifies
2. **Column name mismatch** — code references columns not in the constraint
3. **Constraint missing entirely** — SQL migration was never applied to the database
4. **Stale constraint** — schema was evolved (e.g., columns added) but the constraint was not recreated

## Diagnostic Steps

### 1. Find the ON CONFLICT columns in code

Search for `index_elements` or `on_conflict_do_update` in the DAO/repository layer:

```
grep -rn "index_elements\|on_conflict_do_update" app/dao/
```

Note the exact column list, e.g., `["area_code", "occur_date", "move_type"]`.

### 2. Query the actual database constraints

```sql
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = '<schema>.<table>'::regclass
  AND contype IN ('u', 'p', 'x');
```

This reveals the **real** constraint columns, which may differ from what the SQL migration file or SQLAlchemy model declares.

### 3. Compare and identify the drift

| Check | Code expects | DB actually has |
|-------|-------------|-----------------|
| Column count | 3 | 4 (extra `destination_area_code`) |
| Constraint name | `bd_area_history_data_unique` | `bd_area_history_area_code_date_unqiue` |

### 4. Check the model definition

Read the SQLAlchemy model's `__table_args__` for `UniqueConstraint` — verify it matches both the code's `index_elements` and the DB.

### 5. Fix: recreate the constraint

```sql
ALTER TABLE <schema>.<table> DROP CONSTRAINT IF EXISTS <old_constraint_name>;
ALTER TABLE <schema>.<table> ADD CONSTRAINT <correct_name> UNIQUE (<col1>, <col2>, <col3>);
```

Choose the column list that matches the business logic (what the code uses for upsert), not necessarily what the stale DB constraint had.

## Key Takeaway

**Always verify against `pg_constraint`** — the SQL migration file and the SQLAlchemy model may both be correct, but the live database can have a stale constraint from a previous schema iteration. The DB is the source of truth at runtime.
