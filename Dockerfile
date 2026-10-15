# WedVite: Production Dockerfile for Render Web Service
# Runs both Python 3 Flask (Gunicorn) and Node.js Baileys WhatsApp microservice
FROM node:20-bookworm-slim

# Install Python 3, pip, and curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 1. Install Python dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir --break-system-packages -r requirements.txt

# 2. Install Node.js dependencies for WhatsApp engine
COPY whatsapp_service/package*.json ./whatsapp_service/
RUN cd whatsapp_service && npm install --omit=dev

# 3. Copy application code
COPY . .

# 4. Prepare start script
RUN chmod +x start.sh

# Render binds to $PORT dynamically
EXPOSE 5000

CMD ["./start.sh"]
