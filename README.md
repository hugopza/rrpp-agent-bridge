# RRPP Agent Bridge

Dependency-free V1 bridge foundation for safe, observable processing of future customer communication channels.

## Local setup

Requires Python 3.12 or newer. Set the environment variables shown in `.env.example`; do not commit a real `.env` file.

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

Docker is not required. The web process and worker use the same SQLite database and should run in separate terminals. The dashboard can change the durable execution mode, retry dead-letter jobs, and dismiss terminal failures. All V1 execution records target a network-free local sink and are marked as simulated.

Useful operational commands:

```powershell
python -m rrpp_bridge migrate
python -m rrpp_bridge status
python -m rrpp_bridge recover-stale
python -m rrpp_bridge worker --once
```

`migrate` creates a consistent SQLite backup before applying pending migrations. Automatic retries use bounded exponential backoff. Expired worker leases are recovered automatically and can also be recovered explicitly with the CLI.

`RRPP_CANARY_SENDERS` is a comma-separated allowlist used only in `canary` mode. Even `live` uses the simulated local sink in V1; adding a real external executor requires a separate security review and ADR.

## Tests

```powershell
python -m unittest discover -s tests -v
```

See [`docs/agent-guide/`](docs/agent-guide/README.md) for requirements, architecture, security rules, and delivery milestones.
