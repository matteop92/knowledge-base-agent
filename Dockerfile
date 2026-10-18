FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv/knowledge-agent

COPY pyproject.toml ./
COPY app ./app
RUN pip install --no-cache-dir ".[server]"
COPY prompts ./prompts
COPY config ./config
COPY deploy/start.sh ./deploy/start.sh

# Secrets come from the host's secret store at run time, never from the image.
RUN useradd --create-home agent && chmod +x deploy/start.sh
USER agent
EXPOSE 8000
CMD ["./deploy/start.sh"]
