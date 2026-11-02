# Instagram AI Assistant

AI-powered comment and DM automation for an Instagram **Creator** account, built only on the
**official Meta Instagram API** (Instagram API with Instagram Login) and webhooks. No unofficial
login, scraping or password-based libraries are used.

> **Status: Step 3 — AI reply drafts.** Signed webhooks are stored, and each new comment is
> classified and given a draft reply in the review queue. Nothing is posted or sent yet; sending
> and the dashboard come in later steps.

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

## Webhook receiver

| Endpoint | Purpose |
| --- | --- |
| `GET /webhook` | Meta verification handshake: returns `hub.challenge` only if `hub.verify_token` matches `META_WEBHOOK_VERIFY_TOKEN`. |
| `POST /webhook` | Receives events. Rejects (403, nothing stored) unless `X-Hub-Signature-256` is a valid HMAC-SHA256 of the raw body keyed by `META_APP_SECRET`. |

Stored data (`app/models.py`):

- `webhook_events` — audit log of every validly signed delivery.
- `comments` — one row per comment (deduplicated by Instagram comment ID). Comments from your own
  account are stored with status `ignored_self` so the bot never replies to itself.
- `messages` — incoming DMs (deduplicated by message ID; echoes of your own messages are flagged),
  used later for Instagram's 24-hour messaging window.

### Try it locally with fake data (no Meta account needed)

```bash
# in .env — fake values only
META_APP_SECRET=local-fake-secret
META_WEBHOOK_VERIFY_TOKEN=local-fake-token

uvicorn app.main:app --reload          # terminal 1
python scripts/send_test_webhook.py --text "Price kya hai? 😍"     # terminal 2
python scripts/send_test_webhook.py --dm --text "Hi, details please"
python scripts/show_recent.py          # comments, AI drafts, messages
```

`send_test_webhook.py` signs a sample payload with your local `META_APP_SECRET` and only posts to
`localhost`.

## AI reply drafts (review queue)

New comments are processed in the background after the webhook responds (`app/ai/`):

1. **Free pre-filter** — empty, emoji-only, links, repeated characters, `BLOCKED_WORDS` →
   skipped without an AI call.
2. **One OpenAI call** (`OPENAI_MODEL`, strict JSON schema) returns `intent`, `language`
   (hindi / english / hinglish), `should_reply`, `reply_text`, `suggest_dm`, `dm_text`,
   `confidence`. The comment is passed as JSON data and the prompt forbids following
   instructions inside it.
3. **Rules enforced in code** — spam/abusive never get a reply; replies must be non-empty,
   ≤ 300 chars and link-free; DMs are only suggested for price/detail requests or questions;
   low-confidence drafts are flagged.
4. Saved to `reply_drafts` with status `pending_review` / `skipped` / `failed`.

Cost controls: one short call per comment, `OPENAI_MAX_OUTPUT_TOKENS`, and `OPENAI_DAILY_LIMIT`
(comments over the limit stay queued; run `python scripts/process_pending.py` later).
Without `OPENAI_API_KEY` a free offline stub generates simple drafts so the whole flow works
locally. Tests never call OpenAI.

## Meta / Instagram setup

Not required yet. When you are ready to connect a real account, follow
[`docs/meta-setup.md`](docs/meta-setup.md).

## Project layout

```
app/
  config.py   # environment-based settings + safety validation
  db.py       # SQLAlchemy engine/session
  models.py   # webhook_events, comments, messages
  security.py # webhook signature + verify-token checks
  webhooks.py # GET/POST /webhook
  ai/         # prefilter, prompts, OpenAI/offline generators, draft pipeline
  main.py     # FastAPI app (/health)
scripts/      # local dev helpers (fake signed webhooks, show stored data)
tests/        # pytest suite (no network, no real credentials)
docs/         # setup guides
```
