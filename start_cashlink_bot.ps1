# Start CashLink Bot Only
Write-Host "Starting CashLink Bot..." -ForegroundColor Green

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start CashLink bot
.\.venv\Scripts\python -m src.bot.main --single cashlink