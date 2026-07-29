# Start All Xylenix Telegram Bots (Multi-Bot Mode)
Write-Host "Starting All Xylenix Telegram Bots..." -ForegroundColor Green

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start all bots simultaneously
.\.venv\Scripts\python -m src.bot.main --multi
