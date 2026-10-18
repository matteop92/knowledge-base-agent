# Obsidian's sync tool needs Node 22; build it in its own stage so Python's image stays clean.
FROM node:22-slim AS obsidian
RUN npm install -g obsidian-headless@0.0.14

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/knowledge-agent

COPY --from=obsidian /usr/local/bin/node /usr/local/bin/node
COPY --from=obsidian /usr/local/lib/node_modules/obsidian-headless /opt/obsidian-headless
RUN ln -s /opt/obsidian-headless/cli.js /usr/local/bin/ob && chmod +x /opt/obsidian-headless/cli.js

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir ".[server]"
COPY prompts ./prompts
COPY config ./config
COPY deploy ./deploy

# Secrets come from the host's secret store at run time, never from the image.
# The app runs as `agent`; deploy/start.sh starts as root only to make the mounted volume writable for it.
RUN useradd --create-home agent && chmod +x deploy/*.sh
EXPOSE 8000
CMD ["./deploy/start.sh"]
