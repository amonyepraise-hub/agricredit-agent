# syntax=docker/dockerfile:1
# AgriCredit Agent: officer dashboard on real Benin open data, with officer sign-in.
#
#   docker compose up -d --build      # dashboard + Qwen (Ollama) on http://localhost:8501
#
# Build arguments:
#   WITH_OLLAMA=true   also put the Ollama runtime inside this image (one-container hosting, e.g. a
#                      Hugging Face Space); the default uses the separate Ollama container in docker-compose.yml
ARG PYTHON_IMAGE=python:3.12-slim-bookworm
ARG NODE_IMAGE=node:22-bookworm-slim
ARG OLLAMA_IMAGE=ollama/ollama:0.40.10

FROM ${NODE_IMAGE} AS node
FROM ${OLLAMA_IMAGE} AS ollama

FROM ${PYTHON_IMAGE}
ARG WITH_OLLAMA=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 \
    AGRICREDIT_VAR_DIR=/app/var HOME=/app/var

# Node.js runs the official MCP filesystem server.
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# Ollama runtime (only copied in when WITH_OLLAMA=true; otherwise removed in the next step).
COPY --from=ollama /usr/bin/ollama /usr/local/bin/ollama
COPY --from=ollama /usr/lib/ollama /usr/local/lib/ollama
RUN if [ "$WITH_OLLAMA" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends ca-certificates libopenblas0 libvulkan1 \
      && rm -rf /var/lib/apt/lists/*; \
    else rm -rf /usr/local/bin/ollama /usr/local/lib/ollama; fi

RUN useradd --create-home --uid 1000 --home-dir /home/agricredit agricredit
WORKDIR /app
COPY --chown=agricredit:agricredit . .
RUN chmod +x deploy/*.sh && deploy/build.sh && mkdir -p /app/var && chown -R agricredit:agricredit /app

USER agricredit
EXPOSE 8501
HEALTHCHECK --interval=30s --timeout=5s --start-period=180s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4)"
ENTRYPOINT ["/app/deploy/entrypoint.sh"]
