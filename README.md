# Pipeline Gatekeeper

**Approve production deploys from your phone via iMessage.**

Pipeline Gatekeeper is a lightweight approval gate for GitHub Actions. When your pipeline is ready to ship, it texts you. You reply `approve` or `rollback`. The deploy proceeds — or doesn't.

No dashboards. No Slack bots. Just a text message and a one-word reply.

## Why

Production deploys deserve a human in the loop, but breaking out the laptop to click a button in a CI dashboard is friction you'll eventually skip. An iMessage you can answer from anywhere is friction you won't.

## How it works

```
  push to main
       │
       ▼
  GitHub Actions runs tests
       │
       ▼
  Gatekeeper texts you  ──────►  📱  "Deploy abc123 ready. approve / rollback?"
       │                                       │
       │                                       ▼
       │                            you reply "approve"
       │                                       │
       ▼                                       ▼
  Actions polls /deploy/status  ◄────  webhook updates state
       │
       ▼
  Deploy runs (or is cancelled)
```

Under the hood:

1. A FastAPI server receives inbound iMessage webhooks from [Linq](https://linq.com) and exposes a status endpoint.
2. A GitHub Actions step registers each deploy, then polls for your decision.
3. You reply from your phone. The server updates state. Actions sees it on the next poll.

## Requirements

- Python 3.10+
- A [Linq](https://linq.com) account (for iMessage delivery) with an API token and sandbox line
- A publicly reachable URL for the webhook server (ngrok for local dev, or a host like Fly.io / Railway for production)
- A GitHub repo where you want the gate installed

## Installation

### 1. Install and run the server

```bash
git clone <this repo>
cd pipeline-gatekeeper
pip install -r requirements.txt
```

Set the required environment variables:

| Variable | Description |
|---|---|
| `LINQ_API_TOKEN` | Your Linq API token |
| `LINQ_PHONE_NUMBER` | Your Linq sandbox line (e.g. `+14158707772`) |
| `NOTIFY_NUMBER` | The phone number that should receive approval requests |
| `LINQ_WEBHOOK_SECRET` | (Optional but recommended) HMAC secret for verifying inbound webhooks |

Start the server:

```bash
uvicorn server:app --port 8000
```

Interactive API docs are available at `http://127.0.0.1:8000/docs`.

### 2. Expose the server publicly

For local development:

```bash
ngrok http 8000
```

For production, deploy `server.py` to any Python host. Note that the default in-memory state store (`deploy_states`) does not survive restarts — swap it for Redis or a database before going live.

### 3. Configure Linq

In the Linq dashboard, set the inbound webhook URL to:

```
https://<your-public-host>/webhook/linq
```

### 4. Configure GitHub Actions

Add the following repository secrets:

| Secret | Value |
|---|---|
| `LINQ_API_TOKEN` | Same token as above |
| `LINQ_PHONE_NUMBER` | Same number as above |
| `NOTIFY_NUMBER` | Who to text |
| `GATE_SERVER_URL` | Your public server URL (no trailing slash) |

A workflow example is included in `.github/workflows/`.

## Usage

When a deploy is pending, you'll receive an iMessage like:

> Deploy `deploy-1234567890-42` is ready.
> Repo: `acme/api` · Branch: `main` · Actor: `@ernest`
> Reply `approve` or `rollback`.

Reply with one of:

- **`approve`** — the deploy proceeds
- **`rollback`** — the workflow is cancelled

If more than one deploy is pending at once, prefix your reply with the deploy ID:

```
deploy-1234567890-42 approve
```

The server reacts to your message (👍 on receipt, ✅ on approve, ❌ on rollback) so you get visual confirmation without waiting for the reply text.

## API

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/webhook/linq` | Inbound iMessage webhook (called by Linq) |
| `POST` | `/deploy/register` | Register a new pending deploy (called by GitHub Actions) |
| `GET`  | `/deploy/status/{deploy_id}` | Poll the current state of a deploy |

Full schemas are available at `/docs`.

## Security

- Webhook requests are verified with HMAC-SHA256 when `LINQ_WEBHOOK_SECRET` is set. Always set it in production.
- Replayed webhooks older than 5 minutes are rejected.
- Only inbound messages are acted on; the server ignores its own outbound traffic.

## License

MIT
