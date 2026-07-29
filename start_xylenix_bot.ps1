# Start Xylenix Bot Only
Write-Host "Starting Xylenix Bot..." -ForegroundColor Magenta

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start Xylenix bot
.\.venv\Scripts\python -m src.bot.main --single xylenix
