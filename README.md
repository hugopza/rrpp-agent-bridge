# RRPP Agent Bridge

`rrpp-agent-bridge` is a local-first, auditable bridge for customer communication channels. It persists inbound events, processes them asynchronously, produces reviewable drafts, and never sends an external message in the current version.

The project uses Python 3.12, SQLite, Google OAuth libraries for the read-only Gmail connector, and optional Docker/Gunicorn tooling for deployment.

## Safety model

- The default execution mode is `shadow`.
- Draft approval marks a response as prepared; it does not send it.
- Gmail is read-only.
- Instagram is inbound-only and validates Meta webhook signatures.
- OpenClaw can generate drafts locally but has no channel-delivery capability.
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

## OpenClaw draft generation

OpenClaw is optional and replaces the deterministic placeholder as the worker's draft provider. It does not send messages. Every result is schema-validated, passed through policy, stored as a pending draft, and suppressed by `shadow` mode. If the Gateway times out or returns invalid output, the job remains durable and the dashboard receives a manual-review escalation.

The Gateway connection is local, but the configured OpenClaw model may be a remote provider. With ChatGPT/OpenAI, bounded message content and venue context leave the laptop for model processing. Confirm the provider's privacy, retention, and account terms before processing real customer DMs.

OpenClaw also maintains conversation sessions and local logs according to its own configuration. Review their retention and access permissions before production; the bridge audit log deliberately does not copy prompts, message bodies, venue knowledge, or provider responses.

Prerequisites:

1. Run an OpenClaw Gateway only on `127.0.0.1:18789` with token authentication.
2. Enable its OpenAI-compatible Chat Completions HTTP endpoint.
3. Create the OpenClaw agent ID `rrpp` and disable its tools and channel delivery.
4. Use a dedicated random Gateway token; never give OpenClaw Instagram or Gmail credentials.

On this Windows workstation, use the `.cmd` wrapper because PowerShell blocks the npm `.ps1` launcher. Check or start an already configured Gateway with:

```powershell
cmd /c openclaw gateway status
cmd /c openclaw gateway run --bind loopback --port 18789 --auth token
```

Configure the token in OpenClaw's environment or secret configuration before starting it; do not pass a real token on the command line. Create the isolated agent when it does not exist with `cmd /c openclaw agents add rrpp`, then configure that agent with no bindings, `tools.deny=["*"]`, and elevated tools disabled.

Add these values to the ignored `.env`:

```text
OPENCLAW_ENABLED=true
OPENCLAW_BASE_URL=http://127.0.0.1:18789
OPENCLAW_AGENT_ID=rrpp
OPENCLAW_TIMEOUT_SECONDS=60
OPENCLAW_GATEWAY_TOKEN=your-local-gateway-token
```

The Gateway token must match the token configured in OpenClaw. `OPENCLAW_AGENT_NAME=rrpp` is accepted as a compatibility alias, but `OPENCLAW_AGENT_ID` is the canonical key.

After changing these values, migrate once and restart the worker because it selects its provider at process startup:

```powershell
.\.venv\Scripts\python.exe -m rrpp_bridge migrate
.\.venv\Scripts\python.exe -m rrpp_bridge worker
```

In `Discoteques`, fill **Informació verificada per al bot** with confirmed venue facts and configure an exact recipient route. Then submit a simulator message or send an Instagram DM. The expected flow is:

```text
inbound message -> durable job -> worker -> OpenClaw rrpp -> validated draft -> pending human review
```

The worker sends bounded recent history and venue knowledge, not channel credentials. An unassigned conversation has no venue knowledge and must not infer a venue from message text.

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
