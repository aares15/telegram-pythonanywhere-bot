# CLAUDE.md — Project Guide for AI Agents

This file describes the architecture, conventions, and deployment process for this project so an AI agent can work on it without guessing.

---

## What this project is

A Telegram bot template built for students. It runs on Vercel (serverless functions), uses Cerebras (or any OpenAI-compatible API) for AI responses, and Upstash Redis (over the REST API) for per-user conversation memory. It can also run locally via polling with zero hosting, and falls back to a local SQLite file when run on a host with a persistent disk.

**Stack:** Python 3.13 · Flask · pyTelegramBotAPI · OpenAI SDK · Upstash Redis · Vercel

---

## Project structure

```
telegram-pythonanywhere-bot/
├── api/
│   └── index.py          # Flask entrypoint (Vercel function) — /api/webhook, /api/health, /api/tick, cold-start bootstrap
├── bot/
│   ├── __init__.py
│   ├── config.py         # All env vars and constants (edit this to configure the bot)
│   ├── commands.py       # Single source of truth for the command list — drives BOTH /help text and the Telegram "/" menu (set_my_commands)
│   ├── clients.py        # Instantiates bot, ai, store; register_webhook() + register_commands() (do not edit unless adding a client)
│   ├── store.py          # KV-with-TTL: RedisStore (Upstash REST, for serverless/Vercel) + SqliteStore (local dev / persistent disk)
│   ├── ai.py             # ask_ai() — history + dispatch to providers
│   ├── providers.py      # Provider dispatch: OpenAI-compatible (with retry) or HF Gradio space
│   ├── lookup.py         # /lookup — Wikipedia grounding for world topics; Armenian topics answered from expertise + trusted Armenian source links (never Wikipedia)
│   ├── preferences.py    # Per-user provider preference stored via store
│   ├── history.py        # get/save/clear conversation history via store (graceful degradation)
│   ├── rate_limit.py     # Per-user daily message rate limiting via store (graceful degradation)
│   ├── dedupe.py         # Drops repeated update_ids when Telegram retries (graceful degradation)
│   ├── helpers.py        # send_reply(), keep_typing() context manager, should_respond() utilities
│   ├── quiz.py           # Daily Quiz Arena — generation, scoring, leaderboards, daily broadcast
│   └── handlers.py       # All Telegram command and message handlers — add new commands here
├── tests/
│   ├── conftest.py       # Mocks env vars and external packages (telebot, openai, flask)
│   ├── test_ai.py        # ask_ai() orchestration
│   ├── test_providers.py # _call_main() retry, _call_hf() prompt handling, generate() dispatch
│   ├── test_preferences.py
│   ├── test_handlers.py
│   ├── test_helpers.py
│   ├── test_history.py
│   ├── test_rate_limit.py
│   ├── test_dedupe.py
│   ├── test_store.py     # Direct SqliteStore tests (get/set/delete/incr/expire + TTL)
│   ├── test_redis_store.py # RedisStore (Upstash REST) command mapping + PING-on-init
│   ├── test_clients.py   # Store backend selection (_init_redis / _init_store precedence)
│   ├── test_register_webhook.py  # register_webhook() success / no-op / retry / failure
│   ├── test_register_commands.py # command_specs() source of truth + register_commands() (set_my_commands) retry/failure
│   ├── test_lookup.py    # is_armenian_topic() routing, wiki_lookup() parsing, /lookup handler, ask_ai context grounding
│   └── test_webhook.py   # /api/webhook secret check, dedupe, malformed input, _bootstrap_once()
├── .github/
│   └── workflows/
│       ├── ci.yml          # Runs pytest on every push and pull request
│       └── daily-quiz.yml  # Cron → POST /api/tick to broadcast the daily quiz
├── vercel.json           # Vercel config — routes api/index.py, sets maxDuration=60
├── .env.example          # Template for required environment variables
├── run_local.py          # Run the bot locally via polling — for learning + dev
├── Makefile              # install / run / test shortcuts
├── requirements.txt
├── CLAUDE.md             # Agent-readable project guide (this file)
└── README.md             # Student-facing setup guide
```

