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

### 2026-07-16 - Random IDs were used to break equal message timestamps

- Context: Invalidating an OpenClaw response when a newer inbound message arrives during generation.
- Error: Equal millisecond timestamps were broken by random event IDs, so an older event could be classified as newer and supersede the wrong job.
- Cause: The implementation reused display ordering as causal ingestion ordering even though generated IDs carry no chronology.
- Correction: Compare the SQLite insertion sequence for events within the same conversation and keep IDs only for correlation.
- Prevention: Concurrency and supersession checks require an explicit monotonic sequence; never infer causality from timestamps plus random IDs.

### 2026-07-16 - Catalog UI repeated nested f-string failure

- Context: Rendering an optional formatted catalog price in the server-rendered venue page.
- Error: A nested f-string with escaped dictionary-key quotes made `web.py` fail compilation.
- Cause: Price formatting logic was embedded inside an already interpolated HTML expression despite the existing prevention rule for this exact pattern.
- Correction: Move price formatting into a small helper and interpolate only its completed string; compile the package immediately.
- Prevention: Treat the existing no-nested-f-string rule as a review gate: any conditional or formatted value used by server-rendered HTML must be computed before the HTML expression.

### 2026-07-16 - Windows execution policy blocked OpenClaw and local scripts

- Context: Inspecting the installed OpenClaw CLI and securely synchronizing its existing Gateway token into the ignored project environment.
- Error: PowerShell refused both the npm `openclaw.ps1` wrapper and a repository-local `.ps1` helper because script execution is disabled.
- Cause: Windows command resolution selected PowerShell scripts while the machine execution policy disallows them.
- Correction: Invoke OpenClaw through `cmd /c openclaw ...`; run a reviewed local helper with process-scoped `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ...` and never change the machine-wide policy.
- Prevention: Windows runbooks must use the OpenClaw `.cmd` path and process-scoped bypass only for reviewed repository scripts; never instruct operators to weaken global execution policy.

### 2026-07-16 - OpenClaw agent creation outlived the command timeout

- Context: Creating the isolated `rrpp` agent non-interactively.
- Error: The CLI returned the successful agent JSON but remained alive long enough for the managed command timeout to report failure.
- Cause: OpenClaw continued plugin discovery or cleanup after persisting and reporting the agent.
- Correction: Verify `agents.list` after a timed-out create before retrying; the agent existed and a retry would have duplicated work or failed.
- Prevention: Treat timed-out stateful CLIs as unknown completion, inspect durable state first, and only retry when the intended object is absent.

### 2026-07-16 - Inline Python SQL quoting failed under PowerShell

- Context: Reading the durable worker instance ID to restart the provider after changing `.env`.
- Error: Two `python -c` attempts were parsed with broken nested quotes and failed before querying SQLite.
- Cause: SQL string quoting was composed across PowerShell and Python command-line parsers.
- Correction: Use a short repository-local ignored script for the query, verify the correlated PID, then remove the script.
- Prevention: Do not use nested `python -c` for quoted SQL on Windows PowerShell; use an existing CLI or a temporary reviewed script when no SQLite shell command exists.

### 2026-07-16 - Pinned client tool was incompatible with the active model backend

- Context: Real local smoke test of the authenticated OpenClaw Chat Completions endpoint using the `rrpp` agent.
- Error: The agent generated a valid plain-text proposal, but a function-pinned `tool_choice` caused the endpoint to return `502` because the active ChatGPT backend did not emit the caller-defined function call.
- Cause: The implementation assumed documented client-tool support behaved uniformly across the configured model transport without testing the actual backend.
- Correction: Keep the structured `propose_draft` tool as the preferred result, use `tool_choice=auto`, and validate a bounded non-empty assistant-text fallback. Both formats remain pending human review and have no execution capability.
- Prevention: Smoke-test provider response formats against the configured backend before requiring one transport-specific structured-output mechanism; retain strict size, type, policy, and review boundaries independently of format.

### 2026-07-16 - Batched inspection repeated unverified or optional assumptions

- Context: Reading migrations and test helpers during the OpenClaw provider increment.
- Error: Grouped read commands referenced migration and test filenames that had not been inventoried, and a later group treated a no-match reference scan as a required success. Each caused the entire grouped result to be discarded; this repeated an error already recorded below.
- Cause: The batch was assembled from naming assumptions instead of only paths returned by `rg --files`.
- Correction: Inventory repository paths first, then read only confirmed results; split optional reads so one missing file cannot hide all useful output.
- Prevention: A batched inspection may contain only previously confirmed paths and commands expected to return zero. Run optional no-match scans separately. This rule applies to tests as well as source and migration files.

### 2026-07-16 - Cross-file patch structure and fragile UI context failed

- Context: Adding venue knowledge and OpenClaw configuration across Python, HTML, and example environment files.
- Error: One large patch failed on console-encoding context, a second contained an extra space in an HTML route, and another omitted an `Update File` marker between files.
- Cause: Independent changes were combined into context-sensitive patches without validating every boundary and exact source line.
- Correction: Reapply changes per file using stable ASCII context, then compile immediately after Python block changes.
- Prevention: Keep manual patches file-scoped when they touch long server-rendered HTML or non-ASCII text; verify every patch header and copy exact route strings from source.

### 2026-07-16 - Initial OpenClaw tests asserted the wrong stage

- Context: First full-suite run for the OpenClaw increment.
- Error: A configuration test incorrectly unpacked dictionaries, and a dashboard test expected a per-venue routing selector before any venue existed.
- Cause: The tests encoded an invalid Python iteration shape and ignored the page lifecycle that controls when the selector is rendered.
- Correction: Iterate configuration dictionaries directly and fetch the venue page again after creating the venue.
- Prevention: Run new tests in isolation before the full suite and place UI assertions after the state transition that renders the target control.

### 2026-07-16 - Managed shell denied a temporary log path

- Context: Capturing noisy full-suite output outside the repository.
- Error: PowerShell could not write the test log to `C:\tmp` even though the orchestration layer advertises a temporary writable root.
- Cause: The managed PowerShell process and tool-level filesystem permissions expose different path capabilities.
- Correction: Capture generated logs under the ignored repository `var/` directory.
- Prevention: Use repository-local ignored paths for shell-generated test output in this workspace, matching the existing smoke-test rule.

### 2026-07-15 - Windows blocked structured local-port inspection

- Context: Restarting the local Instagram webhook after enabling its configured inbound connector.
- Error: `Get-NetTCPConnection` returned access denied while querying the listener on port 8081.
- Cause: The managed Windows session does not grant the CIM permissions required by that cmdlet for this inspection.
- Correction: Use the read-only `netstat -ano` output to identify the listener before restarting the known webhook process.
- Prevention: On this workstation, prefer `netstat` for local port diagnostics unless elevated CIM access is explicitly available.

### 2026-07-15 - Background process helpers have Windows environment limits

- Context: Restarting and inspecting the local Instagram webhook and Cloudflare tunnel.
- Error: `Start-Process` with output redirection failed on duplicate `Path`/`PATH` environment keys, and the retired `wmic` tool was unavailable for command-line inspection.
- Cause: The managed shell exposes case-variant environment keys and this Windows installation does not include WMIC.
- Correction: Rebuild the process `Path` from machine and user values, remove both case variants from the process environment, launch hidden without redirection outside the sandbox job, and validate through `netstat` plus health checks.
- Prevention: Do not use redirected `Start-Process` or WMIC on this workstation. Normalize `Path`, use `-WindowStyle Hidden`, and start long-running demo processes outside the temporary sandbox job.

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

### 2026-07-16 - Venue form exposed a technical browser validation error

- Context: An operator tried to create a venue through the private dashboard.
- Error: The browser blocked the form with its generic format warning because the required internal identifier accepted only lowercase ASCII slug syntax.
- Cause: The dashboard required an implementation detail from the operator and relied on native pattern validation without explaining it in the UI.
- Correction: The identifier is now optional and derived safely from the venue name; supplied labels are normalized, and the form explains the resulting value.
- Prevention: Dashboard forms must use operator-facing labels and defaults. Generate technical identifiers from human input where possible, and test the browser-facing form contract as well as server validation.

### 2026-07-16 - Sandbox blocked public webhook round-trip validation

- Context: Diagnosing an active local Instagram webhook and Cloudflare quick tunnel.
- Error: The sandboxed Python process received `WinError 10013` when it attempted an HTTPS request to the public tunnel hostname.
- Cause: The managed execution environment restricts outbound sockets even though the local webhook and Cloudflare metrics endpoints are reachable.
- Correction: Repeat the non-mutating public verification through the approved elevated network command; it returned `200` with the expected challenge.
- Prevention: Treat a sandbox socket-denied error as an environment limitation, not a connector failure. Use the approved network path for public round-trip diagnostics and never print verification tokens.

### 2026-07-16 - Source inspection assumed migration filenames

