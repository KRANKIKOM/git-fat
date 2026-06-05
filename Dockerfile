FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    openssh-client \
    rsync \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src/ ./src/

RUN pip install --no-cache-dir .

ENV PYTHONUNBUFFERED=1
ENV GIT_AUTHOR_NAME=git-fat
ENV GIT_AUTHOR_EMAIL=git-fat@localhost
ENV GIT_COMMITTER_NAME=git-fat
ENV GIT_COMMITTER_EMAIL=git-fat@localhost

ENTRYPOINT ["git-fat"]
