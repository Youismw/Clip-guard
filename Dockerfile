# Pinned base image
FROM python:3.11-slim-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Install ffmpeg, ffprobe, chromaprint (fpcalc), curl, ca-certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libchromaprint-tools \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md requirements.txt ./

# Install project and production dependencies
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir .

COPY src/ src/
COPY dedupe.toml.example dedupe.toml

# Container healthcheck
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD dedupe doctor || exit 1

ENTRYPOINT ["dedupe"]
CMD ["doctor"]
