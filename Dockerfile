# ---- Build stage ----
FROM python:3.12-alpine AS builder

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache g++ linux-headers

WORKDIR /build

# Install dependencies (includes grpcio-tools for proto compilation)
COPY requirements.in .
RUN pip install --no-cache-dir -r requirements.in

# Generate proto stubs
COPY proto/ ./proto/
RUN mkdir -p generated \
    && python -m grpc_tools.protoc \
        -I proto \
        --python_out=generated \
        --grpc_python_out=generated \
        proto/recommendation.proto \
    && touch generated/__init__.py \
    && sed -i 's/^import recommendation_pb2 as recommendation__pb2/from . import recommendation_pb2 as recommendation__pb2/' generated/recommendation_pb2_grpc.py

# ---- Runtime stage ----
FROM python:3.12-alpine AS runtime

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apk add --no-cache libstdc++ \
    && addgroup -S appgroup && adduser -S appuser -G appgroup

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy generated proto stubs
COPY --from=builder --chown=appuser:appgroup /build/generated ./src/generated/

# Copy application source
COPY --chown=appuser:appgroup src/ ./src/

USER appuser

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import grpc; ch=grpc.insecure_channel('localhost:${PORT:-8080}'); grpc.channel_ready_future(ch).result(timeout=3)" || exit 1

ENTRYPOINT ["python", "src/server.py"]