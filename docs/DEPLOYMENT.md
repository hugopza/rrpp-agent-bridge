# Deployment

The supported production shape is one host with persistent SQLite storage and separate
web, Instagram ingress, worker, OpenClaw Gateway, and maintenance processes.

## Boundaries

- Bind the private dashboard to loopback only.
- Expose only the Instagram ingress through HTTPS.
- Keep the OpenClaw Gateway on loopback with token authentication.
- Run the `rrpp` agent without tools, channel bindings, or Instagram credentials.
- Store `.env`, Meta secrets, OpenClaw tokens, databases, and backups outside images and Git.
- Apply migrations explicitly before starting a new application version.

## Compose build

```powershell
Copy-Item .env.docker.example .env.docker
docker compose --env-file .env.docker build
docker compose --env-file .env.docker --profile tools run --rm migrate
docker compose --env-file .env.docker up -d web worker maintenance
docker compose --env-file .env.docker --profile instagram up -d instagram
```

The dashboard publishes only to `127.0.0.1`. Access it through an SSH tunnel:

```powershell
ssh -L 8080:127.0.0.1:8080 user@server
```

The Instagram ingress may sit behind Caddy, nginx, or a named Cloudflare Tunnel. Route
only `/webhooks/instagram` to ingress port `8081`; never route the dashboard port.

## OpenClaw placement

`OPENCLAW_BASE_URL` is restricted to loopback. The simplest supported deployment runs
the worker and OpenClaw as host services. If the worker runs in a container, do not
weaken this restriction casually: design a private authenticated container-network
boundary and record it in an ADR before changing configuration validation.

Before enabling automatic replies:

```powershell
rrpp-bridge migrate
rrpp-bridge agent-check
rrpp-bridge status
rrpp-bridge set-mode live
```

Confirm that `agent-check` is structured, there are no queued historical jobs, the
Instagram token is current, and Meta delivers signed webhook events to the stable URL.

## Backup and restore

Create and verify a backup:

```powershell
rrpp-bridge backup create --kind manual
rrpp-bridge backup verify backups\BACKUP.db
```

Daily and monthly backups use SQLite's online backup API. Configure an `age` public
recipient for encrypted off-host exports. Keep the private identity off the server.

Restore is deliberately offline. Stop web, ingress, worker, and maintenance, then use:

```powershell
rrpp-bridge restore backups\BACKUP.db --confirm RESTORE
```

The command creates a pre-restore safety copy and refuses restoration while tracked
services are active.
