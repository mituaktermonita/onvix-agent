FROM node:24.21.0-bookworm-slim

ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PATH="/opt/runtime/node_modules/.bin:${PATH}"

RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates curl rsync procps util-linux python3 make g++ tini logrotate \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/runtime
COPY runtime/package.json runtime/package-lock.json ./
# Fail on unsupported Node engines; lock transitive packages, not just the two CLIs.
RUN npm ci --omit=dev --engine-strict --no-audit --no-fund \
 && rm -f node_modules/omniroute/.env \
 && npm cache clean --force

RUN useradd -m -s /bin/bash omniroute \
 && useradd -m -s /bin/bash openclaw \
 && useradd -m -s /bin/bash launcher \
 && chmod 700 /home/omniroute /home/openclaw /home/launcher \
 && mkdir -p /opt/runtime/node_modules/omniroute/dist/.build/next/cache \
 && chown omniroute:omniroute /opt/runtime/node_modules/omniroute/dist/.build/next/cache

COPY app /opt/app
COPY scripts/verify-runtime.mjs /opt/app/verify-runtime.mjs
COPY scripts/image-smoke.sh scripts/supervisor-smoke.sh /opt/tests/
COPY tests/image-config.mjs /opt/tests/image-config.mjs
RUN chmod +x /opt/app/*.sh /opt/tests/*.sh \
 && node /opt/app/verify-runtime.mjs

ARG VCS_REF=local
RUN printf '%s\n' "$VCS_REF" > /opt/app/revision
LABEL org.opencontainers.image.source="https://github.com/mituaktermonita/onvix-agent" \
      org.opencontainers.image.revision="$VCS_REF"

WORKDIR /opt/app
EXPOSE 8080 20128 18789
HEALTHCHECK --interval=30s --timeout=15s --start-period=240s --retries=3 CMD ["bash", "/opt/app/healthcheck.sh"]
ENTRYPOINT ["/usr/bin/tini", "--"]
# Daytona creates the container before deploy.py injects the private runtime env.
CMD ["bash", "/opt/app/entrypoint.sh"]
