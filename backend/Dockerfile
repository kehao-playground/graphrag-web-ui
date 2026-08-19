FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS base
WORKDIR /app
# graphrag CLI 必須在 PATH 上 — adapters 以 subprocess 呼叫 `graphrag`,
# 直接跑 .venv/bin/uvicorn 不會注入 venv 的 PATH,會造成「本機過、容器炸」
ENV PATH="/app/.venv/bin:$PATH"
COPY pyproject.toml uv.lock ./
# src/ 還沒 COPY,不能安裝專案本身,否則 build 失敗
RUN uv sync --frozen --no-dev --no-install-project
COPY . .
RUN uv sync --frozen --no-dev
EXPOSE 8000
# main.py 只有 create_app(),沒有模組級 app → 必須 --factory
CMD ["sh", "-c", "alembic upgrade head && uvicorn --factory graphrag_ui.main:create_app --host 0.0.0.0 --port 8000"]
