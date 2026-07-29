#!/bin/bash
# Start PayPulse Bot Only
echo "Starting PayPulse Bot..."

# Activate virtual environment
source ./.venv/bin/activate

# Start PayPulse bot
python -m src.bot.main --single paypulse
