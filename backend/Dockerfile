FROM python:3.13-slim-trixie@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS builder

WORKDIR /build
COPY backend/requirements-dev.lock.txt backend/requirements-runtime.lock.txt ./backend/
RUN python -m pip install --no-cache-dir --only-binary=:all: \
    -c backend/requirements-dev.lock.txt build setuptools wheel
COPY backend/pyproject.toml backend/README.md backend/MANIFEST.in ./backend/
COPY backend/src/incidentclue/ ./backend/src/incidentclue/
RUN python -m build --no-isolation --wheel backend --outdir /wheels \
    && python -m pip download --no-cache-dir --only-binary=:all: --no-deps \
       -r backend/requirements-runtime.lock.txt --dest /wheels

FROM python:3.13-slim-trixie@sha256:2d44738ebf4ce5f63b9d9ea871e9c7cfdc0426f7b0c97cac915fd88ded1d86bb AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENAI_LOG=warning

COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index /wheels/*.whl \
    && python -m pip check \
    && rm -rf /wheels \
    && groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --create-home --no-log-init app \
    && mkdir -p /app/runs \
    && chown -R app:app /app

WORKDIR /app
USER 10001:10001
ENTRYPOINT ["incidentclue"]
CMD ["--help"]
