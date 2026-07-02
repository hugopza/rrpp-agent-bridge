FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install --no-install-recommends --yes age \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 rrpp \
    && useradd --uid 10001 --gid rrpp --create-home --home-dir /home/rrpp rrpp

WORKDIR /app
COPY pyproject.toml README.md ./
COPY rrpp_bridge ./rrpp_bridge
RUN python -m pip install ".[deployment]"

USER 10001:10001
EXPOSE 8080 8081

CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--workers", "1", "--threads", "4", "--timeout", "30", "--graceful-timeout", "30", "rrpp_bridge.wsgi:application"]