---

## How the bot works

1. Telegram sends a POST to `https://<your-project>.vercel.app/api/webhook` on every message
2. Vercel routes the request to the Flask app in `api/index.py` (run as a serverless function — a fresh cold start when no warm instance is available)
3. `api/index.py::webhook()` validates the `X-Telegram-Bot-Api-Secret-Token` header (if `WEBHOOK_SECRET` is set) and returns 403 on mismatch — **before** any heavy imports, so a forged/mis-secreted POST never triggers `bot.get_me()` or client init
4. Only after auth does it lazily import `telebot`, `bot.handlers` (registers the `@bot.message_handler` decorators), and `bot.clients.bot`
5. `_bootstrap_once()` runs (see "Cold-start bootstrap" below) to sync the "/" command menu, once per warm instance (it does NOT touch the webhook)
6. The update is de-duplicated by `update_id` (`bot.dedupe.try_acquire`), then handed to `bot.process_new_updates([update])`; pyTelegramBotAPI routes it to the correct handler in `bot/handlers.py`. If processing raises, the dedupe claim is released and the exception propagates (non-200) so Telegram retries
7. For text messages: the handler checks `should_respond()` → checks rate limit → enters `keep_typing()` (a background thread re-sends the Telegram "typing" action every 4s so the indicator stays alive during slow generations) → calls `ask_ai()` → exits context (stops thread) → sends reply
8. `ask_ai()` loads history via the store, prepends the system prompt, dispatches to `generate()` in `bot/providers.py` which calls `_call_main()` (with retry logic) or `_call_hf()` depending on the user's provider preference, then saves updated history

**Critical:** `telebot.TeleBot` must be created with `threaded=False`. Without this, handlers run in threads that can be killed unexpectedly (and on serverless, a thread can be frozen the moment the response returns). `threaded=False` is also fine for local polling (`run_local.py`) — updates just process sequentially in the main thread.

