# syntax=docker/dockerfile:1
# Public demo of AgriCredit Agent on a Hugging Face Space (Docker SDK).
# The Space holds only this file and README.md: the build downloads the code from GitHub,
# builds the real-open-data store, and bakes the Qwen model into the image so the demo starts
# without any download. To update the demo after a push to GitHub: Space Settings > Factory rebuild.
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG OLLAMA_IMAGE=ollama/ollama:0.40.10

FROM ${NODE_IMAGE} AS node
FROM ${OLLAMA_IMAGE} AS ollama

FROM ${PYTHON_IMAGE}
# Which code to deploy: a branch name or tag of the GitHub repository.
ARG REPO=https://github.com/amonyepraise-hub/agricredit-agent.git
ARG REF=deploy
ARG MODEL=qwen2.5:7b-instruct

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGRICREDIT_VAR_DIR=/app/var HOME=/app/var OLLAMA_MODELS=/app/models

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
COPY --from=ollama /usr/bin/ollama /usr/local/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/local/lib/ollama

# git fetches the code; libopenblas0 and libvulkan1 are the runtime libraries the Ollama image ships with.
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libopenblas0 libvulkan1 \
 && rm -rf /var/lib/apt/lists/*
RUN git clone --depth 1 --branch "$REF" "$REPO" /app
WORKDIR /app
RUN chmod +x deploy/*.sh && deploy/build.sh

# Bake the model into the image (about 4.7 GB) so the Space starts quickly.
RUN mkdir -p /app/models /app/var \
 && (OLLAMA_HOST=127.0.0.1:11434 ollama serve > /dev/null 2>&1 & echo $! > /tmp/ollama.pid) \
 && sleep 10 \
 && OLLAMA_HOST=127.0.0.1:11434 ollama pull "$MODEL" \
 && kill "$(cat /tmp/ollama.pid)" \
 && useradd --uid 1000 --no-create-home user \
 && chown -R 1000:1000 /app

USER user
ENV AGRICREDIT_EMBEDDED_OLLAMA=true \
    AGRICREDIT_LLM_BACKEND=ollama \
    OLLAMA_MODEL=${MODEL} \
    OLLAMA_KEEP_ALIVE=24h \
    OLLAMA_NUM_PARALLEL=1 \
    AGRICREDIT_LLM_TIMEOUT_S=900 \
    AGRICREDIT_AUTH_REQUIRED=true \
    AGRICREDIT_DEMO_ENGINE_CHOICE=true \
    AGRICREDIT_QWEN_NOTE="On this free demo server Qwen runs on 2 CPU cores, so a case takes several minutes. Fast mode gives the same decision in seconds." \
    PORT=8501
EXPOSE 8501
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
