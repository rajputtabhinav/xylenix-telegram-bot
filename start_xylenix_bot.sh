#!/bin/bash
# Start Xylenix Bot Only
echo "Starting Xylenix Bot..."

# Activate virtual environment
source ./.venv/bin/activate

# Start Xylenix bot
python -m src.bot.main --single xylenix