**Cold-start bootstrap.** Vercel has no long-lived "boot" step, so `api/index.py::_bootstrap_once()` is the serverless analog: on the first authenticated `/api/webhook` after a cold start it calls `bot.clients.register_commands()` (syncs the "/" menu via `set_my_commands`, and clears the all-private-chats / all-group-chats scopes so a stale per-scope menu can't shadow it). It runs **at most once per warm instance** — guarded by a module-level `_BOOTSTRAPPED` flag set *before* the attempt so a persistent failure doesn't add latency to every request; a fresh instance retries, so the menu self-heals across deploys. Best-effort, never raises — a failure must not drop the user's message. **It deliberately does NOT (re)register the webhook.** The webhook is set once by hand (`curl setWebhook`, see "Vercel deployment") and must never be overwritten from the request path: a missing/stale `WEBHOOK_URL` would silently re-point Telegram away from the live endpoint and take the bot offline. (`register_webhook()` remains as a helper for manual / persistent-disk use, but nothing in the Vercel path calls it.)

**Local development mode:** `run_local.py` at the repo root runs the same `bot/` modules via `bot.infinity_polling()` instead of the webhook. It auto-loads `.env` with a zero-dependency inline loader, calls `bot.remove_webhook()` to release any registered production webhook, syncs the "/" command menu via `register_commands()`, then blocks on polling. Use this for teaching, prototyping, or iterating without redeploying. Any production webhook registered against the same bot token must be re-registered via `setWebhook` after you stop polling, otherwise production will stay silent.

---

## Environment variables

| Variable | Required | Default | Description |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | — | From @BotFather on Telegram |
| `AI_API_KEY` | Yes | — | API key for the AI provider |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | On Vercel | — | Upstash Redis REST credentials. When both set, the bot uses `RedisStore` — the storage backend for serverless hosts (Vercel) where SQLite can't persist. Takes precedence over `SQLITE_PATH`. The names `KV_REST_API_URL` / `KV_REST_API_TOKEN` are also accepted (older Vercel KV integration). On Vercel, add an Upstash Redis from the Storage/Marketplace tab and it injects these automatically |
| `SQLITE_PATH` | No | — | Absolute path to a SQLite DB file, for **local dev** or a host with a writable persistent disk. Enables history / rate limit / preferences / dedupe / quiz. **Does not work on Vercel** (read-only FS). Ignored when `REDIS_REST_*` is set |
| `AI_BASE_URL` | No | `https://api.cerebras.ai/v1` | Any OpenAI-compatible base URL |
| `AI_MODEL` | No | `gpt-oss-120b` | Model name for the provider |
| `HF_SPACE_ID` | No | — | Hugging Face Gradio space ID (e.g. `edisimon/armgpt-demo`) — enables `/model` command when set |
| `HF_TOKEN` | No | — | HF auth token — only needed if the Gradio space is private or gated |
| `WEBHOOK_SECRET` | No | _auto-generated locally_ | Random string Telegram echoes back in `X-Telegram-Bot-Api-Secret-Token`. Locally / on a persistent disk it's auto-bootstrapped: if the env var is unset, `bot/config.py::_bootstrap_webhook_secret()` generates a 64-hex secret, persists it to `.webhook_secret` (gitignored, mode 0600), and reuses it. **On Vercel set this explicitly as an env var** — the filesystem is read-only, so each instance would otherwise generate nothing (unsigned) and can't share a persisted value. `register_webhook()` ships it to Telegram via `secret_token` |
| `WEBHOOK_URL` | No | — | Target for the `register_webhook()` helper (manual / persistent-disk use). On Vercel the webhook is set **once by hand** and is NOT auto-registered from the request path, so this is not required there. Value would be `https://<your-project>.vercel.app/api/webhook`. Leave unset for local polling |
| `RATE_LIMIT` | No | `250` | Max messages per user per day |
| `ALLOWED_USERS` | No | _open_ | Comma-separated whitelist of usernames (with/without `@`) or numeric user IDs. Empty = everyone allowed. Non-empty = silent drop for non-whitelisted (no rejection reply, no leak of bot existence). Implemented as `func=is_allowed` on every `@bot.message_handler` so telebot never dispatches the handler |
| `HOSTING_LABEL` | No | `Vercel` | Label shown by the `/about` command |
| `TICK_SECRET` | No | — | Enables `/api/tick` (daily-quiz broadcast). Fail-closed: when unset the endpoint returns 403. Set the same value as the `TICK_SECRET` GitHub repo secret used by `.github/workflows/daily-quiz.yml` |
| `WIKI_API_URL` | No | `https://en.wikipedia.org/w/api.php` | Wikipedia API endpoint for `/lookup` (world-history grounding). Point at another language edition (e.g. `hy.wikipedia.org`) to change the source wiki |
| `WIKI_USER_AGENT` | No | `HistoryTeacherBot/1.0 (Telegram teaching bot)` | User-Agent sent to the Wikipedia API — Wikipedia asks clients to identify themselves |

All env vars are read in `bot/config.py`. `.strip()` is called on every value to defend against trailing newlines / whitespace from copy-paste. On Vercel, set these in the project's **Settings → Environment Variables**; `VERCEL_GIT_COMMIT_SHA` is injected automatically and powers `/api/health` + the `/about` version line.

---

## AI provider

The bot uses the OpenAI Python SDK pointed at any OpenAI-compatible endpoint. Switching providers only requires changing `AI_BASE_URL` and `AI_MODEL` (via env vars — no code change needed).

**Known working providers (free tier):**

| Provider | Base URL | Notes |
|---|---|---|
| Cerebras | `https://api.cerebras.ai/v1` | Default. Confirmed working on free tier: `gpt-oss-120b`, `qwen-3-235b-a22b-instruct-2507` |
| Groq | `https://api.groq.com/openai/v1` | 14,400 req/day free. Model: `llama-3.1-8b-instant` |
| Google Gemini | `https://generativelanguage.googleapis.com/v1beta/openai/` | Model: `gemini-2.5-flash` (250 req/day) |

**Cerebras model IDs** (exact strings — wrong format causes 404):
- `gpt-oss-120b` ✓ verified working on free tier. Current default (`bot/config.py`, `.env.example`) — strong reasoning at Cerebras speed
- `qwen-3-235b-a22b-instruct-2507` ✓ verified working on free tier. Strong reasoning and multilingual, but slower per-token and more queue-pressured
- `llama3.1-8b` ✗ deprecated by Cerebras — do not use (was the previous default)

---

## Multi-provider support

The bot can dispatch requests to one of two providers per user. Provider identifiers are **`main`** and **`hf`** — both in code (`VALID_PROVIDERS`, `DEFAULT_PROVIDER`, store values) and in the user-facing `/model` command:

1. **`main`** (default) — any OpenAI-compatible endpoint via `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`. `_call_main()` in `bot/providers.py` has retry logic (3 attempts with exponential backoff: 1s, 2s). Named "main" rather than "openai" to avoid confusing kids who might think it's tied to OpenAI Inc. — the endpoint is *OpenAI-compatible* (a protocol) but the actual provider is usually Cerebras or similar.
2. **`hf`** (optional) — a Hugging Face Gradio space set via `HF_SPACE_ID` (with optional `HF_TOKEN` for private spaces). Called via `gradio_client.Client(...).predict(prompt, length, temperature, top_k, api_name="/generate")`. No retry (HF is slow).

**When `HF_SPACE_ID` is empty, the bot works exactly as a single-provider setup** — the `/model` command is not registered and users always hit the main (OpenAI-compatible) endpoint.

**When `HF_SPACE_ID` is set**, users get a `/model` command:
- `/model` — show current provider + options
- `/model main` — switch to the OpenAI-compatible endpoint
- `/model hf` — switch to the HF space

Preferences are stored via `store` under `provider:{user_id}` (no TTL). If the store is not configured (stateless mode), the bot falls back to `DEFAULT_PROVIDER` (`"main"`).

**HF provider caveats** — the current target (`edisimon/armgpt-demo`, ArmGPT) has:
- Base completion model, not a chat model — `bot/providers.py::_last_user_message` extracts only the most recent user message and passes it as a bare prompt. Chat transcripts (`"User: ...\nAssistant: ..."`) would just confuse it since it was trained on raw Armenian text with no turn structure
- No system prompt support — the system prompt is dropped entirely for HF
- No conversation memory — only the latest user turn is sent
- Hardcoded knobs (`bot/providers.py`) — `HF_LENGTH=100`, `HF_TEMPERATURE=0.6`, `HF_TOP_K=30`. Tuned so generation finishes inside Telegram's ~60s webhook window (and Vercel's 60s `maxDuration`)
- Output is a `(html_output, status_text)` tuple — `_call_hf` takes index 0, strips HTML tags, and strips the echoed prompt prefix if present

