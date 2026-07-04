import logging
import time

from vpn_manager import VPNManager
from proxy_manager import ProxyManager


class HealthMonitor:
    def __init__(
        self,
        logger: logging.Logger,
        config,
        vpn: VPNManager,
        proxy: ProxyManager,
    ) -> None:
        self.logger = logger
        self.config = config
        self.vpn = vpn
        self.proxy = proxy
        self._running = False

    def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _is_vpn_healthy(self) -> bool:
        if not self.vpn.is_interface_up():
            self.logger.warning("VPN interface is down")
            return False

        age = self.vpn.get_handshake_age()
        if age is None:
            self.logger.warning("No WireGuard handshake yet")
            return False

        if age > self.config.MAX_HANDSHAKE_AGE:
            self.logger.warning(
                "Handshake too old: %ds (limit %ds)", age, self.config.MAX_HANDSHAKE_AGE
            )
            return False

        return True

    def _try_connect(self) -> bool:
        """Attempt to bring up VPN, retrying up to MAX_RECONNECT_ATTEMPTS times."""
        for attempt in range(1, self.config.MAX_RECONNECT_ATTEMPTS + 1):
            self.logger.info(
                "VPN connection attempt %d/%d", attempt, self.config.MAX_RECONNECT_ATTEMPTS
            )
            if self.vpn.connect():
                time.sleep(3)  # Give the tunnel a moment to complete handshake
                if self.vpn.is_interface_up():
                    return True
            wait = min(self.config.RECONNECT_DELAY * attempt, 60)
            self.logger.warning("Connection attempt failed, retrying in %ds", wait)
            time.sleep(wait)

        self.logger.error(
            "VPN connection failed after %d attempts", self.config.MAX_RECONNECT_ATTEMPTS
        )
        return False

    def _handle_vpn_failure(self) -> bool:
        """Kill switch: stop proxy, reconnect VPN, restart proxy on success."""
        self.logger.warning("VPN failure detected — kill switch activated")
        self.proxy.stop()

        self.vpn.disconnect()
        time.sleep(2)

        if self._try_connect():
            self.logger.info("VPN reconnected — restarting proxy")
            return self.proxy.start()

        self.logger.error("VPN reconnection failed — proxy remains stopped")
        return False

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        self._running = True
        self.logger.info("AirSocks health monitor starting")

        if not self._try_connect():
            self.logger.error("Initial VPN connection failed — exiting")
            return

        if not self.proxy.start():
            self.logger.error("Failed to start SOCKS5 proxy — exiting")
            self.vpn.disconnect()
            return

        self.logger.info(
            "Ready — SOCKS5 proxy listening on port %d (check interval: %ds)",
            self.config.PROXY_PORT,
            self.config.CHECK_INTERVAL,
        )

        while self._running:
            time.sleep(self.config.CHECK_INTERVAL)

            if not self._running:
                break

            # Proxy died on its own
            if not self.proxy.is_running():
                self.logger.warning("SOCKS5 proxy died unexpectedly")
                if self._is_vpn_healthy():
                    self.logger.info("VPN still up — restarting proxy")
                    self.proxy.start()
                else:
                    self._handle_vpn_failure()
                continue

            # Periodic VPN health check
            if not self._is_vpn_healthy():
                self._handle_vpn_failure()
            else:
                self.logger.debug("VPN health OK (handshake age: %ds)", self.vpn.get_handshake_age() or 0)
