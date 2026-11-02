# Instagram AI Assistant

AI-powered comment and DM automation for an Instagram **Creator** account, built only on the
**official Meta Instagram API** (Instagram API with Instagram Login) and webhooks. No unofficial
login, scraping or password-based libraries are used.

> **Status: Step 1 — project skeleton.** The app runs, loads configuration and exposes `/health`.
> Webhooks, AI replies, DMs and the dashboard are added in later steps.

## Planned features

| Feature | How (official API) | Step |
| --- | --- | --- |
| Detect new comments on posts/reels | `comments` webhook | 2 |
| Understand comments (intent, language, spam) | OpenAI (model set via `OPENAI_MODEL`) | 3 |
| Natural replies in Hindi / English / Hinglish | OpenAI | 3 |
| Post replies to comments | `POST /{comment-id}/replies` | 4 |
| DM a commenter | Private Reply (`recipient.comment_id`) — 1 per comment, within 7 days | 4 |
| Image DMs | `attachment.type=image` with a public HTTPS URL | 4 |
| Dashboard: review queue, switches, logs | FastAPI + HTML | 5 |
| Docker + deployment guide | Docker / docker-compose | 6 |

## Safety defaults

| Variable | Default | Meaning |
| --- | --- | --- |
| `DRY_RUN` | `true` | Never call Instagram write APIs. Turning this off requires real credentials. |
| `REVIEW_QUEUE_ENABLED` | `true` | AI replies wait for your approval. |
| `AUTO_REPLY_ENABLED` | `false` | Post replies automatically. Can only be enabled when the review queue is off. |
| `AUTO_DM_ENABLED` | `false` | Send DMs automatically. |

## Quick start (local, no credentials needed)

Requires Python 3.11+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env           # leave secrets empty for now
uvicorn app.main:app --reload
# open http://127.0.0.1:8000/health
```

Run checks:

```bash
ruff check . && ruff format --check .
pytest
```

## Docker

```bash
cp .env.example .env
docker compose up --build
# http://127.0.0.1:8000/health
```

SQLite data is stored in the `app-data` Docker volume. A single small container keeps hosting
costs low.

## Configuration and secrets

All settings come from environment variables (or a local `.env` file). See
[`.env.example`](.env.example) for the full list.

- `.env` is git-ignored and excluded from the Docker image — never commit it.
- Secrets (`META_APP_SECRET`, `INSTAGRAM_ACCESS_TOKEN`, `OPENAI_API_KEY`, `DASHBOARD_PASSWORD`,
  `SESSION_SECRET_KEY`, …) are masked in logs and never returned by any endpoint.
- In production, set them through your host's secret manager instead of a file.

## Meta / Instagram setup

Not required yet. When you are ready to connect a real account, follow
[`docs/meta-setup.md`](docs/meta-setup.md).

## Project layout

```
app/
  config.py   # environment-based settings + safety validation
  main.py     # FastAPI app (/health)
tests/        # pytest suite (no network, no real credentials)
docs/         # setup guides
```