To switch to a different HF space, change `HF_SPACE_ID` and confirm the target space exposes a `/generate` API with the same signature, or adapt `_call_hf` in `bot/providers.py`.

**HF Spaces routing.** `gradio_client` first fetches the space config from `huggingface.co`, then routes `predict()` calls to `<space-subdomain>.hf.space`. `bot/providers.py::_call_hf` passes `httpx_kwargs={"timeout": HF_REQUEST_TIMEOUT}` so a slow or unreachable space fails fast instead of wedging the function until Telegram's webhook timeout / Vercel's `maxDuration`.

---

## History source lookup (`/lookup`)

`/lookup <topic>` (alias `/wiki`) answers a history question grounded in a real, citable source instead of the model's memory alone. `bot/lookup.py` routes by topic:

- **World-history topics** → `wiki_lookup()` makes one Wikipedia API call (`generator=search` + `prop=extracts|info`) to fetch the top hit's plain-text intro (capped at `WIKI_MAX_EXTRACT` chars) and canonical URL. The extract is passed to `ask_ai(..., context=...)` as a grounding system message, and the reply cites the article.
- **Armenian-history topics** → deliberately **never** sourced from Wikipedia (a product decision). `is_armenian_topic()` does a transparent case-insensitive substring match against `ARMENIAN_TOPIC_KEYWORDS` (extend that list in `bot/config.py` to widen coverage). Matches are answered from the teacher's own expertise, with a "further reading" block linking `ARMENIAN_SOURCES` (Armeniapedia, 100years100facts.com, Armenian-History.com). Some of those sites block bots (Armeniapedia sits behind a MyWikis anti-bot challenge), so the bot links to them rather than fetching them. The filter errs toward catching Armenian topics — a false positive only costs a Wikipedia citation, whereas a miss would route an Armenian topic to Wikipedia, which is what we're avoiding. A **safety net** re-checks the returned Wikipedia article's title with `is_armenian_topic()` and falls back to the Armenian path if a topic slipped past the keyword filter.

