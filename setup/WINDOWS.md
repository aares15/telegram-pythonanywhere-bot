# Native Windows (PowerShell) — no WSL

> Most Windows students should use **WSL** instead — see [WSL.md](WSL.md). With WSL
> every workshop command (`make install`, `./connect-claude-code.sh`, …) runs exactly
> as written on the slides. Use *this* file only if you can't use WSL and want to run
> everything natively in **PowerShell**.

Everything below uses the `.ps1` scripts in this repo, which mirror the `.sh` ones.

---

## 0. One-time: allow scripts to run

By default Windows blocks `.ps1` scripts. In a PowerShell window, run once:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

(Or run any single script ad-hoc without changing policy:
`powershell -ExecutionPolicy Bypass -File .\make.ps1 install`.)

---

## 1. Set up the bot

From the cloned repo folder:

```powershell
.\make.ps1 install      # create .venv and install dependencies
copy .env.example .env  # then edit .env and fill in your tokens
.\make.ps1 run          # run the bot locally via polling
.\make.ps1 test         # run the test suite
```

`.\make.ps1` with no target prints the full list.

---

## 2. Connect Claude Code

```powershell
.\make.ps1 claude sk-your-key
# or directly:
.\setup\connect-claude-code.ps1 sk-your-key
```

It checks your key against the workshop gateway, installs Claude Code if needed,
points it at the gateway, and launches `claude`. Add `-Persist` to remember it in
new terminals — **but not on a shared lab machine** (it writes your key into your
PowerShell `$PROFILE`).

The manual env-var commands (if you'd rather not use the script) are in
[CLAUDE-CODE.md](CLAUDE-CODE.md) under "native Windows PowerShell".

---

## 3. Deploy to PythonAnywhere

For everyday updates, just `git push` to `main` — the GitHub Action deploys for you;
no local script needed. For **first-time setup or recovery** from your machine:

```powershell
# Requires PowerShell 7+ (winget install Microsoft.PowerShell)
.\make.ps1 deploy-pa
```

This reads `.env` (needs `PA_USERNAME` + `PA_API_TOKEN` in addition to the bot vars),
creates/repairs the web app, uploads the `.env` + WSGI file, points the app at your
clone + virtualenv, reloads, and health-checks. It's idempotent — safe to re-run.

`.\make.ps1 deploy-pa` automatically uses `pwsh` (PowerShell 7) if you launched it
from Windows PowerShell 5.1.

---

## Troubleshooting

| What you see | Fix |
|---|---|
| `running scripts is disabled on this system` | Run the `Set-ExecutionPolicy` line in step 0. |
| `deploy-pa` says it needs version 7 | Install PowerShell 7: `winget install Microsoft.PowerShell`, then reopen the terminal as **pwsh**. |
| `py`/`python` not found on `install` | Install Python 3.13 from python.org and tick "Add to PATH". |
| `claude: command not found` after connect | Open a **new** terminal, or add `%USERPROFILE%\.local\bin` to PATH. |
| Gateway `outside_workshop_hours` | Your key is fine — the gateway only opens during class hours. Re-run during the session. |