- Context: Inspecting the runtime-mode schema before planning the outbound Instagram increment.
- Error: The inspection command referenced migration filenames that did not exist and returned PowerShell path errors.
- Cause: The command inferred descriptive filenames instead of first using the repository's actual migration inventory.
- Correction: List `rrpp_bridge/sql/` and inspect the recorded filenames before reading migration contents.
- Prevention: Never guess repository filenames in diagnostic commands; use `rg --files` or a directory listing first.

### 2026-07-16 - Generic OpenClaw workspace returned the obsolete response contract

- Context: Running the real local OpenClaw smoke test before enabling Instagram delivery.
- Error: The reachable `rrpp` agent returned the obsolete `decision/reply` object instead of the five-field bridge decision, so validation rejected it.
- Cause: The agent still used OpenClaw's generic workspace; request-level schema instructions alone did not reliably override its response convention.
- Correction: Version only the safe `config/openclaw/AGENTS.md` template, copy it to the ignored `var/openclaw-workspace/`, point `rrpp` there, start a fresh check session, and retain strict bridge-side schema validation.
- Prevention: Keep runtime workspaces outside version control. Run `rrpp-bridge agent-check` after any OpenClaw workspace, model, or Gateway change. Never enable automatic delivery unless it reports `structured: true` with the current contract.

### 2026-07-16 - OpenClaw runtime workspace was placed inside the tracked source tree

- Context: Preparing the `rrpp` agent contract for the outbound Instagram demo.
- Error: The runtime workspace was created at the repository root, where OpenClaw generated identity, bootstrap, heartbeat, tool, user, and state files that could accidentally be committed later.
- Cause: The response contract and OpenClaw runtime state were treated as one artifact.
- Correction: Keep the reviewed contract template in `config/openclaw/AGENTS.md`; copy it into the ignored `var/openclaw-workspace/` for local execution.
- Prevention: Version agent policy templates, never mutable agent workspaces, memory, sessions, bootstrap state, or locally generated identity files.

### 2026-07-16 - Secret-bearing environment lines were included in diagnostic output

- Context: Confirming the exact Instagram outbound feature-flag name before the live demo.
- Error: A broad repository search included `.env` and printed matching credential lines alongside harmless configuration flags.
- Cause: The diagnostic searched by the `INSTAGRAM_` prefix instead of restricting `.env` output to an allowlist of non-secret keys.
- Correction: Do not persist or repeat the values; use exact-key parsing that reports only boolean flags or whether a secret is present.
- Prevention: Exclude `.env` from broad searches. For secret-bearing configuration, emit only key names, presence, length class, or a redacted value.

### 2026-07-16 - Job status diagnostic used the wrong persisted column name

- Context: Checking whether the worker was alive or blocked while diagnosing a missing Instagram DM.
- Error: The diagnostic queried `jobs.status`, although the queue schema names the column `jobs.state`.
- Cause: The query was written from the dashboard vocabulary instead of the inspected database schema.
- Correction: Query `state` and keep user-facing grouped labels separate from persisted field names.
- Prevention: Inspect the table schema before issuing ad hoc operational SQL, even when a similarly named field exists in the UI model.
- Recurrence: The same assumption was repeated against `deliveries.state`; that table uses `deliveries.status`. The corrected query and schema inspection confirmed one real delivery in `sent` state.

### 2026-07-16 - Pre-staging diff check omitted untracked files

- Context: Running final validation before committing the account-centered Instagram delivery increment.
- Error: `git diff --check` passed before staging but two new Python files still contained trailing whitespace.
- Cause: The normal working-tree diff does not include untracked files.
- Correction: Stage the reviewed files and run `git diff --cached --check`; remove the reported whitespace before committing.
- Prevention: For changes containing new files, treat the staged diff check as mandatory in addition to the pre-staging working-tree check.

### 2026-07-16 - Meta token was validated against the wrong identity endpoint

- Context: Checking whether the configured Instagram token and professional account ID matched before the outbound demo.
- Error: A `graph.instagram.com/{id}` check was interpreted as an account mismatch, then a Facebook Page lookup returned `401`.
- Cause: The diagnosis initially assumed a Facebook Page Access Token, while the configured credential uses Instagram Login and represents an Instagram professional account.
- Correction: Identify the login type from the host that accepts the token, use `graph.instagram.com/me` for Instagram Login, align the local professional account ID, and keep the Send API on the official Instagram host.
- Prevention: Token checks must first distinguish Instagram Login from Facebook Login. Never infer an account mismatch by comparing identities returned from endpoints belonging to different login models.
