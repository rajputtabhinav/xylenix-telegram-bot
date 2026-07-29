#!/bin/bash
# Start All Xylenix Telegram Bots (Multi-Bot Mode)
echo "Starting All Xylenix Telegram Bots..."

# Activate virtual environment
source ./.venv/bin/activate

# Start all bots simultaneously
python -m src.bot.main --multi
