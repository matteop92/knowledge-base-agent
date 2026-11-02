# Obsidian's sync tool runs on Node; build it in its own stage so Python's image stays clean.
# Base images are pinned by digest so a rebuild cannot silently pick up a different image.
FROM node:26-slim@sha256:e4db4c914bf8ad241bd24804fc25d272d89bad97e68cdc8a1414862790a02c39 AS obsidian
WORKDIR /opt/obsidian
# The lock file pins the sync tool and everything it depends on.
COPY deploy/obsidian/package.json deploy/obsidian/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/knowledge-agent

# Node 26 links against libatomic, which the Python image does not ship.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 && rm -rf /var/lib/apt/lists/*
COPY --from=obsidian /usr/local/bin/node /usr/local/bin/node
COPY --from=obsidian /opt/obsidian/node_modules /opt/obsidian/node_modules
RUN ln -s /opt/obsidian/node_modules/obsidian-headless/cli.js /usr/local/bin/ob \
    && chmod +x /opt/obsidian/node_modules/obsidian-headless/cli.js && ob --version

COPY pyproject.toml constraints.txt ./
COPY app ./app
RUN pip install --no-cache-dir -c constraints.txt ".[server]"
COPY prompts ./prompts
COPY config ./config
COPY deploy ./deploy

# Secrets come from the host's secret store at run time, never from the image.
# The app runs as `agent`; deploy/start.sh starts as root only to make the mounted volume writable for it.
RUN useradd --create-home agent && chmod +x deploy/*.sh
EXPOSE 8000
CMD ["./deploy/start.sh"]
