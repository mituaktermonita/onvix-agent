FROM node:20-bookworm-slim

ARG OPENCLAW_VERSION=2026.9.8
ARG OMNIROUTE_VERSION=3.8.51

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl rsync procps util-linux python3 make g++ \
 && rm -rf /var/lib/apt/lists/*

RUN npm install -g openclaw@${OPENCLAW_VERSION} omniroute@${OMNIROUTE_VERSION} \
 && npm cache clean --force

# one user per service: no shared home, files or processes
RUN useradd -m -s /bin/bash omniroute \
 && useradd -m -s /bin/bash openclaw \
 && useradd -m -s /bin/bash launcher

COPY app /opt/app
RUN chmod +x /opt/app/supervisor.sh

CMD ["sleep", "infinity"]
