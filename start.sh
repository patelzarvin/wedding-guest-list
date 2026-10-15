#!/bin/bash
set -e

echo "=========================================================="
echo "💍 Starting WedVite Royal Wedding Guest Hub & WhatsApp AI"
echo "=========================================================="

export FLASK_PORT=${PORT:-5000}
export BAILEYS_PORT=5001

echo "[*] Starting Baileys WhatsApp service on port $BAILEYS_PORT..."
(cd /app/whatsapp_service && node server.js) &

sleep 2

echo "[*] Starting Flask Production Web App on port $FLASK_PORT..."
cd /app && exec gunicorn --bind 0.0.0.0:$FLASK_PORT --workers 1 --threads 4 --timeout 120 app:app