`ask_ai()` has an optional `context` parameter: grounding text sent to the model for one call only, **not** persisted to conversation history (the extract is large and re-fetchable; persisting it would bloat the rolling `MAX_HISTORY` window). The user turn and assistant reply are still saved, so follow-ups work. The HF provider ignores `context` (it only sees the last user message) — consistent with its other limitations.

## Webhook verification

To block spoofed requests, set a random secret and pass it when registering the webhook:

```bash
# Set WEBHOOK_SECRET in the Vercel dashboard (redeploy), then:
curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
  --data-urlencode "url=https://<your-project>.vercel.app/api/webhook" \
  --data-urlencode "secret_token=<your secret>"
```

When `WEBHOOK_SECRET` is set, `api/index.py` checks the `X-Telegram-Bot-Api-Secret-Token` header on every request and returns 403 if it does not match. If the variable is not set, verification is skipped (backwards compatible) and the endpoint logs a one-time warning.

---

## Storage

The bot's storage layer is a thin KV-with-TTL abstraction in `bot/store.py` exposing six operations: `get / set / set_nx / delete / incr / expire`. Two interchangeable backends implement it; `bot/clients.py::store` selects one at import with the precedence **Redis → SQLite → stateless**:

- **`RedisStore` (Upstash REST) — the serverless / Vercel backend.** Selected when `REDIS_REST_URL` + `REDIS_REST_TOKEN` are set (read from `UPSTASH_REDIS_REST_*` or `KV_REST_API_*` — whichever the Vercel/Upstash integration injects). Each op is one Redis command over Upstash's HTTP REST API (`POST` a `["SET","k","v","EX","60"]` array, Bearer auth), so it works where a persistent TCP connection can't and where SQLite can't persist (Vercel's read-only, ephemeral disk). Command semantics were chosen to match `SqliteStore` exactly (`INCR` creates-at-1 and keeps TTL; `SET … NX` = claim-if-absent). `__init__` sends a `PING` so a bad URL/token fails fast and falls back to the next backend.
- **`SqliteStore` — the local-dev / persistent-disk backend.** Selected when `REDIS_REST_*` is absent but `SQLITE_PATH` is set. Opens the DB in WAL mode with `check_same_thread=False`. The schema is a single `kv(key, value, expires_at)` table; expired rows are filtered on read and overwritten on write — no background sweeper, never affects correctness. **Does not work on Vercel** (read-only FS → init fails → falls through to stateless).
- **Stateless mode (neither configured):** `store = None`. Each consumer (`history`, `rate_limit`, `preferences`, `dedupe`, `quiz`) checks for `None` at the top of every function and returns safe defaults: history is empty, rate limiting is skipped, `get_provider` returns `DEFAULT_PROVIDER`, `set_provider` returns `False`, dedupe is a no-op, quiz scoring is skipped. This is the intended Day-1 teaching mode — kids can run the bot locally with only a Telegram token and an AI API key.
- **Graceful degradation under runtime failure:** every store call in the consumer modules is wrapped in try-except. On failure: same fallbacks as stateless mode, plus an error log line.
- **Performance:** SQLite ops are in-process (microseconds); a remote KV over HTTPS is ~20–80ms per round-trip. On Vercel, storage round-trips add to per-request latency, but the AI call still dominates.

