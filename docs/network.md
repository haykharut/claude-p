# Network access

claude-p binds to `0.0.0.0:8080` by default — every network interface
on the host. That means:

- **Anyone on the same Wi-Fi / LAN can reach the dashboard** at
  `http://<server-ip>:8080` (subject to Basic auth).
- **Nothing is reachable from the public internet** unless you
  explicitly expose it (Tailscale, reverse proxy, port forward).

The Settings page detects and displays the specific URLs that work
for your host — use those rather than the ones below.

## Finding your server's IP

On the server:

```bash
# macOS
ipconfig getifaddr en0      # Wi-Fi (en0 usually; en1/en2 if ethernet)
# Linux
ip -4 addr show scope global | awk '/inet /{print $2}' | head -1
# Windows
ipconfig | findstr /i "IPv4"
```

Or just open `http://localhost:8080/settings` on the server — the
Access card shows all detected URLs.

## Apple devices (iPhone, other Macs) via mDNS

If your server is a Mac, the hostname `<servername>.local` is
broadcast automatically. On another Apple device on the same Wi-Fi:

```
http://hayks-macbook-pro.local:8080
```

(Replace with your actual hostname — `scutil --get LocalHostName` on the server.)

Linux/Windows boxes need Bonjour / Avahi installed to resolve `.local`.

## Using a fixed hostname on your router

Most home routers let you pin an IP to a MAC address (DHCP
reservation) and give it a hostname. Then `http://claudep.home.arpa`
or similar works from everywhere on the LAN without memorising IPs.
Consult your router's admin page.

## Restricting to localhost only

If you don't want LAN access at all, bind to the loopback:

```bash
CLAUDE_P_BIND_HOST=127.0.0.1 claude-p serve
```

or in `~/claudectl/config.env`:

```
CLAUDE_P_BIND_HOST=127.0.0.1
```

Only your own machine can reach the dashboard.

## Remote access (outside your LAN)

**Do NOT port-forward claude-p to the public internet.** HTTP Basic
auth is enough for LAN but not for the open web — no rate limiting,
no HTTPS by default, tempting attack surface for a free subscription
to run stuff on.

The right pattern: **Tailscale**.

```bash
# on the server:
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up

# on your other devices (iOS, Mac, Windows, Linux):
# install Tailscale, log in with same account
```

Now `http://<server-tailscale-name>:8080` works from anywhere, over
WireGuard, end-to-end encrypted, zero config. Tailscale's HTTPS
(`https://<server>.ts.net`) is also available if you want proper TLS
for free.

Alternatives:
- **Cloudflare Tunnel** — `cloudflared tunnel --url http://localhost:8080`
  gives you an HTTPS URL without opening ports. More setup than
  Tailscale.
- **SSH port forward** — `ssh -L 8080:localhost:8080 user@server`
  from a laptop. Works for one-off sessions.
- **Reverse proxy behind HTTPS (nginx / Caddy)** — if you have a
  domain and care about browser-native HTTPS. Overkill for most home
  deployments.

## Firewall

Ubuntu default: no firewall, port 8080 open to LAN.

If you have `ufw` enabled and want to explicitly allow:

```bash
sudo ufw allow from 192.168.0.0/16 to any port 8080 comment 'claude-p LAN'
```

macOS firewall (System Settings → Network → Firewall): when enabled,
macOS will prompt the first time a remote device tries to reach the
daemon. Click Allow.

## Security checklist

- [ ] Dashboard password set (`claude-p set-password`)
- [ ] Not port-forwarded to the public internet
- [ ] On a trusted LAN, or fronted by Tailscale for remote access
- [ ] Strong password if LAN is shared (guest Wi-Fi on, housemates etc.)
- [ ] OS updated (you're serving Python code to your LAN)

For exposing beyond LAN, bolt proper TLS + strong auth on top
(Tailscale handles both automatically).
