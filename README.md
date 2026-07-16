# RRPP Agent Bridge

`rrpp-agent-bridge` is a local-first, auditable bridge for customer communication channels. It persists inbound events, processes them asynchronously, produces reviewable drafts, and never sends an external message in the current version.

The project uses Python 3.12, SQLite, Google OAuth libraries for the read-only Gmail connector, and optional Docker/Gunicorn tooling for deployment.

## Safety model

- The default execution mode is `shadow`.
- Draft approval marks a response as prepared; it does not send it.
- Gmail is read-only.
- Instagram is inbound-only and validates Meta webhook signatures.
- The dashboard stays private on `127.0.0.1:8080`.
- Inbound text is untrusted data, never operational instruction.

## First-time setup

Requires Python 3.12 or newer. From PowerShell in the repository root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
Copy-Item .env.example .env
```

Fill the dashboard credentials in the ignored `.env`. Keep `RRPP_MODE=shadow` while testing. Do not commit `.env`, OAuth files, tokens, or tunnel credentials.

If `.env` already exists, do not overwrite it with the example file.

## Local dashboard test

For a reliable first test, use separate terminals. This avoids starting optional connectors that may not be configured yet.

Terminal 1: migrate and start the private dashboard.

```powershell
.\.venv\Scripts\python.exe -m rrpp_bridge migrate
.\.venv\Scripts\python.exe -m rrpp_bridge web
```

Terminal 2: start the worker.

```powershell
.\.venv\Scripts\python.exe -m rrpp_bridge worker
```

Open `http://127.0.0.1:8080/login` and authenticate with `RRPP_DASHBOARD_USER` and `RRPP_DASHBOARD_PASSWORD` from `.env`.

Use the local simulator in the dashboard to submit a test message. The expected flow is:

```text
simulator -> event -> job -> worker -> draft -> human review
```

Check `Converses`, `Cua de revisió`, `Activitat`, and `Sistema`. In `shadow` mode, every execution remains suppressed and simulated.

`scripts/run-local.ps1` is a convenience command for the dashboard, worker, maintenance, and any locally authorized Gmail poller. It does not start the Instagram webhook. Prefer the separate terminals above while diagnosing or testing connectors.

## Instagram inbound test

Instagram support is inbound-only. It receives signed DM webhooks, creates/updates conversations, and creates reviewable drafts. It has no reply sender, proactive messaging, or outbound Graph API client.

Before starting it, configure these ignored `.env` values:

```text
RRPP_INSTAGRAM_ENABLED=true
INSTAGRAM_VERIFY_TOKEN=your-private-verification-value
INSTAGRAM_APP_SECRET=Meta-App-Secret
INSTAGRAM_BUSINESS_ACCOUNT_ID=Instagram-business-account-ID
INSTAGRAM_PAGE_ACCESS_TOKEN=reserved-for-future-official-use
```

`INSTAGRAM_PAGE_ACCESS_TOKEN` is not used by the current inbound connector. Never put any of these values in source control, screenshots, or documentation.

Terminal 3: start the dedicated webhook service.

```powershell
.\.venv\Scripts\python.exe -m rrpp_bridge instagram-webhook
```

It listens only on `http://127.0.0.1:8081`. A direct request without Meta verification returns `403`, which is expected.

Terminal 4: expose only the webhook port for local Meta testing.

```powershell
cloudflared tunnel --url http://127.0.0.1:8081
```

Configure the public URL supplied by Cloudflare in Meta with this exact path:

```text
https://YOUR-CLOUDFLARE-HOST/webhooks/instagram
```

Do not tunnel port `8080` and do not expose the dashboard. For a stable Meta configuration, use a named Cloudflare tunnel and a fixed hostname rather than a temporary quick tunnel.

In the dashboard, create a venue route with channel `Instagram` and the exact configured Instagram business account ID as recipient. Without that explicit rule, incoming conversations remain `Sense assignar` by design.

## Gmail read-only connector

Gmail is optional and uses only the `gmail.readonly` OAuth scope. Place the OAuth client at `secrets/gmail-oauth-client.json`, then authorize:

```powershell
.\.venv\Scripts\rrpp-bridge.exe gmail-auth
.\.venv\Scripts\rrpp-bridge.exe gmail-poll --once
```

It can read Inbox messages but cannot send, delete, archive, label, or mark mail as read. If it reports `RefreshError`, authorize again and restart the poller:

```powershell
.\.venv\Scripts\rrpp-bridge.exe gmail-auth
```

## Operations and tests

Useful commands:

```powershell
.\.venv\Scripts\rrpp-bridge.exe status
.\.venv\Scripts\rrpp-bridge.exe worker --once
.\.venv\Scripts\rrpp-bridge.exe maintenance --once
.\.venv\Scripts\rrpp-bridge.exe backup create --kind manual
.\.venv\Scripts\rrpp-bridge.exe backup verify backups\BACKUP.db
```

Run the test suite with:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

For Docker/VPS deployment, encrypted backups, SSH access, and the production Instagram ingress boundary, read [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md). The persistent architecture and security rules are in [docs/agent-guide/](docs/agent-guide/README.md).
