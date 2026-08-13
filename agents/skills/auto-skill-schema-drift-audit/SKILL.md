---
name: schema-drift-audit
description: Systematically detect schema drift between live PostgreSQL, SQLAlchemy model, SQL migration file, and DAO code
source: auto-skill
extracted_at: '2026-06-24T07:22:35.245Z'
---

# Schema Drift Audit

When you need to verify that code (model + SQL file + DAO) matches the live database schema, follow this procedure. Useful after manual DB changes, before deploying code, or when debugging unexpected runtime errors.

## The Four Sources of Truth

```
┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
│   Live DB       │   │  SQLAlchemy     │   │  SQL Migration  │   │  DAO / Service  │
│ (pg_catalog)    │   │  Model (.py)    │   │  File (.sql)    │   │  (upsert logic) │
└─────────────────┘   └─────────────────┘   └─────────────────┘   └─────────────────┘
       ▲                     ▲                     ▲                     ▲
       │                     │                     │                     │
   Runtime reality     ORM declarations      DDL definition       index_elements,
                                                                column references
```

All four must agree. When they don't, you have schema drift.

## Audit Procedure

### Step 1: Query live DB column definitions

```sql
SELECT column_name, data_type, character_maximum_length, is_nullable, column_default
FROM information_schema.columns
WHERE table_name = '<table>'
ORDER BY ordinal_position;
```

### Step 2: Query live DB constraints

```sql
SELECT conname, contype, pg_get_constraintdef(oid)
FROM pg_constraint
WHERE conrelid = '<table>'::regclass;
```

### Step 3: Read the code artifacts

Read these files for the same table:
- **Model**: `app/dao/models/<table>.py` — column types, nullable, defaults, UniqueConstraint
- **SQL file**: `data/sql/<table>.sql` — DDL column definitions and constraints
- **DAO**: `app/dao/<table>_dao.py` — `index_elements`, `set_` fields in upsert

### Step 4: Build a comparison table

Create a table with one row per column, columns for each source:

```
┌──────────────┬──────────────┬──────────────┬──────────────┐
│ Column       │ Live DB      │ Model        │ SQL File     │
├──────────────┼──────────────┼──────────────┼──────────────┤
│ area_code    │ varchar(60)  │ String(60)   │ varchar(60)  │  ✅
│ target_code  │ MISSING      │ String(60)   │ varchar(60)  │  ❌
│ move_type    │ int def 0    │ Int def "0"  │ int def 0    │  ✅
│ nullable col │ YES          │ nullable=F   │ NOT NULL     │  ❌
└──────────────┴──────────────┴──────────────┴──────────────┘
```

Also compare the unique constraint definitions separately.

### Step 5: Assess runtime impact

For each mismatch, determine:
- **Will inserts/upserts fail?** (missing column, wrong type in ON CONFLICT)
- **Will queries return wrong data?** (type coercion issues)
- **Will ORM reject valid rows?** (nullable=False vs DB allowing NULL)

### Step 5b: Check test fixtures

Test files often hardcode column values (types, names, unique key params). After identifying drift, also check:
- `tests/test_<table>_dao.py` — do row fixtures match current column names and types?
- Do `find_by_unique_key` calls use the correct key columns?
- Are expected counts in service tests consistent with stub data?

**Common pitfall:** Tests written for an older schema version will fail silently or loudly after model changes. Always update test fixtures alongside model/SQL changes.

### Step 6: Decide direction

| Option | When to use |
|--------|------------|
| **Code → DB** | DB was intentionally changed; code needs to catch up |
| **DB → Code** | Code is correct; DB needs migration |

The live DB is the runtime source of truth, but the *intent* may live in code. Check with the user if unclear.

## Step 7: Check semantic/reference data alignment

After structural drift is resolved, verify that **lookup/reference data** matches the format of external data sources. Structural correctness doesn't guarantee semantic correctness.

### Example: Name resolution drift

A reference table (`bd_area_info`) stores short names ("广州", "河北") but an external API (Baidu) returns full names with administrative suffixes ("广州市", "湖南省"). The resolution code uses exact string matching:

```python
query.filter((AreaInfo.name == api_name) | (AreaInfo.simple_name == api_name))
```

This fails silently — a fallback path stores the raw API name as the "code", producing garbage data that's hard to detect without querying the actual stored values.

### How to detect

1. **Sample actual stored data** — `SELECT DISTINCT target_area_code FROM table LIMIT 30` — are codes actually numeric, or are they names masquerading as codes?
2. **Quantify fallback rate** — `SELECT COUNT(*) WHERE code ~ '^\d+$'` vs total — if most "codes" are non-numeric, resolution is broken
3. **Trace the data flow** — what format does the external source return? What format does the reference table store? Do they match?

### Key principle

**Silent fallbacks hide data quality issues.** When resolution code has a fallback path (e.g., "use name as code"), the code won't crash — it will just produce wrong data. Always verify the *output quality*, not just that the code runs without errors.

## Common Drift Patterns

1. **Column added to DB but not model** — manual ALTER TABLE never reflected in code
2. **Type changed in DB** (e.g., integer → varchar) — model still has old type
3. **Nullable mismatch** — model says NOT NULL, DB allows NULL (or vice versa)
4. **Constraint column drift** — unique constraint uses different columns than DAO's `index_elements`
5. **Missing columns** — code references columns that don't exist in DB (guaranteed runtime failure)
6. **Reference data format mismatch** — lookup table stores values in a different format than external APIs produce (silent resolution failure, not a structural issue)

## Key Principles

**Always start from the live DB.** The model and SQL file can both be wrong simultaneously. Only `information_schema` and `pg_constraint` reflect what's actually running.

**Check every column systematically.** Don't focus only on "interesting" columns (new ones, type changes). Nullable mismatches can hide in any column — in one audit, 7 of 14 columns had nullable drift even though only 6 were initially suspected. Build the full comparison table before deciding scope.
