# Start PayPulse Bot Only
Write-Host "Starting PayPulse Bot..." -ForegroundColor Cyan

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start PayPulse bot
.\.venv\Scripts\python -m src.bot.main --single paypulse
