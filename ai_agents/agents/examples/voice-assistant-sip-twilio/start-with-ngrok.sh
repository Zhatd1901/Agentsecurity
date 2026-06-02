#!/bin/bash

# Start ngrok for WSS support
# This script only starts ngrok with proper WebSocket support

echo "Starting ngrok tunnel for WSS support..."

# Check if ngrok is installed
if ! command -v ngrok &> /dev/null; then
    echo "Warning: ngrok is not installed. Skipping ngrok tunnel."
    echo "Download from: https://ngrok.com/download"
    echo "Without ngrok, Twilio WSS will not work for incoming calls."
    echo "For development without ngrok, ensure your server has a public URL."
    exit 0
fi

# Check if ngrok auth token is set
if [ -z "$NGROK_AUTHTOKEN" ]; then
    echo "Warning: NGROK_AUTHTOKEN environment variable is not set."
    echo "You may need to set it for ngrok to work properly."
    echo "Get your auth token from: https://dashboard.ngrok.com/get-started/your-authtoken"
fi

# Start ngrok with WebSocket support in foreground
echo "Starting ngrok tunnel with WebSocket support..."
echo "Make sure your server is running on port 9000 before testing WSS connections."
echo ""
echo "ngrok will start in foreground. Press Ctrl+C to stop."
echo "Logs will be saved to: /tmp/ngrok.log"
echo ""

# Start ngrok in foreground with verbose output
ngrok start --config=ngrok.yml --log=stdout twilio-server
