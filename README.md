# Xylenix Telegram Bot (Full-Stack)

Telegram referral bot with AI verification, tiered rewards, withdrawals, and FastAPI backend.

## Features
- Deep-link referrals (`/start <referrer_id>`)
- AI verification stub for payment screenshots
- Tiered rewards (≤15: ₹180, >15: ₹190)
- Withdrawal requests (min ₹250)
- FastAPI backend + PostgreSQL (SQLAlchemy)

## Quickstart

1. Create and fill `.env` from example:
```bash
copy .env.example .env  # Windows
```

2. Create virtualenv and install deps:
```bash
python -m venv .venv
. .venv/Scripts/activate  # PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

3. Start backend (FastAPI):
```bash
uvicorn src.backend.main:app --reload --port 8000
```

4. In another terminal, start the bot:
```bash
python -m src.bot.main
```

## Tech
- Python 3.11+
- FastAPI, SQLAlchemy 2.0, PostgreSQL
- python-telegram-bot v21

## Notes
- Database tables are auto-created on first run (no migrations yet).
- AI and QR services are stubbed; integrate Anthropic and QR decoding later.
