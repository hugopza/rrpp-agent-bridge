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

### 2026-07-02 - Git commit attempted inside read-only metadata sandbox

- Context: Committing the completed Instagram inbound increment.
- Error: Git could not create `.git/index.lock`, so neither staging nor commit occurred.
- Cause: Repository content is writable in the managed sandbox, but Git metadata is mounted read-only unless the Git command uses its approved elevated permission.
- Correction: Verify the worktree remained unstaged, then rerun `git add` and `git commit` through the approved Git escalation.
- Prevention: In this workspace, use the approved elevated Git command path for staging and commits instead of first attempting them in the filesystem sandbox.

### 2026-07-02 - Isolated package build attempted blocked network access

- Context: Final package verification for the Instagram inbound increment.
- Error: `python -m build` created an isolated environment and failed while trying to download Hatchling from PyPI.
- Cause: The managed execution environment blocks outbound package-index access even though the build backend is already installed in the project virtual environment.
- Correction: Repeat package verification with `python -m build --no-isolation` using the pinned local build tooling.
- Prevention: In this managed workspace, use non-isolated builds after confirming the declared build backend is installed; reserve isolated network builds for CI with package-index access.

### 2026-07-02 - Queue transaction refactor introduced invalid indentation

- Context: Extracting reusable in-transaction event enqueueing for batched Instagram webhooks.
- Error: The first patch left the insertion block over-indented and `compileall` rejected `queue.py`.
- Cause: A nested transaction block was removed without realigning its former body in the same edit.
- Correction: Realign the full insertion block and compile the package before continuing.
- Prevention: After changing Python block structure, run `compileall` immediately before layering further edits.

### 2026-06-21 - PowerShell expanded container shell substitutions

- Context: Running an ephemeral `age` verification through `docker run ... sh -c` on Windows.
- Error: PowerShell evaluated `$(id -u)`, `$(age-keygen ...)`, and `$(age ...)` on the host before the command reached the Linux container.
- Cause: A double-quoted PowerShell argument contained command-substitution syntax intended for the inner POSIX shell.
- Correction: Use a PowerShell-literal outer argument and files for intermediate values so only the container shell interprets its syntax.
- Prevention: Cross-shell smoke commands must avoid nested interpolation; prefer argument arrays or literal outer quoting and verify which shell owns every substitution.

### 2026-06-21 - Compose built the same image concurrently

- Context: Building the shared runtime image for web, worker, and maintenance services.
- Error: BuildKit exported several identical build targets to `rrpp-agent-bridge:local` concurrently and failed with `image already exists`.
- Cause: The shared Compose anchor gave every service both the same `build` definition and the same image tag.
- Correction: Only the web service owns the build definition; all other services reference the resulting immutable local image.
- Prevention: In Compose, define one build owner per shared image tag and make sibling services image-only consumers.

### 2026-06-21 - Windows lacked IANA timezone data

- Context: Scheduling backups at 03:00 in `Europe/Madrid` with the standard-library `zoneinfo` API.
- Error: The local Windows Python installation raised `ZoneInfoNotFoundError` because it had no IANA timezone database.
- Cause: The implementation assumed that operating-system timezone files were available on every supported platform.
- Correction: Add the cross-platform `tzdata` package as an explicit runtime dependency and test the configured timezone during setup.
- Prevention: Any named IANA timezone used by Windows-supported code requires an explicit timezone-data dependency and a startup validation test.

### 2026-06-21 - Failed startup validation leaked SQLite connection

- Context: Testing that runtime startup rejects an existing outdated database.
- Error: The expected validation exception left the database connection open, so Windows could not remove the test database.
- Cause: Connection cleanup occurred only after successful application initialization.
- Correction: Application initialization now closes through `finally`, and CLI preparation closes before re-raising any startup exception.
- Prevention: Every connection acquired before validation must have an exception-path close; test startup failures inside a temporary directory to expose Windows file-handle leaks.

### 2026-06-21 - Runtime startup bypassed migration backup

- Context: Applying schema 004 to the local operational database.
- Error: The explicit migration command found the database already upgraded and therefore could not create its intended pre-migration backup.
- Cause: Normal web, worker, poller, and status startup paths called the general migration initializer automatically.
- Correction: Runtime startup now initializes only an empty database and fails with an explicit migration instruction when an existing schema is outdated; only `rrpp-bridge migrate` upgrades existing data and takes the backup.
- Prevention: Existing databases may only advance schema through the dedicated migration command; automated tests verify that runtime startup leaves an outdated schema unchanged.

### 2026-06-21 - Nested HTML f-string broke Python parsing

- Context: Rendering an optional dashboard form inside a venue-route list item.
- Error: A nested f-string with escaped quotes produced a Python syntax error during bytecode compilation.
- Cause: Conditional HTML generation, interpolation, and quoting were combined into one expression.
- Correction: The route row now uses a small rendering function that builds the optional control separately.
- Prevention: Do not nest f-strings for conditional HTML; compute optional fragments first and run `compileall` after each substantial server-rendered UI block.

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

### 2026-06-19 - Local setup assumed newer PowerShell cryptography and UTF-8 behavior

- Context: Automated creation of local secrets and `.env` on Windows PowerShell.
- Error: The shell lacked the static `RandomNumberGenerator.Fill` method, and `Set-Content -Encoding utf8` added a BOM that invalidated the first environment key.
- Cause: The setup command assumed newer .NET and PowerShell encoding semantics than the user's installed shell provides.
- Correction: Secret generation uses an instantiated random-number generator with `GetBytes`; the file is written with BOM-less `UTF8Encoding`, while the application accepts UTF-8 with or without BOM.
- Prevention: Use Windows PowerShell 5.1-compatible APIs for setup scripts and explicitly control BOM behavior for machine-readable files.

### 2026-06-19 - Packaging configuration omitted the build backend

- Context: Installing the project editable and building the first wheel.
- Error: `pip` fell back to setuptools, whose flat-layout discovery treated `var/` as another top-level package.
- Cause: Hatch-specific build configuration existed, but `pyproject.toml` did not declare Hatchling in `[build-system]`.
- Correction: The manifest now declares `hatchling.build`, explicitly packages `rrpp_bridge`, includes migration SQL, and ignores build artifacts.
- Prevention: A packaging change is incomplete until editable installation and wheel/sdist builds succeed from a workspace containing ignored runtime directories.

### 2026-06-19 - Editable install excluded its build helper

- Context: Retrying editable installation after installing Hatchling locally.
- Error: `pip install --no-build-isolation -e .` could not import the `editables` helper.
- Cause: Disabling build isolation also bypassed discovery and installation of an editable-only build requirement.
- Correction: Install the helper in the project virtual environment, then verify both editable installation and the generated CLI entry point.
- Prevention: Use normal build isolation for editable installs unless every dynamic build requirement has been explicitly provisioned and verified.

### 2026-06-19 - Migration tests hard-coded the previous latest version

- Context: Adding the Gmail connector state migration.
- Error: Existing tests expected exactly migrations 1 and 2; assertion failure occurred before closing SQLite connections, causing Windows cleanup errors.
- Cause: Tests encoded a moving implementation count and did not protect resource cleanup with `finally`.
- Correction: Expected ranges derive from `latest_version()`, and database connections always close through `finally` blocks.
- Prevention: Migration tests may pin their starting version but must derive the target version and guarantee cleanup on assertion failure.
