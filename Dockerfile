FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Installing Package Dependencies

RUN apt-get update && \
    apt-get install -y curl gnupg && \
    \
    # Node.js 22
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    \
    # Caddy
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https && \
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key \
        | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg && \
    curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt \
        | tee /etc/apt/sources.list.d/caddy-stable.list && \
    apt-get update && \
    apt-get install -y caddy && \
    \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY . .

# Backend
RUN pip install --no-cache-dir -r backend/requirements.txt

# Frontend
WORKDIR /app/frontend
RUN npm install
RUN npm run build

WORKDIR /app

EXPOSE 80

CMD sh -c "\
	cd /app/backend && uvicorn main:app --host 0.0.0.0 --port 8000 & \
	exec caddy run --config /app/Caddyfile"