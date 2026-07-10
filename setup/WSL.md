# Windows machines: work inside WSL (Ubuntu)

The Yerevan lab runs on **Windows**, but every command in the workshop slides is
written for macOS/Linux. So on Windows you work inside **WSL** — a real Ubuntu
Linux running on top of Windows. Open it once and **every slide command runs
exactly as written**: `make`, `cp`, `./connect-claude-code.sh`, `git`, `python3`
— all of it.

> If you're on your own Mac or Linux laptop, ignore this file — the slides already
> apply to you.

---

## Start here (students)

1. **Open the `Ubuntu` app** from the Windows Start menu. A black terminal opens.
   That terminal *is* your workshop machine for the whole week.
2. **First time only:** it asks you to create a UNIX username and password. Pick
   anything simple and remember the password (you'll need it for `sudo`).
   *(If the lab machine was pre-set with a shared `student` user, there's no prompt —
   just start typing.)*
3. **Always clone into your home folder**, never into the Windows `C:` drive:

   ```bash
   cd ~
   git clone https://github.com/<your-username>/telegram-pythonanywhere-bot.git
   ```

   Check you're in the right place — `pwd` should print `/home/...`, **not**
   `/mnt/c/...`. Cloning under `/mnt/c` is slow and breaks file watching.

4. From here, **follow the slides verbatim** — `make install`, `make run`, etc.

### 30-second sanity check

Run this in Ubuntu before class starts; every line should print a version:

```bash
git --version
python3 --version
make --version
gh --version
curl --version | head -1
date -u            # confirm the clock looks right (see TLS note below)
```

---

## If something goes wrong

| Symptom | Fix |
|---|---|
| `gh auth login` / Vercel link **doesn't open a browser** | Copy the URL it prints into the Windows browser (Chrome/Edge) by hand and continue there. |
| `./connect-claude-code.sh: bad interpreter` or `\r` errors | The script picked up Windows line endings. Run: `sed -i 's/\r$//' setup/connect-claude-code.sh && chmod +x setup/connect-claude-code.sh` |
| HTTPS/TLS suddenly fails (after the PC slept) — `curl` to the gateway, GitHub, or Telegram errors out | The WSL clock drifted. Close Ubuntu, open **PowerShell**, run `wsl --shutdown`, then reopen Ubuntu. |
| `claude: command not found` after install | Open a **new** Ubuntu terminal, or `export PATH="$HOME/.local/bin:$PATH"`. |
| `make: command not found` | The machine wasn't provisioned — see the instructor list below, or run that `apt install` line. |

---

## Shared-machine hygiene ⚠️

These are lab machines — the next student inherits whatever you leave behind.

- **Don't use `connect-claude-code.sh --persist` on a lab machine.** `--persist`
  writes *your personal class key* into `~/.bashrc`, where the next student would
  inherit it. Run the script **without** `--persist` so the key lives only in that
  one terminal session. (To clean up a machine that already has it, delete the
  block between `# >>> workshop claude code >>>` and `# <<< workshop claude code <<<`
  in `~/.bashrc`.)
- Your bot's secrets live in `.env` inside your cloned folder. Day 5 covers
  resetting the **git identity** on a shared machine (`gh auth logout` / `login`).

---

## Instructor: one-time provisioning per machine

WSL needs admin rights and the virtualization features enabled, so this is an IT
imaging task — **do it and test it before class, on the same Windows account type
the students will log in as** (a distro installed under an admin profile is *not*
automatically available under a student profile).

**1. Enable WSL + install Ubuntu** (admin PowerShell, then reboot):

```powershell
wsl --install -d Ubuntu
```

**2. Inside Ubuntu, install the toolchain + GitHub CLI** (official apt repo —
*not* snap or the distro `universe` package, which lag and break):

```bash
sudo apt update
sudo apt install -y \
  git make python3 python3-venv python3-pip \
  curl ca-certificates wget gpg build-essential \
  nano dos2unix xdg-utils wslu

# GitHub CLI — official repo
sudo mkdir -p -m 755 /etc/apt/keyrings
wget -nv -O /tmp/githubcli-archive-keyring.gpg \
  https://cli.github.com/packages/githubcli-archive-keyring.gpg
sudo cp /tmp/githubcli-archive-keyring.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg
sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  | sudo tee /etc/apt/sources.list.d/github-cli.list >/dev/null
sudo apt update
sudo apt install -y gh

git config --global core.autocrlf input   # never inject CRLF into cloned repos
```

**3. Pre-warm the Claude Code installer** once on the image so 20 simultaneous
downloads don't stall during Day 2:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
```

**4. Pre-flight the network** from inside Ubuntu on the lab Wi-Fi — every endpoint
the workshop touches:

```bash
for u in github.com api.github.com api.telegram.org \
         vercel.com ai.simonian.online; do
  echo -n "$u -> "; curl -s -o /dev/null -w '%{http_code}\n' -m 10 "https://$u"
done
```

(`ai.simonian.online` answers only during class hours — a 403/closed response
off-hours is expected; any other endpoint failing is a real problem to fix.)

**5. Consider a shared `student` UNIX user** in the image so the first-launch
username/password prompt doesn't cost 20 kids three minutes each.
