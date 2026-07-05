import logging
import random
import re
import subprocess
import time
from pathlib import Path
from typing import Optional


# RFC1918 private ranges kept outside the WireGuard tunnel so that replies to
# clients on the local network (e.g. the SOCKS5 proxy answering a LAN peer)
# route back out via the container's normal gateway instead of being pulled
# into wg0 by wg-quick's full-tunnel (AllowedIPs = 0.0.0.0/0) routes. Public
# internet traffic is unaffected and still fully forced through the tunnel.
PRIVATE_RANGES = ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]


class VPNManager:
    def __init__(self, logger: logging.Logger, config) -> None:
        self.logger = logger
        self.config = config
        self._active_config: Optional[Path] = None
        self._prepared_config: Optional[Path] = None
        self.interface: Optional[str] = None

    def get_available_configs(self) -> list[Path]:
        configs = sorted(self.config.CONFIG_DIR.glob("*.conf"))
        if not configs:
            self.logger.error("No WireGuard .conf files found in %s", self.config.CONFIG_DIR)
        return configs

    def select_random_config(self) -> Optional[Path]:
        configs = self.get_available_configs()
        if not configs:
            return None
        selected = random.choice(configs)
        self.logger.info("Selected config: %s", selected.name)
        return selected

    def _prepare_config(self, config_path: Path) -> Path:
        """Copy config to a temp file named wg0.conf.

        - Interface name is fixed to "wg0" (wg-quick derives it from the filename
          stem; provider filenames often exceed the 15-char Linux interface name limit).
        - DNS stripped: resolvconf absent in container; all traffic tunnelled via AllowedIPs.
        - PostUp/PostDown stripped: we manage the iptables kill switch ourselves.
        """
        self.config.TEMP_DIR.mkdir(parents=True, exist_ok=True)
        content = config_path.read_text()
        content = re.sub(r"^\s*DNS\s*=.*\n?", "", content, flags=re.MULTILINE)
        content = re.sub(r"^\s*(PostUp|PostDown)\s*=.*\n?", "", content, flags=re.MULTILINE)
        content = self._strip_ipv6(content)
        tmp_path = self.config.TEMP_DIR / "wg0.conf"
        tmp_path.write_text(content)
        tmp_path.chmod(0o600)
        return tmp_path

    def _strip_ipv6(self, content: str) -> str:
        """Remove IPv6 entries from Address and AllowedIPs (IPv6 disabled in container)."""
        def ipv4_only(match: re.Match) -> str:
            key = match.group(1)
            values = [v.strip() for v in match.group(2).split(",") if ":" not in v.strip()]
            return f"{key} = {', '.join(values)}" if values else ""

        return re.sub(
            r"^(Address|AllowedIPs)\s*=\s*(.+)$",
            ipv4_only,
            content,
            flags=re.MULTILINE,
        )

    def _parse_endpoint(self, config_path: Path) -> tuple[str, int]:
        """Extract the VPN server IP and UDP port from the config."""
        content = config_path.read_text()
        match = re.search(r"^\s*Endpoint\s*=\s*(.+?):(\d+)\s*$", content, re.MULTILINE)
        if match:
            return match.group(1), int(match.group(2))
        self.logger.warning("Could not parse VPN endpoint from %s", config_path.name)
        return "", 0

    def _cleanup_stale_interface(self, interface: str) -> None:
        result = subprocess.run(["ip", "link", "show", interface], capture_output=True)
        if result.returncode == 0:
            self.logger.warning("Stale interface %s found — removing it", interface)
            subprocess.run(["ip", "link", "delete", interface], capture_output=True)

    def _get_default_route(self) -> Optional[tuple[str, str]]:
        """Return (gateway_ip, device) of the container's non-VPN default route."""
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        for line in result.stdout.splitlines():
            match = re.search(r"^default via (\S+) dev (\S+)", line)
            if match and match.group(2) != "wg0":
                return match.group(1), match.group(2)
        return None

    def _exempt_private_ranges(self) -> None:
        """Keep RFC1918 traffic on the container's normal gateway, not wg0."""
        gateway = self._get_default_route()
        if gateway is None:
            self.logger.warning("Could not determine default gateway — LAN traffic may route via VPN")
            return
        gateway_ip, device = gateway
        for cidr in PRIVATE_RANGES:
            subprocess.run(
                ["ip", "route", "replace", cidr, "via", gateway_ip, "dev", device],
                capture_output=True,
                timeout=5,
            )
        self.logger.info("Excluded private ranges (%s) from VPN tunnel via %s", ", ".join(PRIVATE_RANGES), device)

    # ------------------------------------------------------------------
    # iptables kill switch
    # ------------------------------------------------------------------

    def _ipt(self, *args: str) -> None:
        subprocess.run(["iptables"] + list(args), capture_output=True, timeout=5)

    def _enable_kill_switch(self, interface: str, endpoint_ip: str, endpoint_port: int) -> None:
        """Block all outbound traffic that does not go through the VPN tunnel.

        Using a dedicated chain (AIRSOCKS_KS) avoids conflicting with any other
        iptables rules that may exist in the container.
        """
        # Clean up any leftover chain from a previous incomplete run
        self._ipt("-D", "OUTPUT", "-j", "AIRSOCKS_KS")
        self._ipt("-F", "AIRSOCKS_KS")
        self._ipt("-X", "AIRSOCKS_KS")

        self._ipt("-N", "AIRSOCKS_KS")
        self._ipt("-I", "OUTPUT", "1", "-j", "AIRSOCKS_KS")

        # Allow loopback
        self._ipt("-A", "AIRSOCKS_KS", "-o", "lo", "-j", "RETURN")
        # Allow traffic through the VPN tunnel
        self._ipt("-A", "AIRSOCKS_KS", "-o", interface, "-j", "RETURN")
        # Allow already-established connections (e.g. the WireGuard handshake itself)
        self._ipt("-A", "AIRSOCKS_KS", "-m", "conntrack", "--ctstate", "ESTABLISHED,RELATED", "-j", "RETURN")
        # Allow WireGuard UDP to the VPN server so the tunnel can (re)connect
        if endpoint_ip and endpoint_port:
            self._ipt("-A", "AIRSOCKS_KS", "-p", "udp", "-d", endpoint_ip, "--dport", str(endpoint_port), "-j", "RETURN")
        # Drop everything else — no traffic can leak through eth0
        self._ipt("-A", "AIRSOCKS_KS", "-j", "DROP")

        self.logger.info(
            "Kill switch active — outbound blocked except wg0 and %s:%d",
            endpoint_ip, endpoint_port,
        )

    def _disable_kill_switch(self) -> None:
        self._ipt("-D", "OUTPUT", "-j", "AIRSOCKS_KS")
        self._ipt("-F", "AIRSOCKS_KS")
        self._ipt("-X", "AIRSOCKS_KS")
        self.logger.info("Kill switch removed")

    # ------------------------------------------------------------------
    # Connect / disconnect
    # ------------------------------------------------------------------

    def connect(self, config_path: Optional[Path] = None) -> bool:
        if config_path is None:
            config_path = self.select_random_config()
        if config_path is None:
            return False

        interface = "wg0"
        self._cleanup_stale_interface(interface)

        prepared = self._prepare_config(config_path)
        self.logger.info("Connecting via %s (interface: %s)", config_path.name, interface)

        try:
            result = subprocess.run(
                ["wg-quick", "up", str(prepared)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                self._active_config = config_path
                self._prepared_config = prepared
                self.interface = interface
                self.logger.info("VPN up on interface %s", interface)
                endpoint_ip, endpoint_port = self._parse_endpoint(config_path)
                self._enable_kill_switch(interface, endpoint_ip, endpoint_port)
                self._exempt_private_ranges()
                return True
            self.logger.error("wg-quick up failed: %s", result.stderr.strip())
            return False
        except subprocess.TimeoutExpired:
            self.logger.error("wg-quick up timed out")
            return False
        except Exception as exc:
            self.logger.error("VPN connect error: %s", exc)
            return False

    def disconnect(self) -> None:
        if not self._prepared_config or not self.interface:
            return
        self._disable_kill_switch()
        self.logger.info("Bringing down interface %s", self.interface)
        try:
            subprocess.run(
                ["wg-quick", "down", str(self._prepared_config)],
                capture_output=True,
                text=True,
                timeout=30,
            )
        except Exception as exc:
            self.logger.error("wg-quick down error: %s", exc)
        finally:
            self._active_config = None
            self._prepared_config = None
            self.interface = None

    # ------------------------------------------------------------------
    # Health checks
    # ------------------------------------------------------------------

    def is_interface_up(self) -> bool:
        if not self.interface:
            return False
        try:
            result = subprocess.run(
                ["ip", "link", "show", self.interface],
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0 and "UP" in result.stdout
        except Exception:
            return False

    def get_handshake_age(self) -> Optional[int]:
        """Seconds since the most recent WireGuard handshake, or None."""
        if not self.interface:
            return None
        try:
            result = subprocess.run(
                ["wg", "show", self.interface, "latest-handshakes"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return None
            for line in result.stdout.strip().splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    timestamp = int(parts[1])
                    if timestamp == 0:
                        return None
                    return int(time.time()) - timestamp
        except Exception:
            pass
        return None

    def check_connectivity(self) -> bool:
        if not self.interface:
            return False
        try:
            result = subprocess.run(
                [
                    "ping", "-c", "1",
                    "-W", str(self.config.CONNECTIVITY_CHECK_TIMEOUT),
                    "-I", self.interface,
                    self.config.CONNECTIVITY_CHECK_HOST,
                ],
                capture_output=True,
                text=True,
                timeout=self.config.CONNECTIVITY_CHECK_TIMEOUT + 2,
            )
            return result.returncode == 0
        except Exception:
            return False
