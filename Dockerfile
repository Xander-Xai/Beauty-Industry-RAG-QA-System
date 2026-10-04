FROM python:3.10-slim

# The uid/gid are pinned so the container manifests under deploy/ can name them
# explicitly: they set runAsUser/runAsGroup/fsGroup to 1000 and rely on Secret
# volumes being group-readable by the application user.
# The group is created first because --gid alone fails on this base image: it
# has no group 1000 yet, so useradd exits 6.
RUN groupadd --gid 1000 appuser \
    && useradd --create-home --shell /bin/bash --uid 1000 --gid 1000 appuser

WORKDIR /app

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || exit 1

CMD ["python", "app.py"]
