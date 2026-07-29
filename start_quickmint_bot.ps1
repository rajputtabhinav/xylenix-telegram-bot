# Start QuickMint Bot Only
Write-Host "Starting QuickMint Bot..." -ForegroundColor Yellow

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start QuickMint bot
.\.venv\Scripts\python -m src.bot.main --single quickmint
