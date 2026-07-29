#!/bin/bash
# Start EarnHive Bot Only
echo "Starting EarnHive Bot..."

# Activate virtual environment
source ./.venv/bin/activate

# Start EarnHive bot
python -m src.bot.main --single earnhive
