#!/bin/bash
# Start CashLink Bot Only
echo "Starting CashLink Bot..."

# Activate virtual environment
source ./.venv/bin/activate

# Start CashLink bot
python -m src.bot.main --single cashlink