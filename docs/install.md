# Installation

## Mac (dev / testing)

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
uv venv --python 3.12 && uv pip install -e '.[dev]'
.venv/bin/claude-p set-password
.venv/bin/claude-p dev
```

Open <http://localhost:8080>, username `admin`, password = what you
just set.

## Ubuntu home server (production)

**Option A: full installer** (dedicated system user, shared machines):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
sudo ./scripts/install.sh
```

**Option B: bootstrap** (your own user, one command, personal box):

```bash
git clone https://github.com/haykharut/claude-p.git
cd claude-p
./scripts/bootstrap.sh
```

Sets up the venv, DB, password, systemd service, and linger. One
command, good to go. Update after pushing new code:

```bash
./scripts/update.sh    # pull, migrate, restart
```

## First run

1. Open the dashboard. **Settings → Access** shows your URLs.
2. Copy an example job:
   ```bash
   cp -r ~/claude-p/jobs-example/hello-world ~/claudectl/fs/jobs/
   ```
3. Click **Run now**. Output appears under `/runs/…`.
4. The **Ledger** tab shows cost across rolling windows.

Full walkthrough: [jobs.md](./jobs.md).
