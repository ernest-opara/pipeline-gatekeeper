# Pipeline Gatekeeper

**Approve production deploys from your phone — with an AI risk briefing.**

When your pipeline is ready to ship, Gatekeeper texts you a short brief: what changed, who changed it, and a Claude-generated risk assessment. You reply `approve`, `approve 10` (canary), or `rollback`. The deploy proceeds accordingly.

## Features

- **AI pre-flight risk summary** — Claude reads the diff and texts you a one-line risk call (LOW / MEDIUM / HIGH) before you approve.
- **Rich approval context** — commit message, files changed, PR title, and a link to the GitHub run come in the message.
- **Canary rollouts** — reply `approve 10` to ship to 10% of traffic, `approve 100` for a full deploy.
- **Approver allowlist** — only phone numbers on the allowlist can approve.
- **Business-hours window** — deploys outside the window require a `force approve` override.
- **`status` command** — text `status` to see every pending deploy.
- **Redis-ready state** — set `REDIS_URL` for persistence across restarts; otherwise state is in-memory.

## How it works

```
  push to main
       │
       ▼
  GitHub Actions runs tests
       │
       ▼
  /deploy/register ──▶ Claude summarizes risk ──▶ iMessage:
                                                  "MEDIUM RISK: touches auth middleware.
                                                   approve / approve 10 / rollback?"
       │                                                │
       │                                                ▼
       │                                      you reply from your phone
       │                                                │
       ▼                                                ▼
  poll /deploy/status  ◄────────────── webhook updates state
       │
       ▼
  deploy runs at the approved canary %
```

## Setup

### 1. Install

```bash
git clone <this repo>
cd pipeline-gatekeeper
pip install -r requirements.txt
```

### 2. Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `LINQ_API_TOKEN` | yes | Linq API token |
| `LINQ_PHONE_NUMBER` | yes | Your Linq sandbox line |
| `NOTIFY_NUMBER` | yes | Phone number to alert |
| `ANTHROPIC_API_KEY` | recommended | Enables AI risk summary |
| `LINQ_WEBHOOK_SECRET` | recommended | HMAC secret for webhook signature verification |
| `APPROVER_NUMBERS` | optional | Comma-separated allowlist of approver phone numbers |
| `DEPLOY_WINDOW_START_HOUR` | optional | Start of deploy window (0–23) |
| `DEPLOY_WINDOW_END_HOUR` | optional | End of deploy window (0–23) |
| `DEPLOY_WINDOW_TZ` | optional | IANA timezone for the window (e.g. `America/New_York`) |
| `REDIS_URL` | optional | Redis connection string for durable state |

### 3. Run the server

```bash
uvicorn server:app --port 8000
```

Swagger docs at `http://127.0.0.1:8000/docs`.

### 4. Expose publicly

For local dev: `ngrok http 8000`. For production: deploy to any Python host (Fly.io, Railway, Render) and set `REDIS_URL` so state survives restarts.

### 5. Point Linq at the server

Set the inbound webhook URL in Linq to `https://<host>/webhook/linq`.

### 6. Configure GitHub Actions

Add repo secrets: `LINQ_API_TOKEN`, `LINQ_PHONE_NUMBER`, `NOTIFY_NUMBER`, `GATE_SERVER_URL`. An example workflow ships in `.github/workflows/deploy.yml`.

## Reply reference

| Reply | Effect |
|---|---|
| `approve` | Full deploy (100%) |
| `approve 10` | Canary to 10% (also `25`, `50`, `100`) |
| `rollback` | Cancel the deploy |
| `status` | List every pending deploy |
| `force approve` | Override the deploy-window check |
| `<deploy-id> approve` | Disambiguate when multiple deploys are pending |

The server reacts to your reply (👍 on receipt, ✅ on approve, ❌ on rollback) for visual confirmation.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhook/linq` | Linq inbound message webhook |
| `POST` | `/deploy/register` | Register a pending deploy (GitHub Actions) |
| `GET`  | `/deploy/status/{deploy_id}` | Poll deploy state + canary percent |

## Security

- Webhook signatures verified with HMAC-SHA256 when `LINQ_WEBHOOK_SECRET` is set.
- Replayed webhooks older than 5 minutes are rejected.
- Approver allowlist defends against anyone texting the Linq number.

## License

MIT
