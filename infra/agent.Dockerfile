FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY services/voice-agent ./services/voice-agent
RUN uv run --no-dev --no-sync -m livekit.agents download-files
CMD ["uv", "run", "--no-dev", "--no-sync", "services/voice-agent/main.py", "start"]
