# Obsidian's sync tool needs Node 22; build it in its own stage so Python's image stays clean.
# Base images are pinned by digest so a rebuild cannot silently pick up a different image.
FROM node:25-slim@sha256:81db02c4b671288a03915da9534dbd54f96d0e7c24d80ccc54f5b36b2e684370 AS obsidian
WORKDIR /opt/obsidian
# The lock file pins the sync tool and everything it depends on.
COPY deploy/obsidian/package.json deploy/obsidian/package-lock.json ./
RUN npm ci --omit=dev

FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/knowledge-agent

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
