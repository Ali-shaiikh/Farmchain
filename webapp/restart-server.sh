#!/bin/bash
# Restart FarmChain server

echo "Stopping existing server..."
pkill -f "node.*server.js" 2>/dev/null
pkill -f "nodemon" 2>/dev/null
sleep 2

echo "Starting server..."
cd "$(dirname "$0")"
npm run dev
