import os
from pathlib import Path


class Config:
    CONFIG_DIR = Path(os.getenv("CONFIG_DIR", "/configs"))
    TEMP_DIR = Path(os.getenv("TEMP_DIR", "/tmp/airsocks"))

    PROXY_PORT = int(os.getenv("PROXY_PORT", "8080"))
    PROXY_HOST = os.getenv("PROXY_HOST", "0.0.0.0")

    CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "30"))
    MAX_HANDSHAKE_AGE = int(os.getenv("MAX_HANDSHAKE_AGE", "180"))
    MAX_RECONNECT_ATTEMPTS = int(os.getenv("MAX_RECONNECT_ATTEMPTS", "5"))
    RECONNECT_DELAY = int(os.getenv("RECONNECT_DELAY", "10"))

    LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

    CONNECTIVITY_CHECK_HOST = os.getenv("CONNECTIVITY_CHECK_HOST", "1.1.1.1")
    CONNECTIVITY_CHECK_TIMEOUT = int(os.getenv("CONNECTIVITY_CHECK_TIMEOUT", "10"))
