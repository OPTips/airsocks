# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project purpose

AirSocks is a Dockerized application that connects to a VPN server via WireGuard and exposes a SOCKS5 proxy on port 8080. It is designed as a proxy gateway for other applications to route their traffic securely through the VPN. Compatible with any WireGuard-based VPN provider (AirVPN, Mullvad, ProtonVPN, etc.).

## Build and run

```bash
# Build and start (builds the image locally)
docker compose up --build -d

# Or use the prebuilt multi-arch image instead of building
docker pull ghcr.io/optips/airsocks:latest
docker compose up -d

# View logs
docker compose logs -f
```

There is no test suite, linter config, or CI check beyond the image build — `app/` has no dependencies beyond the Python standard library (`requirements.txt` is intentionally empty of packages). Verify changes by running the container and checking `docker compose logs -f` for the connect/health-check/kill-switch log lines.

The container requires `NET_ADMIN` and `SYS_MODULE` capabilities plus `/dev/net/tun` — these are declared in `compose.yml`.

## CI/CD

`.github/workflows/build.yml` builds and pushes `ghcr.io/optips/airsocks:latest` (linux/amd64 + linux/arm64) on every push to `master` that touches `Dockerfile`, `requirements.txt`, `app/**`, or the workflow itself. There is no separate lint/test job — the build succeeding is the only gate.

## WireGuard configs

Place WireGuard `.conf` files in the `configs/` directory. The container mounts it read-only at `/configs`. On each (re)connection the app picks one file at random (`vpn_manager.VPNManager.select_random_config`).

Before calling `wg-quick up`, the selected config is copied to a temp file and rewritten (`VPNManager._prepare_config`):
- **Interface is always named `wg0`**, regardless of the source filename — the config is copied to `TEMP_DIR/wg0.conf` precisely so `wg-quick` (which derives the interface name from the file stem) never sees provider filenames, which are often longer than Linux's 15-char interface name limit. Duplicate/odd filenames are not a concern.
- `DNS =` is stripped to avoid a `resolvconf` dependency inside the container. DNS still flows through the tunnel because `AllowedIPs = 0.0.0.0/0` routes all traffic through the interface.
- `PostUp`/`PostDown` are stripped — the kill switch is managed entirely by the application (iptables), not by provider scripts.
- IPv6 entries are stripped from `Address`/`AllowedIPs` since IPv6 is disabled in the container.

## Architecture

All application code lives in `app/`. Entry point is `app/main.py`.

| File | Responsibility |
|---|---|
| `config.py` | All tuneable values loaded from environment variables |
| `logger_setup.py` | stdout handler (visible via `docker compose logs`) |
| `vpn_manager.py` | `VPNManager` — config selection/rewriting, `wg-quick up/down`, iptables kill switch, handshake age, connectivity check |
| `proxy_manager.py` | `ProxyManager` — start/stop `microsocks` subprocess on port 8080 |
| `health_monitor.py` | `HealthMonitor` — main loop: periodic VPN health checks, kill-switch orchestration, reconnection with backoff |
| `main.py` | Wires everything together, handles SIGTERM/SIGINT for clean shutdown |

## Kill switch

The kill switch operates at two levels simultaneously:

1. **Network (iptables)** — `VPNManager._enable_kill_switch` creates a dedicated `AIRSOCKS_KS` chain hooked into `OUTPUT` as soon as the tunnel comes up. It allows loopback, traffic out the WireGuard interface (`wg0`), established/related connections, and UDP to the VPN endpoint (so WireGuard itself can reconnect) — everything else is dropped. This means outbound traffic cannot leak even if the Python process crashes; a separate chain name avoids clobbering any other iptables rules in the container. `_disable_kill_switch` tears the chain down on clean disconnect.
2. **Application** — `HealthMonitor._handle_vpn_failure` runs when the periodic health check fails (interface down, no handshake, or handshake older than `MAX_HANDSHAKE_AGE`) or the proxy process dies unexpectedly:
   - `proxy.stop()` immediately
   - `vpn.disconnect()` (also tears down the iptables chain — traffic is still blocked at this point via the network-level kill switch until reconnect)
   - Reconnect with a new random config, retrying up to `MAX_RECONNECT_ATTEMPTS` times with linear backoff (`RECONNECT_DELAY * attempt`, capped at 60s)
   - Proxy restarts only after a successful reconnection; if reconnection fails entirely, the proxy stays down and traffic stays blocked

## LAN routing exception

`wg-quick` with `AllowedIPs = 0.0.0.0/0` installs a full-tunnel default route via `wg0` in a dedicated policy-routing table, which would otherwise also capture replies from the SOCKS5 proxy back to LAN clients (e.g. when the proxy port is published on the Docker host and reached from another machine on the network) — those replies would be pulled into the tunnel and never reach the client. `VPNManager._exempt_private_ranges` (called right after `wg-quick up` in `connect()`) adds explicit routes for RFC1918 ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`) via the container's original gateway/interface (detected with `_get_default_route`), so private-network traffic stays off the tunnel while all public traffic remains fully tunnel-forced. This doesn't weaken the kill switch: reply traffic to an already-established connection is allowed by the `ESTABLISHED,RELATED` iptables rule regardless of which interface it exits through — the fix is routing-only.

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

`microsocks` (rofl0r/microsocks) is compiled from source in the Dockerfile's builder stage and copied into the final image. It binds to `0.0.0.0:8080`. Other Docker containers can use it by setting `ALL_PROXY=socks5h://<airsocks_container_or_service_name>:8080` or by putting them on the same Docker network.

## Caveats

- The container must run with `--privileged` or the capabilities listed in `compose.yml` — WireGuard kernel module manipulation requires this.
- IPv6 is disabled in the container (`net.ipv6.conf.all.disable_ipv6=1`); IPv6 addresses in WireGuard configs are stripped automatically.
- The Dockerfile wraps `/sbin/sysctl` to silently ignore failures — `wg-quick` calls `sysctl net.ipv4.conf.all.src_valid_mark=1` but the container lacks permission to write kernel params directly; the value is already applied via Docker's `sysctls:` in `compose.yml`, so the wrapper just avoids a spurious failure.
