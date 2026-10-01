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
# Bake NLTK corpora: brown/treebank/tagger/punkt for fast-method NLP
# extraction (verified 2026-08-21), plus the rest of graphrag_chunking's
# bootstrap list for `chunking.type: sentence` — wordnet, words and the
# NE chunker (F26-02). Either downloads at runtime otherwise, which fails
# offline and, on nltk >= 3.10, behind a proxy. The build fails
# when any download fails: `quiet=True` alone used to ship an image without
# corpora. nltk >= 3.10 refuses a proxied fetch unless told the proxy is
# trusted; a build proxy is the operator's own, so this one RUN opts in
# (F26-01) and the runtime keeps nltk's default. download_dir is explicit:
# nltk's default is root's $HOME whatever NLTK_DATA says, and every image
# before F26 baked its corpora where the runtime user cannot read them.
# The downloader writes its zips 0600 and wordnet is read from its zip, so
# the tree is opened up for the runtime user afterwards.
RUN NLTK_ALLOW_PROXIED_URLOPEN=1 ./.venv/bin/python -c "import nltk, os, sys; \
    missing = [p for p in ('brown','treebank','averaged_perceptron_tagger', \
                           'averaged_perceptron_tagger_eng','punkt','punkt_tab', \
                           'maxent_ne_chunker','maxent_ne_chunker_tab','words','wordnet') \
               if not nltk.download(p, quiet=True, download_dir=os.environ['NLTK_DATA'])]; \
    sys.exit('nltk corpora not downloaded: ' + ', '.join(missing) if missing else 0)" \
    && chmod -R a+rX "$NLTK_DATA"

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
# `exec` makes uvicorn replace the shell (R2-27): otherwise sh stays PID 1,
# ignores SIGTERM and forwards nothing, so `docker stop` / a pod deletion
# never runs the lifespan shutdown and ends in SIGKILL at the grace timeout.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn --factory graphrag_ui.main:create_app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=\"$FORWARDED_ALLOW_IPS\""]
