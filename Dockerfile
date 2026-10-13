# One image for the tool server and the worker; compose picks the command. (The dashboard installs the optional group.)
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1
ARG WITH_DASHBOARD=false
# Dependencies first (a cached layer), then the code.
COPY pyproject.toml uv.lock ./
RUN if [ "$WITH_DASHBOARD" = "true" ]; then uv sync --locked --no-dev --group dashboard --no-install-project; \
    else uv sync --locked --no-dev --no-install-project; fi
COPY app ./app
COPY assistant ./assistant
COPY config ./config
COPY migrations ./migrations
COPY dashboard ./dashboard
COPY evals ./evals
COPY scripts ./scripts
RUN useradd --system --uid 10001 --no-create-home p11  # the app only reads /srv, so no slow chown of the virtualenv
USER p11
ENV PATH="/srv/.venv/bin:$PATH" PYTHONPATH=/srv
CMD ["uvicorn", "app.api.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8011"]
