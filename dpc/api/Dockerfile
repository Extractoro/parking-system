# One image for api, ray-head, ray-worker and your coordinators. A Ray client must have the same
# Python and Ray versions as the cluster, and the workers must import your task code: one image
# from one uv.lock guarantees both. Python 3.14, dependencies from uv.lock.
FROM ghcr.io/astral-sh/uv:0.13.1-python3.14-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/srv/.venv/bin:$PATH" \
    PYTHONPATH=/srv
WORKDIR /srv

COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

COPY app ./app
RUN mkdir -p /data /faults

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
