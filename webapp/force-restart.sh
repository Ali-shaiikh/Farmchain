#!/bin/bash
echo "=== Force Restarting FarmChain Server ==="

# Kill all node processes related to server
echo "Stopping all Node.js processes..."
pkill -9 -f "node.*server.js" 2>/dev/null
pkill -9 -f "nodemon" 2>/dev/null
sleep 2

# Verify port 3000 is free
lsof -ti:3000 | xargs kill -9 2>/dev/null
sleep 1

echo "Starting server..."
cd "$(dirname "$0")"
npm run dev
