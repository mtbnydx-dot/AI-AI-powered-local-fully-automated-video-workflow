# Control server image (the node agent runs on the host via systemd so it can drive docker compose).
# Build where there is network access, then `docker save` / `docker load` into the offline registry.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml README.md ./
COPY drama_factory ./drama_factory
RUN pip install --no-cache-dir ".[postgres,s3]"
ENTRYPOINT ["dfctl"]
