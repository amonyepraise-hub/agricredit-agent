# AgriCredit Agent container.
# NOTE: this image could NOT be built in the original build environment (Docker Hub was unreachable);
# the tested one-command path is ./run_demo.sh. See README "Docker" before relying on this file.
FROM node:22-bookworm-slim AS nodedeps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
COPY --from=nodedeps /usr/local/bin/node /usr/local/bin/node
WORKDIR /app
COPY requirements.lock pyproject.toml README.md ./
RUN pip install -r requirements.lock
COPY src ./src
COPY data/schema.sql data/generate_synthetic_data.py data/model_card.json data/README.md ./data/
COPY fs_mcp_root ./fs_mcp_root
COPY tests ./tests
COPY evals ./evals
COPY .env.example ./.env.example
COPY --from=nodedeps /app/node_modules ./node_modules
RUN pip install --no-deps -e . \
 && python data/generate_synthetic_data.py \
 && python -m agricredit.risk.train > /dev/null \
 && cp .env.example .env
# Inside Docker, reach an Ollama server running on the host:
ENV OLLAMA_BASE_URL=http://host.docker.internal:11434
EXPOSE 8501
CMD ["streamlit", "run", "src/agricredit/ui/app.py", "--server.port=8501", "--server.address=0.0.0.0", "--server.headless=true", "--browser.gatherUsageStats=false"]
