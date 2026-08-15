## ADDED Requirements

### Requirement: Notes store directory layout

The system SHALL maintain a notes store under `docs/notes/` with `docs/notes/` as the active staging area and `docs/notes/archive/` as cold storage. Each active note is a single markdown file named `<date>-<topic>.md` containing a conclusion section, an evidence / provenance section, and a status marker.

#### Scenario: Active note file shape

- **WHEN** a note is captured
- **THEN** a file `docs/notes/<date>-<topic>.md` exists with `## 结论（1-2 行）`, `## 证据/出处`, and a `## 状态: promising` section

#### Scenario: Promoted note moves to cold storage

- **WHEN** a note is promoted by triage
- **THEN** the original file moves to `docs/notes/archive/` and its top carries a pointer to the authoritative source that absorbed it

### Requirement: Two-level knowledge index

The system SHALL maintain a coarse-level index in AGENTS.md (one row per knowledge category, stable) and an entry-level index in `agents/knowledges/INDEX.md` (one row per captured entry). New categories update AGENTS.md; regular entries update only the entry-level index.

Retrieval MUST be grep-driven: locate the category via AGENTS.md, then grep the note and index files for the topic, opening files only after a keyword hit.

#### Scenario: New category updates AGENTS.md

- **WHEN** the first note or knowledge entry of a new category is captured
- **THEN** AGENTS.md gains one row for the category in the «知识在哪里» map

#### Scenario: Regular entry does not touch AGENTS.md

- **WHEN** an additional note on an existing category is captured
- **THEN** only the entry-level index gains a row; AGENTS.md is unchanged

#### Scenario: Grep-driven retrieval finds a note

- **WHEN** a subsequent analysis needs knowledge whose topic it knows
- **THEN** grep against the note directory and INDEX returns the matching file, which is then read