---

## Reliability

- **AI retry logic:** `_call_main()` in `bot/providers.py` retries up to 3 attempts (`AI_RETRIES=2` extra retries) with exponential backoff (1s, 2s) before raising. Handles transient network errors and rate-limit spikes. HF is not retried (it's too slow — a retry would blow the per-request budget).
- **Typing indicator during slow calls:** `keep_typing()` in `bot/helpers.py` spawns a daemon thread that re-sends `send_chat_action(chat_id, "typing")` every 4 seconds (Telegram's typing action expires after ~5s). On context exit the thread is signalled and joined with a 2s timeout so the request shuts down cleanly before the function returns. Transient `send_chat_action` errors are caught and logged; the thread keeps looping.

---

## Vercel deployment

The deployment target is `https://<your-project>.vercel.app`. `api/index.py` is a Flask app run as a Vercel serverless function; `vercel.json` routes to it and sets `maxDuration=60` (matching Telegram's ~60s webhook budget). There is **no long-lived worker** — each request may hit a fresh cold start.

**One-time setup:**
1. Import the GitHub repo at [vercel.com](https://vercel.com). Vercel auto-detects the Python function and `vercel.json`.
2. In **Settings → Environment Variables**, set at least `TELEGRAM_BOT_TOKEN`, `AI_API_KEY`, `WEBHOOK_SECRET`, and `WEBHOOK_URL=https://<your-project>.vercel.app/api/webhook`. For persistent memory add an **Upstash Redis** from the Storage/Marketplace tab (it injects `UPSTASH_REDIS_REST_URL` / `_TOKEN` — or the `KV_REST_API_*` names — automatically). Optionally set `TICK_SECRET` for the daily quiz.
3. Deploy (any push to `main` deploys automatically thereafter).
4. **Register the webhook once** (no request reaches the function before this exists):
   ```bash
   curl -X POST "https://api.telegram.org/bot<TOKEN>/setWebhook" \
     --data-urlencode "url=https://<your-project>.vercel.app/api/webhook" \
     --data-urlencode "secret_token=<WEBHOOK_SECRET>"
   ```

**Auto-deploy on push.** Vercel's Git integration builds and deploys on every push to `main` (and creates preview deployments for other branches / PRs). There is no bot-side deploy endpoint or GitHub deploy workflow — Vercel owns deployment. Keep the bot token configured only on the **production** deployment so a preview build never clobbers the production webhook.

**Cold-start bootstrap.** On the first authenticated `/api/webhook` after each cold start, `_bootstrap_once()` syncs the "/" command menu (`register_commands()` → `set_my_commands` + scope cleanup). Once per warm instance, best-effort, never raises, so the "/" menu self-heals across deploys. It **does not** register the webhook — the webhook is set once by hand (`setWebhook` above) and must never be overwritten from the request path (a stale `WEBHOOK_URL` would take the bot offline). See "Cold-start bootstrap" under "How the bot works" for the full rationale.

**Verifying a deploy is live.** `/api/health` returns `OK <short-sha>`, where the SHA is `VERCEL_GIT_COMMIT_SHA` captured at cold start (git fallback locally). Hit `https://<your-project>.vercel.app/api/health` and confirm it reports the commit you just pushed. `/about` shows the same SHA on its `Version` line.

**Daily quiz.** `.github/workflows/daily-quiz.yml` runs on a cron and `POST`s `/api/tick` with an `X-Tick-Secret` header. Set two GitHub repo secrets: `TICK_URL` (`https://<your-project>.vercel.app/api/tick`) and `TICK_SECRET` (matching the `TICK_SECRET` env var in Vercel). `/api/tick` is fail-closed (403 when `TICK_SECRET` is unset) and idempotent per day (an atomic once-per-day store claim), so retries are safe. Vercel Cron is a viable alternative, but GitHub Actions is used so the schedule works regardless of Vercel plan.

**Auto webhook-secret bootstrap (local / persistent disk only).** If `WEBHOOK_SECRET` is unset, `bot/config.py::_bootstrap_webhook_secret()` generates a 64-hex-char secret and persists it in `.webhook_secret` at the project root (gitignored, chmod 0600), reused on later runs. This is convenient locally, but **on Vercel the filesystem is read-only**, so the write fails and the bot runs unsigned — always set `WEBHOOK_SECRET` as an env var on Vercel so every instance verifies against the same value.

**Critical Vercel-specific constraints:**
- **Read-only, ephemeral filesystem.** State cannot live on disk — use Upstash Redis (`RedisStore`) for history / rate limit / preferences / dedupe / quiz. `SQLITE_PATH` and the `.webhook_secret` file do not persist on Vercel; `WEBHOOK_SECRET` must be an env var.
- **60s function budget.** `vercel.json` sets `maxDuration=60`. Slow paths (HF generation) are timed out below this so the function returns before Telegram/Vercel cut it off.
- **Cold starts, no boot hook.** Module-level code re-runs per cold start and there's no persistent "boot"; the "/" command-menu sync therefore happens via `_bootstrap_once()` on the first request per instance (see above). The webhook is NOT auto-registered — set it once by hand.
- **No background work after the response.** An instance can be frozen the moment the HTTP response returns, so anything that must complete (e.g. sending the reply) has to finish within the request; don't defer work to a thread that outlives the response.

---

## Known gotchas

- **`threaded=False` is required** — see "How the bot works" above
- **Cerebras model names** — exact ID strings are required (e.g. `gpt-oss-120b`); a wrong format causes a 404. Check https://inference-docs.cerebras.ai/models for current IDs
- **Telegram 4096 char limit** — `send_reply()` in `bot/helpers.py` handles splitting automatically
- **Group chats** — `should_respond()` returns `True` for all messages, so the bot replies to every message in any chat it's in. If you need mention-gated or reply-gated behavior in groups, reintroduce it in `bot/helpers.py::should_respond`. The handler still strips `@<bot_username>` from text before sending to the AI
- **Webhook secret must match** — if `WEBHOOK_SECRET` is set, the same value must be passed as `secret_token` in `setWebhook`. Mismatch causes all updates to return 403 and the bot goes silent. On Vercel, `WEBHOOK_SECRET` must be an env var (the `.webhook_secret` file can't persist there)
- **Command list is single-sourced** — both the `/help` text and the Telegram "/" menu come from `bot/commands.py::command_specs()`. Add/remove a command there (and its handler in `bot/handlers.py`); the menu re-syncs via `register_commands()` on the next cold start. Telegram caches the "/" menu client-side briefly — restart the app to force a refresh
- **`/api/health` body is `OK <short-sha>`** — a truthful "which commit is live?" probe (SHA from `VERCEL_GIT_COMMIT_SHA`, git fallback). Scripts should check the HTTP status or the `OK ` prefix, never exact body equality
- **Formatter strips unused imports between Edit calls** — if you do a two-step rewrite (add an import in one Edit, use it in the next), the formatter may remove the "unused" import between calls. Combine them into one Edit, or re-add the import after the second Edit
- **Windows `make.ps1 install` + the Microsoft Store Python stub** — typing `py`/`python` on Windows can hit a Store "app execution alias": a 0-byte stub under `%LOCALAPPDATA%\Microsoft\WindowsApps` that exits 0 and creates nothing. So `Get-Command py` succeeding (or `py -m venv` returning 0) proves nothing. `make.ps1`'s `New-RepoVenv` tries `py`→`python`→`python3` and keeps the first whose run actually produces `.venv\Scripts\python.exe`; don't "simplify" it back to a single `Get-Command` check. A student whose `python --version` works can still hit the old failure because the script tried `py` (the stub) first
