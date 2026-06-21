# RRPP Agent Bridge

Dependency-free V1 bridge foundation for safe, observable processing of future customer communication channels.

## Local setup

Requires Python 3.12 or newer. Set the environment variables shown in `.env.example`; do not commit a real `.env` file.

The application automatically reads a repository-local `.env` file and never overrides variables already supplied by the operating system.

PowerShell example:

```powershell
$env:RRPP_DASHBOARD_USER = "admin"
$env:RRPP_DASHBOARD_PASSWORD = "use-a-long-random-password"
$env:RRPP_SESSION_SECRET = "use-at-least-32-random-characters-here"
$env:RRPP_MODE = "shadow"
python -m rrpp_bridge init-db
python -m rrpp_bridge web
```

In a second terminal, run the independent worker:

```powershell
python -m rrpp_bridge worker
```

Open `http://127.0.0.1:8080`, authenticate, and submit a local simulator event. The event is durably persisted before the worker handles it. V1 produces drafts or owner escalations but has no external sender.

The dashboard provides a bounded overview plus dedicated views for conversations, human review, full audit activity, and nightlife venues. Create a venue and add an exact Gmail recipient address or simulator recipient as its routing rule. Unmatched conversations remain under `Sense assignar`. Approving a draft only marks it prepared; it never sends a message.

Docker is not required. The web process and worker use the same SQLite database and should run in separate terminals. The dashboard can change the durable execution mode, retry dead-letter jobs, and dismiss terminal failures. All V1 execution records target a network-free local sink and are marked as simulated.

`scripts/run-local.ps1` also starts the Gmail poller when authorized and the maintenance process for health reporting and scheduled backups. The `Sistema` view shows service heartbeats and recovery status.

Useful operational commands:

```powershell
python -m rrpp_bridge migrate
python -m rrpp_bridge status
python -m rrpp_bridge recover-stale
python -m rrpp_bridge worker --once
```

`migrate` creates a consistent SQLite backup before applying pending migrations. Existing databases never migrate during normal startup; web, worker, and poller processes stop with an explicit instruction if this command is required. Automatic retries use bounded exponential backoff. Expired worker leases are recovered automatically and can also be recovered explicitly with the CLI.

Operational commands:

```powershell
python -m rrpp_bridge backup create --kind manual
python -m rrpp_bridge backup verify backups\BACKUP.db
python -m rrpp_bridge restore backups\BACKUP.db --confirm RESTORE
python -m rrpp_bridge maintenance --once
```

For the private Docker/VPS topology, backup encryption, SSH access, and restore procedure, see [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md).

`RRPP_CANARY_SENDERS` is a comma-separated allowlist used only in `canary` mode. Even `live` uses the simulated local sink in V1; adding a real external executor requires a separate security review and ADR.

After the local virtual environment and `.env` have been prepared, the shortest startup command is:

```powershell
.\scripts\run-local.ps1
```

This runs the worker in a hidden child process and the dashboard in the current terminal. Press `Ctrl+C` to stop the dashboard; the script also stops its worker process.

Open `http://127.0.0.1:8080/login`. The prepared local username is `admin`; its generated password is the `RRPP_DASHBOARD_PASSWORD` value in the ignored `.env` file.

## Gmail read-only connector

Place the installed-application OAuth client at `secrets/gmail-oauth-client.json`, then authorize once:

```powershell
.\.venv\Scripts\rrpp-bridge.exe gmail-auth
.\.venv\Scripts\rrpp-bridge.exe gmail-poll --once
```

The requested scope is only `gmail.readonly`. Credentials remain under ignored `secrets/`; the connector reads `INBOX`, never sends, deletes, archives, labels, marks read, or otherwise changes email. After authorization, `scripts/run-local.ps1` automatically starts the Gmail poller beside web and worker.

## Tests

```powershell
python -m unittest discover -s tests -v
```

See [`docs/agent-guide/`](docs/agent-guide/README.md) for requirements, architecture, security rules, and delivery milestones.
