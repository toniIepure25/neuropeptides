# Multi-stage build: compile a wheel, then install it into a slim runtime image.
FROM python:3.13-slim AS builder

WORKDIR /src
COPY . .
RUN python -m pip install --no-cache-dir build \
    && python -m build --wheel --outdir /wheels

FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NTX_LOG_LEVEL=INFO

COPY --from=builder /wheels/*.whl /tmp/
RUN python -m pip install --no-cache-dir --no-deps /tmp/*.whl \
    && rm -f /tmp/*.whl \
    && useradd --create-home --shell /usr/sbin/nologin ntx

USER ntx
WORKDIR /home/ntx

# Offline demo from the bundled corpus; deterministic by default.
CMD ["ntx", "--frozen-time", "demo", "--db", "ntx.db", "--out", "reports"]
