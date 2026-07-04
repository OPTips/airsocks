import signal
import sys

from config import Config
from health_monitor import HealthMonitor
from logger_setup import setup_logger
from proxy_manager import ProxyManager
from vpn_manager import VPNManager


def main() -> None:
    config = Config()
    logger = setup_logger(config.LOG_LEVEL)
    logger.info("AirSocks starting up")

    vpn = VPNManager(logger, config)
    proxy = ProxyManager(logger, config)
    monitor = HealthMonitor(logger, config, vpn, proxy)

    def shutdown(signum, frame):
        logger.info("Shutdown signal received — cleaning up")
        monitor.stop()
        proxy.stop()
        vpn.disconnect()
        logger.info("Shutdown complete")
        sys.exit(0)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    monitor.run()


if __name__ == "__main__":
    main()
