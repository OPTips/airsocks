import logging
import subprocess
import time
from typing import Optional


class ProxyManager:
    def __init__(self, logger: logging.Logger, config) -> None:
        self.logger = logger
        self.config = config
        self._process: Optional[subprocess.Popen] = None

    def start(self) -> bool:
        if self.is_running():
            return True

        self.logger.info(
            "Starting SOCKS5 proxy on %s:%d",
            self.config.PROXY_HOST,
            self.config.PROXY_PORT,
        )
        try:
            self._process = subprocess.Popen(
                [
                    "microsocks",
                    "-i", self.config.PROXY_HOST,
                    "-p", str(self.config.PROXY_PORT),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(0.5)
            if self._process.poll() is None:
                self.logger.info("SOCKS5 proxy started (PID %d)", self._process.pid)
                return True
            self.logger.error("SOCKS5 proxy exited immediately after start")
            self._process = None
            return False
        except FileNotFoundError:
            self.logger.error("microsocks binary not found")
            return False
        except Exception as exc:
            self.logger.error("Failed to start SOCKS5 proxy: %s", exc)
            return False

    def stop(self) -> None:
        if self._process is None:
            return
        self.logger.info("Stopping SOCKS5 proxy (PID %d)", self._process.pid)
        try:
            self._process.terminate()
            try:
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
                self._process.wait()
            self.logger.info("SOCKS5 proxy stopped")
        except Exception as exc:
            self.logger.error("Error stopping proxy: %s", exc)
        finally:
            self._process = None

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None
