FROM python:3.11-slim

# Upgrade system packages to patch OS-level vulnerabilities
RUN apt-get update && apt-get upgrade -y && rm -rf /var/lib/apt-get/lists/*

WORKDIR /app
COPY requirements.txt .

# Force pip to ignore pre-installed system packages and forcibly overwrite them
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir --upgrade --ignore-installed \
    "setuptools>=78.1.1" \
    "wheel>=0.48.0" \
    "jaraco.context>=6.1.2" \
    "msgpack>=1.2.1" \
 && python -m pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=8080
EXPOSE 8080

CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT}