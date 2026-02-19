FROM python:3.11-slim

WORKDIR /app

# Install Python deps (cached layer)
COPY pyproject.toml .
COPY src/ src/
RUN pip install --no-cache-dir .

RUN mkdir -p /app/data

# Non-root user — UID 1000 matches deploy user on VPS for volume permissions
RUN useradd -m -s /bin/bash -u 1000 botuser \
    && chown -R botuser:botuser /app
USER botuser

HEALTHCHECK --interval=60s --timeout=5s --retries=3 \
  CMD python -c "import os,time; assert time.time()-os.path.getmtime('/app/data/health')<120"

CMD ["python", "-m", "src.main"]
