# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

AirSocks is a Dockerized application that connects to a VPN server via WireGuard and exposes a SOCKS5 proxy on port 8080. It is designed as a proxy gateway for other applications to route their traffic securely through the VPN. Compatible with any WireGuard-based VPN provider (AirVPN, Mullvad, ProtonVPN, etc.).

## Build and run

```bash
# Build and start
docker compose up --build

# Rebuild after code changes
docker compose up --build -d

# View logs
docker compose logs -f
```

The container requires `NET_ADMIN` and `SYS_MODULE` capabilities plus `/dev/net/tun` — these are declared in `compose.yml`.

## WireGuard configs

Place WireGuard `.conf` files in the `configs/` directory. The container mounts it read-only at `/configs`. On each (re)connection the app picks one file at random.

The app strips the `DNS =` directive from configs before calling `wg-quick up` to avoid `resolvconf` dependency issues inside Docker. DNS still flows through the VPN tunnel because `AllowedIPs = 0.0.0.0/0` routes all traffic through the interface.

## Architecture

All application code lives in `app/`. Entry point is `app/main.py`.

| File | Responsibility |
|---|---|
| `config.py` | All tuneable values loaded from environment variables |
| `logger_setup.py` | stdout handler (visible via `docker compose logs`) |
| `vpn_manager.py` | `VPNManager` — config selection, `wg-quick up/down`, handshake age |
| `proxy_manager.py` | `ProxyManager` — start/stop `microsocks` subprocess on port 8080 |
| `health_monitor.py` | `HealthMonitor` — main loop: periodic VPN health checks, kill switch, reconnection |
| `main.py` | Wires everything together, handles SIGTERM/SIGINT for clean shutdown |

## Kill switch

When the VPN health check fails (interface down OR handshake older than `MAX_HANDSHAKE_AGE`):
1. The SOCKS5 proxy is stopped immediately (`proxy.stop()`)
2. The WireGuard interface is brought down
3. A new random config is selected and the VPN reconnects
4. The proxy restarts only after a successful reconnection

Note: `PostUp`/`PostDown` directives from provider configs are stripped — the kill switch is managed entirely by the application.

## Key environment variables

| Variable | Default | Description |
|---|---|---|
| `CHECK_INTERVAL` | `30` | Seconds between VPN health checks |
| `MAX_HANDSHAKE_AGE` | `180` | Seconds before a stale handshake triggers reconnect |
| `MAX_RECONNECT_ATTEMPTS` | `5` | Max retries before giving up a connection cycle |
| `RECONNECT_DELAY` | `10` | Base delay (seconds) between retries, multiplied by attempt number |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `CONNECTIVITY_CHECK_HOST` | `1.1.1.1` | Host pinged through the VPN interface during health checks |

## SOCKS5 proxy

`microsocks` is compiled from source in the Dockerfile (multi-stage build). It binds to `0.0.0.0:8080`. Other Docker containers can use it by setting `SOCKS5_PROXY=<airsocks_container_ip>:8080` or by putting them on the same Docker network.

## Caveats

- The container must run with `--privileged` or the capabilities listed in `compose.yml` — WireGuard kernel module manipulation requires this.
- IPv6 is disabled in the container (`net.ipv6.conf.all.disable_ipv6=1`); IPv6 addresses in WireGuard configs are stripped automatically.
- `wg-quick` derives the interface name from the config filename stem (e.g., `europe1.conf` → interface `europe1`). Avoid duplicate filenames.
