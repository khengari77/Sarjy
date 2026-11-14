FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libgomp1 && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.9.11 /uv /usr/local/bin/uv
ENV PYTHONUNBUFFERED=1 UV_COMPILE_BYTECODE=1
RUN useradd --uid 10001 --create-home agent
ENV HOME=/home/agent
ENV HF_HOME=/home/agent/.cache/huggingface
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev
COPY services/voice-agent ./services/voice-agent
COPY apps/api/app/domain ./shared/app/domain
COPY packages/contracts ./packages/contracts
ENV PYTHONPATH=/app/shared
RUN uv run --no-dev --no-sync -m livekit.agents download-files
RUN chown -R agent:agent /home/agent
USER agent
CMD ["uv", "run", "--no-dev", "--no-sync", "services/voice-agent/main.py", "start"]
