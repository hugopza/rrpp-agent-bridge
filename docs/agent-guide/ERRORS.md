# Error Register

Record mistakes that can recur or reveal a weakness in the development process. This is not an incident log and MUST NOT contain secrets, personal data, or raw customer content.

## Entry Format

```markdown
### YYYY-MM-DD - Short title

- Context: where the mistake occurred.
- Error: what went wrong.
- Cause: why it happened.
- Correction: how it was fixed or contained.
- Prevention: a concrete rule or automated check that prevents recurrence.
```

## Entries

### 2026-06-19 - Sandbox-inaccessible smoke-test path

- Context: CLI smoke testing used an absolute temporary database path.
- Error: SQLite could not open the database inside the managed execution environment.
- Cause: The chosen host path was outside the command sandbox's writable view.
- Correction: The smoke test was repeated with the ignored `var/` directory inside the workspace.
- Prevention: Use repository-local ignored paths for runtime smoke tests unless external-path access has already been verified.

### 2026-06-19 - Oversized patch used fragile context

- Context: A combined patch attempted to add observability, replace the CLI, and modify many dashboard sections.
- Error: The complete patch was rejected because one CSS context line did not match exactly.
- Cause: Too many independent edits depended on a single large context-sensitive patch.
- Correction: The changes were split by subsystem and the dashboard was replaced as one explicit file operation.
- Prevention: Split cross-file changes into independently verifiable patches; replace a file deliberately when most of its structure changes.

### 2026-06-19 - Tests lagged behind a positional API change

- Context: `process_one` changed from receiving a mode argument to reading durable runtime mode.
- Error: Existing tests passed `shadow` and `dry-run` as the positional worker ID and did not initialize runtime mode.
- Cause: The public helper and its tests were not changed atomically.
- Correction: Tests now initialize durable mode and invoke the new keyword-oriented contract.
- Prevention: When changing a callable signature, search all call sites and update implementation plus tests in the same patch; prefer keyword arguments for operational parameters.

### 2026-06-19 - Tests assumed unstable timestamp ordering

- Context: Retry and mode-matrix tests selected rows using timestamps created within the same millisecond and inserted a timestamp with different precision.
- Error: Tests selected the wrong row or treated an intended past time as later during lexical comparison.
- Cause: Operational identity was inferred from timestamp order instead of stable identifiers, and timestamp formats were mixed.
- Correction: Assertions join through message IDs; forced timestamps use the same fixed-width UTC format as production values.
- Prevention: Use IDs for correlation and ordering; use the project UTC helper or a clearly old fixed-width timestamp in tests.

### 2026-06-19 - Initial migration backup ignored SQLite WAL

- Context: The first migration implementation backed up the database with a filesystem copy.
- Error: A direct copy can omit committed pages still present in the WAL file; migration version checks also happened before taking the write lock.
- Cause: SQLite was treated as a single inert file instead of an active transactional database.
- Correction: Backups use SQLite's online backup API, and migration versions are rechecked while holding `BEGIN IMMEDIATE`.
- Prevention: Use database-native backup and transactional migration primitives; never copy a live SQLite main file as the only backup.
