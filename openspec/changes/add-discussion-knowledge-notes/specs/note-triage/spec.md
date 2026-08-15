## ADDED Requirements

### Requirement: Periodic note triage

The system SHALL provide a triage entry point that lists all active notes with their status and lets the user promote, merge, or discard each one, consolidating the index accordingly.

#### Scenario: List all active notes

- **WHEN** user runs triage
- **THEN** the command lists every active note with topic, date, status, and key conclusion for per-entry decision

#### Scenario: Promote a note to authoritative docs

- **WHEN** user confirms a note holds a stable conclusion
- **THEN** the conclusion is written as an ADR or into the appropriate design / dataset doc, and the original note is moved to cold storage with a pointer to its authoritative location

#### Scenario: Fold a note into a quick-reference

- **WHEN** user confirms a note belongs to the repeated-lookup category
- **THEN** a `agents/knowledges/` quick-reference entry is created that points to authoritative sources, and the note is marked promoted

#### Scenario: Mark a note stale

- **WHEN** user confirms a note is superseded or invalid
- **THEN** it is marked `stale` and removed from the active index

### Requirement: Same-topic consolidation

Triage SHALL fold multiple notes on the same topic into a single authoritative artifact (one ADR or one quick-reference), compressing N index rows into one and keeping one archived original per topic.

#### Scenario: Many notes collapse into one artifact

- **WHEN** triage consolidates N notes sharing a topic
- **THEN** a single authoritative artifact is written, the active index rows for the topic are removed, and the originals move to cold storage with a single pointer

### Requirement: Cold storage cleanup

Triage SHALL periodically clean cold storage so it does not grow unboundedly: archived originals older than a configured retention window are removed or compressed, and cold storage is never part of the hot grep path nor indexed in AGENTS.md.

#### Scenario: Prune old archived notes

- **WHEN** an archived note has been in cold storage beyond the retention window
- **THEN** it is eligible for removal or compression by triage

#### Scenario: Cold storage not in hot path

- **WHEN** a subsequent analysis searches for knowledge
- **THEN** archived originals are not surfaced as authoritative references; the authoritative source of truth remains the docs or the quick-reference entry it points to