#!/bin/sh

# this file is necessary for the fly.io deployment to work, as it starts tailscaled and then the bot
# Start tailscaled in the background
/app/tailscaled --state=/var/lib/tailscale/tailscaled.state --socket=/var/run/tailscale/tailscaled.sock &

# Wait a moment for tailscaled to initialize
sleep 2

# Connect to tailnet
/app/tailscale up --auth-key=${TAILSCALE_AUTHKEY} --hostname=faebot-discord

# Run the bot (this replaces the shell process so signals work properly)
exec /usr/bin/python3.13 /app/faediscord.py