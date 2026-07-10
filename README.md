# Telegram Bot — Vercel Starter Template

A minimal Python Telegram bot that deploys to Vercel (free Hobby tier) with persistent conversation memory in Upstash Redis and AI powered by Cerebras (defaults to `gpt-oss-120b` — strong reasoning at Cerebras speed; `qwen-3-235b-a22b-instruct-2507` is also available).

**Stack:** Python · Flask · pyTelegramBotAPI · OpenAI SDK · Upstash Redis · Vercel

**All services used are free. No credit card required.**

> **Live demo:** <a href="https://t.me/tele_pythonanywhere_bot" target="_blank"><img src="https://img.shields.io/badge/Chat%20on-Telegram-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white" alt="Chat on Telegram"/></a>

---

## What you will need

| Service | Purpose | Needed for | Free tier |
|---|---|---|---|
| [Telegram](https://telegram.org) | The bot platform | Everything | Always free |
| [Cerebras](https://cloud.cerebras.ai) | AI API — `gpt-oss-120b` (default), `qwen-3-235b-a22b-instruct-2507`, and more | Everything | 1M tokens/day, 30 req/min |
| [GitHub](https://github.com) | Source code | Everything | Always free |
| [Vercel](https://vercel.com) | Hosting the bot (serverless) | Deployment | Free Hobby tier, auto-deploys on push |
| [Upstash Redis](https://upstash.com) | Persistent memory (via Vercel Marketplace) | Deployment (memory) | Free tier, ~10k commands/day |

> **Age requirements (check before signing up).** Each of the services above has a minimum age in its Terms of Service. As a rule of thumb: **Telegram, Cerebras, GitHub, Vercel, Upstash, Hugging Face** are 13+ globally (16+ in the EU/UK for some, due to GDPR). If you're under 13, or in a region where the minimum is 16+, the safest path is to walk through the signup steps with a parent or teacher — they create the accounts and share the API keys with you. You can still do all of the coding, testing, and deployment work yourself.

---

# Part 1 — Run it on your laptop

You can have the bot replying to your messages on Telegram in about 10 minutes without touching Vercel or any deployment. Perfect for getting started and iterating on changes.

## Step 1 — Create a Telegram bot

1. Open Telegram and search for **@BotFather**
2. Send `/newbot`
3. Choose a name (e.g. `My AI Bot`) and a username ending in `bot` (e.g. `myai_bot`)
4. BotFather will reply with a **bot token** that looks like `7123456789:AAF...`
5. Save this token — you will need it in Step 4

---

## Step 2 — Get a Cerebras API key

1. Go to [cloud.cerebras.ai](https://cloud.cerebras.ai) and sign up (free, no credit card)
2. Verify your email and log in
3. Click your profile icon (top right) → **API Keys**
4. Click **Create new API key**, give it a name
5. Copy the key (looks like `csk-...`)
6. Save it — you will need it in Step 4

> **Using a different provider?** Any OpenAI-compatible API works. Set `AI_API_KEY` to your provider's key, `AI_BASE_URL` to their base URL, and `AI_MODEL` to the model name.

---

## Step 3 — Fork and clone the repo

1. Create a [GitHub account](https://github.com) if you don't have one
2. Go to the template repo and click **Fork** (top right) to copy it to your account
3. Clone your fork to your computer:

```bash
git clone https://github.com/<your-username>/telegram-pythonanywhere-bot.git
cd telegram-pythonanywhere-bot
```

---

## Step 4 — Install dependencies and configure `.env`

Create the virtualenv and install Python dependencies:

```bash
make install
```

Then copy the template and fill in the values you saved in Steps 1 and 2:

```bash
cp .env.example .env
```

Open `.env` in your editor and set these two lines:

```
TELEGRAM_BOT_TOKEN=<paste your BotFather token here>
AI_API_KEY=<paste your Cerebras API key here>
```

Leave everything else as-is for now. Memory is optional — without it the bot runs in **stateless mode** (no conversation memory, no rate limit), which is fine for initial testing. For local dev you can enable SQLite memory by setting `SQLITE_PATH` (see the customization reference); on Vercel you'll use Upstash Redis instead (Part 2).

---

## Step 5 — Run the bot locally

```bash
make run
```

You should see something like:

```
Storage not configured — running in stateless mode (no memory, no rate limit).
Bot @your_bot_username starting in polling mode.
Send your bot a message on Telegram to try it out.
Press Ctrl+C to stop.
```

Open Telegram, find your bot, and send it a message. You'll see each exchange logged in your terminal:

```
[14:32:15] @alice → @your_bot: hello, who are you?
[14:32:17] @your_bot → @alice: Hi! I'm an AI assistant powered by Cerebras.
```

This is the same bot code you'll deploy to Vercel — the only difference is how Telegram delivers messages. Locally we poll; in production Telegram pushes to a webhook. Edit any file in `bot/`, `Ctrl+C` the bot, rerun `make run`, and you'll see your changes immediately.

---

# Part 2 — Deploy it to Vercel

Once the bot works locally, the next step is to put it on Vercel so it keeps running when your laptop is closed. Vercel runs the Flask app in `api/index.py` as a serverless function — it spins up on demand for each request. Deploys are driven by Git: connect the repo once, and every push to `main` ships automatically.

There's no server to configure, no WSGI file, and no monthly renewal. The only pieces are: environment variables (set in the Vercel dashboard), a storage backend (Upstash Redis), and a one-time `setWebhook` call so Telegram knows where to send messages.

## Step 6 — Push your fork to GitHub

If you cloned your own fork in Step 3, you're already set — just make sure your latest code is pushed:

```bash
git push origin main
```

Vercel deploys straight from GitHub, so GitHub is the source of truth. You never upload code to Vercel by hand.

---

## Step 7 — Import the project into Vercel

1. Sign up at [vercel.com](https://vercel.com) with your GitHub account (free Hobby tier — no card)
2. Click **Add New… → Project**
3. Find your `telegram-pythonanywhere-bot` fork and click **Import**
4. Leave the build settings at their defaults — `vercel.json` already tells Vercel to run `api/index.py` as a serverless function (with a 60-second max duration). There's no build step for a Python function.
5. **Don't click Deploy yet** — add the environment variables first (Step 8), otherwise the first deploy comes up without a token.

---

## Step 8 — Add environment variables

In the import screen (or later under **Project → Settings → Environment Variables**), add:

| Variable | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | your BotFather token |
| `AI_API_KEY` | your Cerebras API key |
| `WEBHOOK_SECRET` | a random string you generate — run `openssl rand -hex 32` |
| `WEBHOOK_URL` | `https://<your-project>.vercel.app/api/webhook` (fill in after you know your project's domain — you can add it right after the first deploy) |

`AI_BASE_URL` and `AI_MODEL` default to Cerebras / `gpt-oss-120b`, so you only need them if you're switching providers or models.

> **Why set `WEBHOOK_SECRET` here?** Vercel's filesystem is read-only, so the bot can't auto-generate and persist a secret to a file the way it does locally. Setting it as an env var means every serverless instance verifies incoming updates against the same value. Without it the webhook is unsigned (anyone who guesses the URL could forge updates), so always set it in production.

---

## Step 9 — Add persistent memory (Upstash Redis)

SQLite can't be used on Vercel — the filesystem is read-only and wiped between invocations. Instead, attach an Upstash Redis database (a serverless key-value store):

1. In your Vercel project, open the **Storage** tab → **Create Database** → **Upstash → Redis** (from the Marketplace)
2. Follow the prompts to create a free database and **connect it to this project**
3. Vercel automatically injects `UPSTASH_REDIS_REST_URL` and `UPSTASH_REDIS_REST_TOKEN` as environment variables — you don't have to copy anything by hand

That's it. The bot detects those variables at startup and uses Redis for conversation memory, rate limiting, per-user provider preferences, dedupe, and quiz scores. (The code also accepts the older `KV_REST_API_URL` / `KV_REST_API_TOKEN` names, in case an older Vercel KV integration injects those instead.)

If you skip this step the bot still works, just in **stateless mode** — no memory between messages, no rate limiting, no quiz leaderboards.

---

## Step 10 — Deploy

Click **Deploy** (or push a commit). When it finishes, Vercel gives you a URL like `https://<your-project>.vercel.app`.

Verify the function is live by visiting `https://<your-project>.vercel.app/api/health` in a browser — it should return `OK` followed by the short commit ID that's running (e.g. `OK 4ea0ce2`). That commit ID (from Vercel's `VERCEL_GIT_COMMIT_SHA`) is how you can always tell exactly which version of your code is live.

If you hadn't filled in `WEBHOOK_URL` yet, add it now under **Settings → Environment Variables** using your real `.vercel.app` domain, then **redeploy** (Deployments tab → ⋯ → Redeploy) so the variable is picked up.

---

## Step 11 — Point Telegram at your bot (one-time `setWebhook`)

Telegram needs to be told your bot's webhook URL. On Vercel this is a **one-time manual step** — the bot never re-registers the webhook itself (doing that from a serverless request, off a possibly-stale env var, could silently point Telegram at the wrong place and take the bot offline). Set it once here; it stays until you change it.

Run this from your laptop, substituting your token, domain, and the `WEBHOOK_SECRET` you set in Step 8:

```bash
curl -X POST "https://api.telegram.org/bot<TELEGRAM_BOT_TOKEN>/setWebhook" \
  --data-urlencode "url=https://<your-project>.vercel.app/api/webhook" \
  --data-urlencode "secret_token=<your WEBHOOK_SECRET>" \
  --data-urlencode "max_connections=1"
```

The `secret_token` **must** match `WEBHOOK_SECRET` — without it, every update gets rejected with 403. You should see `{"ok":true,...}` in the response.

Now open Telegram, find your bot, and send it a message. Replies come from Vercel. 🎉

---

## Step 12 — Auto-deploy on every push

Nothing to set up — this is how Vercel's Git integration works by default. Every push to `main` triggers a new deployment; when it goes live, `/api/health` reports the new commit ID.

The first `/api/webhook` request after each new deploy re-syncs the bot's `/` command menu with your current commands (via `_bootstrap_once()` in `api/index.py`), so a command you add or remove shows up in Telegram's `/` menu automatically — **send the bot a message after a deploy to trigger it.** (Telegram caches the menu client-side briefly — fully restart the Telegram app if you don't see the change right away.) This never touches the webhook itself; the webhook stays exactly as you set it in Step 11.

> **Preview vs. production.** Only set your bot token in the **Production** environment. Vercel also builds a unique URL for every branch/PR ("preview" deployments); keeping the token production-only means only production ever talks to your bot.

---

## Step 13 — Daily quiz push *(optional)*

The bot can broadcast a daily quiz to every chat that ran `/subscribe`. It's driven by a GitHub Actions workflow (`.github/workflows/daily-quiz.yml`) that calls the bot's `/api/tick` endpoint on a schedule.

1. Set a `TICK_SECRET` environment variable in Vercel (any random string — `openssl rand -hex 32`), and redeploy.
2. On GitHub, go to your fork → **Settings → Secrets and variables → Actions → New repository secret**, and add two secrets:

| Name | Value |
|---|---|
| `TICK_URL` | `https://<your-project>.vercel.app/api/tick` |
| `TICK_SECRET` | the same value you set in Vercel |

The workflow runs each morning (and you can trigger it manually from the **Actions** tab). If the secrets aren't set, it skips with a warning — so this feature is fully optional.

---

# Part 3 — Customize it

## Secure the webhook

Set `WEBHOOK_SECRET` as a Vercel environment variable (Step 8), and pass the same value as `secret_token` in the `setWebhook` call (Step 11). The bot then rejects any incoming request whose `X-Telegram-Bot-Api-Secret-Token` header doesn't match — forged updates get a 403. Keep the two in sync: if you change `WEBHOOK_SECRET`, re-run `setWebhook` with the new value, or Telegram's requests will start getting 403'd and the bot goes silent.

> **Local-dev convenience:** when you run the bot locally and *don't* set `WEBHOOK_SECRET`, it auto-generates a 64-hex-character secret and saves it to `.webhook_secret` (gitignored, mode `0600`) so local runs are signed too. That file can't persist on Vercel's read-only filesystem, which is why production relies on the env var instead.

**To rotate the secret:** change `WEBHOOK_SECRET` in Vercel, redeploy, and re-run the Step 11 `setWebhook` command with the new value.

---

## Add a second AI provider *(optional)*

If you set `HF_SPACE_ID` in your environment, the bot registers a `/model` command that lets users switch between the default provider (`main`) and a Hugging Face Gradio Space (`hf`). Useful for demoing multiple models in the same bot.

```
HF_SPACE_ID=username/space-name
HF_TOKEN=your_hf_token_here   # only for private/gated Spaces
```

Users can now run `/model main` or `/model hf` to switch per-user.

---

## Lock down to specific users *(optional)*

By default the bot replies to anyone on Telegram. To restrict it to a private allow-list, set `ALLOWED_USERS` to a comma-separated list of usernames (with or without `@`) or numeric user IDs:

```
ALLOWED_USERS=@alice,bob,123456789
```

When the variable is set, everyone outside the list gets **silence** — no rejection message, no `/start` response, nothing. This is deliberate: any reply would confirm to a scanner that the bot exists. Whitelisted users see normal behavior.

To find your numeric user ID, message [@userinfobot](https://t.me/userinfobot) on Telegram — it replies with your ID. Useful when you have no public username, or want to whitelist by an identifier that can't change later.

Redeploy for the change to take effect: the list is read at startup.

---

## Customization reference

| What to change | How |
|---|---|
| Bot personality / instructions | Edit `SYSTEM_PROMPT` in `bot/config.py` |
| AI model | Set `AI_MODEL` env var (free-tier tested: `gpt-oss-120b` (default), `qwen-3-235b-a22b-instruct-2507`) |
| AI provider | Set `AI_BASE_URL` env var (any OpenAI-compatible endpoint) |
| Secure the webhook | Set `WEBHOOK_SECRET` env var — see "Secure the webhook" above |
| Persistent memory | Attach Upstash Redis on Vercel (Step 9); or set `SQLITE_PATH` for local dev |
| Restrict who can use the bot | Set `ALLOWED_USERS` env var |
| Daily message limit | Set `RATE_LIMIT` env var (default `250`) |
| Add a second provider | Set `HF_SPACE_ID` (and optionally `HF_TOKEN`) — enables `/model` command |
| Conversation memory length | Edit `MAX_HISTORY` in `bot/config.py` |
| Hosting label shown by `/about` | Set `HOSTING_LABEL` env var (default `Vercel`) |
| Add a new command | Add a handler in `bot/handlers.py` |

---

# Reference

## Project structure

```
telegram-pythonanywhere-bot/
├── api/
│   └── index.py          # Vercel serverless entry — Flask app, /api/webhook, /api/health, /api/tick
├── bot/
│   ├── config.py         # All env vars and constants
│   ├── commands.py       # Single source of truth for the command list (/help text + "/" menu)
│   ├── clients.py        # bot, ai, store instances; register_webhook() + register_commands()
│   ├── store.py          # KV with TTL — RedisStore (Upstash REST, for Vercel) + SqliteStore (local dev)
│   ├── ai.py             # ask_ai orchestration — history, optional grounding context, AI dispatch
│   ├── providers.py      # Provider dispatch: OpenAI-compatible (with retry) or HF Gradio space
│   ├── lookup.py         # /lookup — Wikipedia grounding for world topics; Armenian sources for Armenian topics
│   ├── preferences.py    # Per-user provider preference (via store)
│   ├── history.py        # Conversation memory (via store, graceful degradation)
│   ├── rate_limit.py     # Per-user rate limiting (via store, graceful degradation)
│   ├── dedupe.py         # Drops repeated update_ids when Telegram retries
│   ├── quiz.py           # Daily Quiz Arena — generation, scoring, leaderboards, daily broadcast
│   ├── helpers.py        # Utilities (send_reply, keep_typing, should_respond)
│   └── handlers.py       # Telegram commands — add new commands here
├── tests/                # Offline test suite (mocked Telegram + OpenAI clients)
├── .github/
│   └── workflows/
│       ├── ci.yml        # Runs tests on every push and pull request
│       └── daily-quiz.yml # Calls /api/tick on a schedule (optional daily quiz)
├── vercel.json           # Vercel config — runs api/index.py as a serverless function
├── .env.example          # Copy to .env for local dev (never commit .env)
├── .gitignore
├── Makefile              # install / run / test shortcuts
├── run_local.py          # Local polling entry point (used by `make run`)
├── requirements.txt
├── CLAUDE.md             # Agent-readable project guide
└── README.md
```

---

## Make commands

```bash
make install    # set up virtual environment and install dependencies
make run        # run the bot locally via polling (no deploy needed, reads .env)
make test       # run all tests
```

Windows users without `make` can use the PowerShell equivalent: `.\make.ps1 install`, `.\make.ps1 run`, `.\make.ps1 test`.

---

## Bot commands

| Command | Description |
|---|---|
| `/start` | Welcome message + the command list |
| `/help` | List all commands |
| `/reset` | Clear your conversation history |
| `/about` | Show model, storage, and hosting info |
| `/lookup <topic>` | Look up a history topic from a real source — world topics are grounded in a cited Wikipedia article; Armenian topics are answered from the teacher's expertise plus links to trusted Armenian sources (never Wikipedia) |
| `/quiz [topic]` | Start an auto-scored trivia quiz |
| `/leaderboard` | Show the top quiz scorers in this chat |
| `/subscribe` | Get a daily quiz in this chat each morning (`/unsubscribe` to stop) |
| `/fact` | Get an interesting history fact |
| `/remember <text>`, `/recall`, `/forget` | Save, retrieve, and delete a personal note |
| `/model` | Switch AI provider (only available when `HF_SPACE_ID` is set) |

---

## Running tests

```bash
make test
```

Tests run offline against mocked Telegram and OpenAI clients — no real API keys or network access required. The same suite runs automatically via GitHub Actions on every push and pull request.
