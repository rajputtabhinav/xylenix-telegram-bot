#!/bin/bash
# Start QuickMint Bot Only
echo "Starting QuickMint Bot..."

# Activate virtual environment
source ./.venv/bin/activate

# Start QuickMint bot
python -m src.bot.main --single quickmint
