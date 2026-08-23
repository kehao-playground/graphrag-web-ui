FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
WORKDIR /app
# The graphrag CLI must be on PATH — adapters invoke `graphrag` via
# subprocess, and running .venv/bin/uvicorn directly would not inject the
# venv's PATH ("passes locally, explodes in the container")
ENV PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
# src/ is not COPYed yet; the project itself cannot be installed or the build fails
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
# Bake NLTK corpora (brown/treebank/tagger/punkt): fast-method NLP extraction
# downloads these at runtime otherwise (verified 2026-08-21).
RUN ./.venv/bin/python -c "import nltk; [nltk.download(p, quiet=True) for p in ('brown','treebank','averaged_perceptron_tagger_eng','punkt','punkt_tab')]"
EXPOSE 8000
# main.py only has create_app(), no module-level app → --factory is required
# --proxy-headers: in both topologies (compose/K8s) the api is reachable only
# behind the web nginx (compose publishes no api port, K8s goes
# ClusterIP/ingress); trust the whole cluster network so
# request.client.host reflects the X-Forwarded-For real client IP
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory graphrag_ui.main:create_app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
