## ADDED Requirements

### Requirement: Capture discussion conclusion as a note

The system SHALL provide a way to capture discussion conclusions and intermediate analysis results into the notes staging area, independent of creating an OpenSpec change.

Support capture from the current conversation, from explicit user-provided material, and as a session-end review pass. Captured entries MUST be written to the notes staging area and registered in the notes index, and MUST be confirmed by the user before any file is written.

#### Scenario: Capture from current conversation

- **WHEN** user invokes the capture command with a topic
- **THEN** the command reviews the current conversation, extracts distinct candidate entries (each with a 1-2 line conclusion and evidence locations), and asks the user to confirm before writing

#### Scenario: Capture from explicit material

- **WHEN** user invokes the capture command with a topic and concrete material
- **THEN** the command builds the note directly from the supplied material without scanning the conversation

#### Scenario: Session-end batch capture

- **WHEN** user invokes the capture command in review mode
- **THEN** the command scans the entire session for capturable points and presents the candidate list for per-entry select / edit / skip confirmation

#### Scenario: No write without confirmation

- **WHEN** user rejects all candidate entries
- **THEN** no files are created or modified

### Requirement: Note entry lifecycle status

Every note entry SHALL carry a status: `promising` (new, not yet reviewed), `promoted` (moved to authoritative docs or a quick-reference), or `stale` (superseded or invalid). Capture writes entries as `promising`.

#### Scenario: New entry starts promising

- **WHEN** a note is first captured
- **THEN** it is marked `promising` and lives in the active notes directory

#### Scenario: Same-topic capture appends

- **WHEN** a new capture matches an existing note's topic
- **THEN** the command appends to the existing note file instead of creating a new one, and updates its content in place

### Requirement: Active notes bounded growth

The active notes directory SHALL remain bounded: growth is per distinct topic (via same-topic merge), triage moves entries out, and a capacity warning triggers lightweight triage when active entries exceed a configured threshold.

#### Scenario: Capacity warning before overflow

- **WHEN** the number of active notes reaches the configured threshold
- **THEN** the capture command warns the user and suggests running a lightweight triage before adding more entries

#### Scenario: Triage reduces active count

- **WHEN** triage completes successfully
- **THEN** promoted entries are moved to cold storage and stale entries are removed, reducing the active count