FROM python:3.11-slim AS builder

RUN apt-get update && apt-get install -y \
    gcc \
    make \
    git \
    && rm -rf /var/lib/apt/lists/*

RUN git clone https://github.com/rofl0r/microsocks.git /tmp/microsocks \
    && cd /tmp/microsocks \
    && make \
    && strip /tmp/microsocks/microsocks

FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    wireguard-tools \
    iptables \
    iproute2 \
    iputils-ping \
    procps \
    && rm -rf /var/lib/apt/lists/*

# wg-quick calls `sysctl net.ipv4.conf.all.src_valid_mark=1` but lacks permission
# to write kernel params from inside the container. The value is already set via
# Docker's sysctls in compose.yml, so we wrap sysctl to silently ignore failures.
RUN mv /sbin/sysctl /sbin/sysctl.orig \
    && printf '#!/bin/sh\n/sbin/sysctl.orig "$@" || true\n' > /sbin/sysctl \
    && chmod +x /sbin/sysctl

COPY --from=builder /tmp/microsocks/microsocks /usr/local/bin/microsocks

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ .

RUN mkdir -p /configs /tmp/airsocks

VOLUME ["/configs"]

EXPOSE 8080

CMD ["python", "main.py"]
