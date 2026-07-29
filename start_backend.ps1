# Start Xylenix Backend Server
Write-Host "Starting Xylenix Backend..." -ForegroundColor Green

# Activate virtual environment
& ".\.venv\Scripts\Activate.ps1"

# Start FastAPI server
.\.venv\Scripts\python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --reload
