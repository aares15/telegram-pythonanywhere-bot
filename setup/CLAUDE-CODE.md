# Running Claude Code on your machine

Claude Code is an AI pair-programmer that lives in your terminal. In this workshop
it talks to **our own AI gateway** instead of Anthropic's — so you don't need an
account or a credit card, just the **key your instructor hands you** in class.

Your key looks like `sk-………` and is personal (one per student: `gyumri-NN` / `yerevan-NN`).

---

## The fast way (macOS / Linux / WSL / Git-Bash)

From the cloned repo:

```bash
cd setup
./connect-claude-code.sh sk-your-key-here
```

That's it. The script will:

1. check your key against the gateway,
2. install Claude Code if you don't have it,
3. point it at the workshop gateway, and
4. launch `claude`.

Want every new terminal to stay connected? Add `--persist`:

```bash
./connect-claude-code.sh sk-your-key-here --persist
```

Once it's running, just ask in plain English, e.g.
*"Add a `/quote` command to `bot/handlers.py` that replies with a random quote, and list it in `/help`."*

---

## The manual way (any OS)

**1. Install Claude Code (once):**

```bash
# macOS / Linux / WSL / Git-Bash
curl -fsSL https://claude.ai/install.sh | bash
```

```powershell
# native Windows PowerShell
irm https://claude.ai/install.ps1 | iex
```

(Or, if you have Node.js: `npm install -g @anthropic-ai/claude-code`.)

**2. Point it at the workshop gateway** (paste your key where shown):

```bash
# macOS / Linux / WSL / Git-Bash
export ANTHROPIC_BASE_URL="https://ai.simonian.online"
export ANTHROPIC_AUTH_TOKEN="sk-your-key-here"
export ANTHROPIC_MODEL="gemma26"
export ANTHROPIC_SMALL_FAST_MODEL="gemma26"
```

```powershell
# native Windows PowerShell
$env:ANTHROPIC_BASE_URL  = "https://ai.simonian.online"
$env:ANTHROPIC_AUTH_TOKEN = "sk-your-key-here"
$env:ANTHROPIC_MODEL      = "gemma26"
$env:ANTHROPIC_SMALL_FAST_MODEL = "gemma26"
```

**3. Run it** from your project folder:

```bash
claude
```

---

## Troubleshooting

| What you see | What it means | Fix |
|---|---|---|
| `outside_workshop_hours` (403) | Your key is fine — the gateway only opens during class hours. | Run it again during the session. |
| `invalid key` / 401 | The key is mistyped or expired. | Re-copy it from the instructor; watch for spaces. |
| `Couldn't reach …` | No internet / wrong URL. | Check Wi‑Fi; the URL is `https://ai.simonian.online` (no `/v1`). |
| `maximum context length … 20480` | You're running **outside class hours** — the big-context workshop model is only up during the session. | Run it during the session window; the model has a 200k window then. |
| `claude: command not found` | PATH not updated after install. | Open a **new** terminal, or `export PATH="$HOME/.local/bin:$PATH"`. |

---

## Instructor notes

- **Gateway:** `https://ai.simonian.online` (Caddy → LiteLLM Anthropic path; **no** `/v1` in `ANTHROPIC_BASE_URL`).
- **Model for Claude Code: `gemma26`.** It's in the student allow-list (`[gemma, gemma26, qwen]`)
  and, like `gemma`, has a `gemma26 → gemma-solo` fallback that reaches the workshop deployment
  during class (see routing below).
- **Why `gemma` only works *during* the session — the scheduled GPU switch:**
  gpu-pc serves a different model layout outside vs. during class, driven by systemd timers
  (`workshop-gemma-start/stop`, see `runpod/workshop/scheduling/`):
  - **During the session window** (timer fires ~45 min before): `docker-compose.gemma26.yml`
    takes the whole GPU and serves **`nvidia/Gemma-4-26B-A4B-NVFP4`** as model `gemma` on `:8000`
    with **`--max-model-len 206848` (~200k)** and `--max-num-seqs 24` — comfortably fits Claude
    Code's ~20k system prompt and covers 20 concurrent students.
  - **Outside the window:** the idle "three-e" layout is up; `gemma` there is a 12B on `:8002`
    with only a **20,480** window — too small for Claude Code (it 400s `ContextWindowExceededError`).
    This is why off-hours testing of Claude Code against `gemma` fails; it's expected.
  - **Routing:** LiteLLM `gemma26` (and `gemma`) → `:8002`, but `config.yaml` has
    `fallbacks: [{"gemma": ["gemma-solo"]}, {"gemma26": ["gemma-solo"]}, …]`; during the session
    `:8002` is gone, so a `gemma26` call fails over to `gemma-solo` → `:8000` (the 200k workshop
    model). The fallback is router-internal, so the student key's allow-list is not re-checked
    against `gemma-solo`.
- **Slide model id:** Week 1 Day 3 (`slides/week1-day3.md`) must use `ANTHROPIC_MODEL="gemma26"`
  and `ANTHROPIC_SMALL_FAST_MODEL="gemma26"`. (Earlier drafts showed `claude-gemma12-workshop`,
  which is invalid — the `claude-` prefix 400s and gemma12 isn't a student model.)
- **Keys:** per-student LiteLLM virtual keys, `gyumri-01..20` / `yerevan-01..20`
  (rpm 120, tpm 500k, 5 parallel each). Both centers provisioned for **20 students**.
  Inventories: `~/.config/glassprompter/litellm-keys-{gyumri,yerevan}.csv`.
- **Time gate:** the gateway enforces workshop hours (`403 outside_workshop_hours`) —
  Gyumri 22–26 Jun, Yerevan 29 Jun–3 Jul & 6–10 Jul (12:30–16:30 / 13:30–17:30 Yerevan time;
  the GPU model is brought up ~45 min before). Hand keys out and let students run the script
  *during* a session; outside hours the preflight reports "key valid, gateway closed."
- **Setting `ANTHROPIC_AUTH_TOKEN`** skips Claude Code's normal login entirely.
