# Start EarnHive Bot Only
Write-Host "Starting EarnHive Bot..." -ForegroundColor DarkGreen

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start EarnHive bot
.\.venv\Scripts\python -m src.bot.main --single earnhive
