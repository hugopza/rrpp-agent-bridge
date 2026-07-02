# Private VPS Deployment

This deployment keeps the dashboard on the VPS loopback interface. Do not expose port 8080 through the firewall or a public reverse proxy.

## Prerequisites

- A single Linux VPS with Docker Engine and Docker Compose.
- SSH key access to the VPS.
- Repository checkout owned by the deployment user.
- An ignored `.env.docker` created from `.env.docker.example` with new random dashboard credentials.
- Host directories `var/`, `backups/`, `backup-export/`, and `runtime-secrets/` writable by container UID 10001.

Create an `age` identity on the owner's trusted device, not on the VPS:

```text
age-keygen -o rrpp-backup.agekey
```

Put only the printed public recipient in `RRPP_BACKUP_AGE_RECIPIENT`. Keep the `.agekey` file off the VPS and move encrypted files from `backup-export/` to separate storage regularly.

## Gmail Files

- Mount the OAuth client as `secrets/gmail-oauth-client.json` in read-only mode.
- Put the existing renewable token at `runtime-secrets/gmail-token.json`.
- Never copy either file into the image or Git.

## First Start

```text
docker compose --env-file .env.docker --profile tools run --rm migrate
docker compose --env-file .env.docker build
docker compose --env-file .env.docker --profile gmail up -d
docker compose --env-file .env.docker ps
docker compose --env-file .env.docker run --rm worker rrpp-bridge backup create --kind manual
```

Omit `--profile gmail` until the Gmail files exist. Normal services refuse an outdated schema; always run the explicit migration command before deploying a new version.

## Private Access

From the owner's computer:

```text
ssh -L 8080:127.0.0.1:8080 user@vps-address
```

Open `http://127.0.0.1:8080`. Keep the SSH session open while using the dashboard.

## Restore

Stop all database users first:

```text
docker compose --env-file .env.docker --profile gmail stop web worker gmail maintenance
```

Confirm the services are stopped in `docker compose ps`. If a process was killed rather than stopped gracefully, wait 60 seconds for its heartbeat lease to expire. Then run:

```text
docker compose --env-file .env.docker run --rm worker rrpp-bridge restore /app/backups/BACKUP.db --confirm RESTORE
docker compose --env-file .env.docker --profile gmail up -d
```

For an encrypted export, temporarily mount the owner's identity read-only and pass `--identity`:

```text
docker compose --env-file .env.docker run --rm -v /trusted/temporary/rrpp-backup.agekey:/run/secrets/backup.agekey:ro worker rrpp-bridge restore /app/backup-export/BACKUP.db.age --confirm RESTORE --identity /run/secrets/backup.agekey
```

Do not copy the identity into a persistent VPS directory. The restore command validates the source, creates a `pre_restore` safety backup, restores with SQLite's backup API, checks integrity, and rolls back automatically if validation fails.

## Instagram inbound webhook

Instagram requires a professional Instagram account, a linked Facebook Page, a Meta Developer App, the applicable messaging/webhook product, webhook subscriptions, permissions, and any review required for accounts outside the app's test roles. Configure `INSTAGRAM_VERIFY_TOKEN`, `INSTAGRAM_APP_SECRET`, and `INSTAGRAM_BUSINESS_ACCOUNT_ID`; `INSTAGRAM_PAGE_ACCESS_TOKEN` is reserved for future official API use and is not used for inbound processing.

Enable the connector only after configuration is complete:

```text
RRPP_INSTAGRAM_ENABLED=true
docker compose --env-file .env.docker --profile instagram up -d instagram
```

The container binds the ingress service to VPS loopback port 8081. Terminate TLS in a reverse proxy and forward only the exact public path `/webhooks/instagram` to `http://127.0.0.1:8081/webhooks/instagram`. Do not proxy port 8080 or any dashboard path. Register the resulting HTTPS URL and verify token in the Meta App dashboard, then subscribe only to the DM webhook fields needed by the connector.

This version accepts signed inbound text events, creates reviewable drafts, and never sends a reply. Attachments, profile lookup, proactive messages, cold outreach, and promotional sending are not implemented.

## Routine Checks

- Review `Sistema` for stale services, errors, dead letters, and backup age.
- Confirm a verified daily backup exists and encrypted exports are moved off-host.
- Test restoration on a non-production copy after deployment changes.
- Use `docker compose ... logs SERVICE` only for sanitized process diagnostics; message bodies and credentials must not be logged.
