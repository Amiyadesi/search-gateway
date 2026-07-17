ARG BASE_IMAGE=python:3.11.13-slim-bookworm@sha256:86adf8dbadc3d6e82ee5dd2c74bec2e1c2467cdad47886280501df722372d2e1
FROM ${BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DEFAULT_TIMEOUT=180 \
    PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ \
    PIP_TRUSTED_HOST=mirrors.aliyun.com

WORKDIR /app

COPY GrokSearch /tmp/GrokSearch

RUN pip install --no-cache-dir \
    fastapi==0.124.4 \
    uvicorn==0.38.0 \
    /tmp/GrokSearch

COPY bridge.py /app/bridge.py

EXPOSE 8010
CMD ["uvicorn", "bridge:app", "--host", "0.0.0.0", "--port", "8010"]
