FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
WORKDIR /app
# The graphrag CLI must be on PATH — adapters invoke `graphrag` via
# subprocess, and running .venv/bin/uvicorn directly would not inject the
# venv's PATH ("passes locally, explodes in the container")
ENV PATH="/app/.venv/bin:$PATH"
# Corpora go to a world-readable shared prefix, not root's $HOME: the
# runtime user below is not root and would not find /root/nltk_data.
ENV NLTK_DATA=/usr/local/share/nltk_data
COPY pyproject.toml uv.lock ./
# src/ is not COPYed yet; the project itself cannot be installed or the build fails
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
# Bake NLTK corpora (brown/treebank/tagger/punkt): fast-method NLP extraction
# downloads these at runtime otherwise (verified 2026-08-21).
RUN ./.venv/bin/python -c "import nltk; [nltk.download(p, quiet=True) for p in ('brown','treebank','averaged_perceptron_tagger_eng','punkt','punkt_tab')]"

# Non-root runtime (uid 10001). The workspaces mountpoint is created and
# chowned in the image so a *fresh* docker named volume inherits these
# permissions on first use; K8s relies on the chart's fsGroup instead.
# ⚠ Upgrading an existing deployment: a volume created by an older
# root-running image stays root-owned — chown it once (see README).
RUN groupadd --system --gid 10001 appuser \
    && useradd --system --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser \
    && mkdir -p /data/workspaces \
    && chown -R appuser:appuser /data/workspaces /app
USER appuser
EXPOSE 8000
# main.py only has create_app(), no module-level app → --factory is required.
# --proxy-headers with a NARROW --forwarded-allow-ips: the api trusts
# X-Forwarded-For only from the reverse proxy in front of it. Trusting `*`
# lets anyone who can reach the api directly (in K8s every pod can, via the
# ClusterIP Service) forge a client IP and so mint a fresh login-rate-limit
# bucket per attempt. Compose publishes no api port, so the only peer is the
# web container on the compose network; K8s additionally pins this down with
# a NetworkPolicy. FORWARDED_ALLOW_IPS is a uvicorn CLI concern, not app
# config (spec §8.3's fixed env names are unaffected) — override it per
# topology when the proxy sits in a different subnet. The default is the
# RFC1918 space (covers compose bridge networks and every common CNI pod
# CIDR); K8s narrows the reachable peer set further via NetworkPolicy.
ENV FORWARDED_ALLOW_IPS=10.0.0.0/8,172.16.0.0/12,192.168.0.0/16
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory graphrag_ui.main:create_app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"$FORWARDED_ALLOW_IPS\""]
