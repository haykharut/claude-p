# Filesystem access

## Mental model (read this first)

All your job files live in **one place only**: `~/claudectl/fs/` on
the host running the daemon.

```
~/claudectl/fs/
├── jobs/     # one folder per job (the job.yaml + code + workspace + runs)
├── shared/   # cross-job staging (only visible to jobs with shared: true)
└── inbox/    # drop-zone (reserved for file-triggered runs in a later version)
```

On the **server** (the box running claude-p) you just `cd ~/claudectl/fs/`.

On **other devices** (your Mac, phone, another laptop on the same
Wi-Fi) claude-p exposes that same folder over WebDAV. Your device
mounts it as if it were a local drive, but **nothing is copied
locally** — every open/save is a live HTTP round-trip to the server.
One copy, two access paths. When the server is off or you leave the
LAN, the mount disappears.

> This is a **live network mount**, not a sync tool. If you want
> "keep a copy on my laptop and reconcile when online" (e.g. edit on a
> plane), use Syncthing on top — see the bottom of this doc.

Same credentials as the dashboard. Read-write. Any WebDAV client works.

> The **Settings page** shows the exact URLs for your host — copy them
> from there instead of guessing.

## macOS — Finder

1. **⌘K** in Finder (or `Go → Connect to Server…`)
2. Paste the URL from Settings, e.g. `http://192.168.1.42:8080/fs`
3. Auth: username `admin` (any non-empty value works), password = your
   dashboard password. Tick **Remember in Keychain**.
4. Finder mounts `fs` as a volume. Drag files in and out.

Tips:
- If you're on the Mac where the daemon runs, `http://localhost:8080/fs`
  also works.
- If both devices are Apple, `http://<servername>.local:8080/fs` works
  via mDNS without knowing the IP.
- To unmount: sidebar → the eject icon.

## Windows — Map Network Drive

Windows disables HTTP Basic over plain HTTP by default. You have to
either run claude-p behind HTTPS (e.g. Tailscale, reverse proxy) or
flip a registry flag:

```reg
Windows Registry Editor Version 5.00

[HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Services\WebClient\Parameters]
"BasicAuthLevel"=dword:00000002
```

Then restart the `WebClient` service (`sc stop WebClient & sc start WebClient`).

After that: Explorer → **This PC** → **Map network drive…** → paste the URL,
tick **Reconnect at sign-in**, enter credentials.

Simpler alternative: install [CyberDuck](https://cyberduck.io/) or
WinSCP, use their WebDAV profile — no registry changes needed.

## Linux — davfs2

```bash
sudo apt install davfs2
sudo mkdir -p /mnt/claudep
sudo mount -t davfs http://192.168.1.42:8080/fs /mnt/claudep
# (prompts for user/password)
```

For a per-user fstab entry (no sudo after first setup):

```bash
sudo usermod -aG davfs2 $USER   # re-login after
# /etc/fstab:
http://192.168.1.42:8080/fs  /mnt/claudep  davfs  user,rw,noauto  0  0
```

## iOS — Files app

Files → **Browse** → ••• → **Connect to Server** → paste the URL.
Registered users: username/password. Shows up alongside iCloud in the
sidebar.

## Android

Most file managers have WebDAV support — use
[Solid Explorer](https://play.google.com/store/apps/details?id=pl.solidexplorer2)
or [Material Files](https://f-droid.org/en/packages/me.zhanghai.android.files/).
Add a "Remote connection" → WebDAV, paste the URL, enter credentials.

## Why WebDAV and not SMB / NFS / SSHFS?

- **SMB** is faster and more native on Windows, but requires a second
  daemon (Samba). claude-p ships WebDAV baked in — one port, one auth.
- **NFS** requires root on the client for mount, and its auth model
  (UID mapping, Kerberos) is overkill for a single-user home server.
- **SSHFS** works but involves SSH key management. WebDAV reuses the
  dashboard password, which you already have.

For truly fast / large-file access on Windows, run Samba separately on
the Ubuntu box against `~/claudectl/fs/` and use WebDAV as a fallback
for other devices.

## Syncthing — bidirectional sync for development

WebDAV is a live mount — when the server is offline, your files
disappear. If you develop jobs on your laptop and want changes to
appear on the server in seconds (and server-side outputs to sync back),
use [Syncthing](https://syncthing.net/). It runs on both machines and
keeps folders in sync bidirectionally.

### Setup (one-time, ~2 minutes)

Install Syncthing on both sides:

```bash
# Mac
brew install syncthing
brew services start syncthing

# Ubuntu server (SSH in first)
sudo apt install syncthing -y
systemctl --user enable --now syncthing
sudo loginctl enable-linger $USER
```

Then pair them with one command from your Mac:

```bash
./scripts/setup-sync.sh user@server-ip
```

The script SSHs into the server, exchanges device IDs, creates the
shared folder on both sides, and sets ignore patterns — no browser
tabs, no manual copy-pasting.

### What the script does

1. Reads Syncthing API keys from both machines (local config + SSH)
2. Adds each device to the other's Syncthing config
3. Creates `~/claudectl/fs/jobs/` as a bidirectional shared folder
4. Sets ignore patterns: `.venv`, `__pycache__`, `*.pyc`, `.ruff_cache`

### Manual pairing (alternative)

If you prefer the browser UI: forward the server's Syncthing port
(`ssh -L 8385:localhost:8384 user@server-ip`), then pair via
`http://localhost:8384` (local) and `http://localhost:8385` (server).

### Why the ignore patterns matter

`.venv` contains platform-specific binaries (Linux vs macOS) — each
side creates its own. `__pycache__` and `*.pyc` are also
platform/version-specific. Syncing them causes `Exec format error`.

## Common issues

**macOS Finder asks for a password on every session.**
Tick the Keychain checkbox on the first mount, or go to Keychain
Access and search for your server's hostname to pre-save.

**Windows says "The folder you entered does not appear to be valid."**
Almost always the BasicAuthLevel registry flag. Re-check it and
restart the `WebClient` service.

**davfs2 mount is slow on first access.**
davfs caches aggressively but the initial PROPFIND of a big directory
is slow. Consider mounting a specific subdir (`/fs/jobs/my-job/`)
rather than all of `/fs`.

**`.venv/` folders clutter the listing.**
Known — wsgidav supports hiding them via `hide_file_in_dir`; not
enabled yet. File manager hidden-file toggle works meanwhile.
