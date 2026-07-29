# Multi-Bot Architecture Implementation

## Overview

This implementation adds support for running multiple Telegram bots (PayPulseBot, QuickMintBot, CashLinkBot, EarnHiveBot) on the same server with complete database isolation. Each bot operates independently with its own user base, referrals, and earnings.

## Key Features

### ✅ Complete Bot Isolation
- **Database Isolation**: Each bot has its own isolated data using `bot_id` field
- **User Isolation**: Users are isolated per bot (same Telegram user can join multiple bots independently)
- **Referral Isolation**: Referral links and tracking are bot-specific
- **Payment Verification Isolation**: Screenshots are verified separately per bot

### ✅ Multi-Bot Support
- **PayPulseBot**: `bot_id: paypulse`
- **QuickMintBot**: `bot_id: quickmint`  
- **CashLinkBot**: `bot_id: cashlink`
- **EarnHiveBot**: `bot_id: earnhive`

### ✅ Flexible Deployment Options
- **Multi-Bot Mode**: Run all bots simultaneously
- **Single Bot Mode**: Run individual bots for testing/development
- **Backward Compatibility**: Existing single-bot setup still works

## Architecture Changes

### 1. Database Models Enhanced
All models now include `bot_id` field for isolation:
- `User` table: Primary key is now (`user_id`, `bot_id`)
- `Transaction`, `WithdrawalRequest`, `UserSession`, `ScreenshotMetadata`: All include `bot_id`
- **Foreign Key Constraints**: Removed for cross-bot compatibility

### 2. Multi-Bot Manager
- **`MultiBotManager`**: Coordinates multiple bot instances
- **`IsolatedXylenixBot`**: Individual bot instance with full isolation
- **Thread-based**: Each bot runs in its own thread for true parallelism

### 3. Configuration System
- **Environment Variables**: Support for multiple bot tokens
- **BotConfig Class**: Individual bot configuration management
- **Dynamic Loading**: Automatically loads available bot configurations

## Environment Configuration

### Required Environment Variables

```env
# PayPulseBot
PAYPULSE_BOT_TOKEN=7969074108:AAEwgEH6yJ3falKq4OfhVrYggJnivn2AO-o
PAYPULSE_BOT_USERNAME=PayPulseBot
PAYPULSE_BOT_ID=paypulse

# QuickMintBot
QUICKMINT_BOT_TOKEN=8462242994:AAGVnJaS29b80luzWBeLvKtrOvwYW44lSnw
QUICKMINT_BOT_USERNAME=QuickMintBot
QUICKMINT_BOT_ID=quickmint

# CashLinkBot
CASHLINK_BOT_TOKEN=8462242994:AAGVnJaS29b80luzWBeLvKtrOvwYW44lSnw
CASHLINK_BOT_USERNAME=CashLinkBot
CASHLINK_BOT_ID=cashlink

# EarnHiveBot
EARNHIVE_BOT_TOKEN=8430596216:AAFjDGNVweB59Ao_1Z6ypcnayzedGbFp1sY
EARNHIVE_BOT_USERNAME=EarnHiveBot
EARNHIVE_BOT_ID=earnhive

# Default configuration (backward compatibility)
BOT_TOKEN=7969074108:AAEwgEH6yJ3falKq4OfhVrYggJnivn2AO-o
BOT_USERNAME=PayPulseBot
BOT_ID=paypulse

# Other settings remain the same...
DATABASE_URL=sqlite+aiosqlite:///./xylenix.db
REDIS_URL=redis://localhost:6379
# ... etc
```

## Running the Bots

### Multi-Bot Mode (All Bots)
```bash
# Windows
.\start_bot.ps1

# Linux/Mac
./start_all_bots.sh

# Direct Python
python -m src.bot.main --multi
```

### Single Bot Mode
```bash
# Windows
.\start_paypulse_bot.ps1
.\start_quickmint_bot.ps1  
.\start_cashlink_bot.ps1
.\start_earnhive_bot.ps1

# Linux/Mac
./start_paypulse_bot.sh
./start_quickmint_bot.sh
./start_cashlink_bot.sh
./start_earnhive_bot.sh

# Direct Python
python -m src.bot.main --single paypulse
python -m src.bot.main --single quickmint
python -m src.bot.main --single cashlink
python -m src.bot.main --single earnhive
```

## User Experience

### Complete Independence
- **New User Journey**: User joins PayPulseBot → completely separate from QuickMintBot
- **Referral Links**: `t.me/PayPulseBot?start=123` vs `t.me/QuickMintBot?start=123`
- **Earnings**: User can have different earnings/referrals on each bot
- **Verification**: Must verify separately for each bot they want to use

### Example User Flow
1. **User A** joins **PayPulseBot** → gets verified → earns ₹500
2. **Same User A** joins **QuickMintBot** → starts fresh → separate verification → separate earnings
3. **User B** gets referred to **PayPulseBot** by **User A** → **User A** earns on PayPulseBot
4. **User B** separately joins **QuickMintBot** → completely independent experience

## API Changes

### Backend API Updates
All API endpoints now support `bot_id` parameter:

```bash
# Get user info for specific bot
GET /api/v1/users/123?bot_id=paypulse

# Get referrals for specific bot
GET /api/v1/users/123/referrals?bot_id=quickmint

# Global stats per bot
GET /api/v1/users/stats/global?bot_id=cashlink
```

## Database Schema Updates

### Migration Required
The database schema has been updated. You'll need to run migrations:

```python
# Database will be automatically updated when you first run
# Or you can manually run:
from src.db.init_db import init_db
init_db()
```

### New Indexes
Added optimized indexes for multi-bot queries:
- `idx_user_bot_id` (bot_id, user_id)
- `idx_txn_bot_user_status_created` 
- `idx_withdrawal_bot_user_status`
- And more for optimal performance

## Monitoring & Management

### Bot Status Monitoring
```python
from src.bot.multi_bot_manager import multi_bot_manager

# Get status of all bots
status = multi_bot_manager.get_bot_status()
print(status)
```

### Individual Bot Management
```python
# Restart specific bot
multi_bot_manager.restart_bot("paypulse")

# Get specific bot instance
bot = multi_bot_manager.get_bot_instance("quickmint")
```

## Security & Isolation

### ✅ Complete Data Separation
- **Users**: Cannot cross-reference between bots
- **Payments**: Screenshot verification isolated per bot
- **Referrals**: Referral chains are bot-specific
- **Admin**: Withdrawal requests show which bot they're from

### ✅ Cache Isolation
- Cache keys include bot_id: `paypulse:user:123`
- Rate limiting per bot-user combination
- Session management isolated per bot

## Performance

### Optimizations
- **Concurrent Execution**: All bots run simultaneously
- **Optimized Indexes**: Database queries optimized for multi-bot
- **Caching Strategy**: Smart caching with bot isolation
- **Thread Management**: Proper thread isolation and cleanup

## Backward Compatibility

### ✅ Existing Setup Support
- Default bot configuration uses existing environment variables
- Single bot mode still works exactly as before
- Database migration is automatic and safe
- API endpoints work with default bot_id if not specified

## Troubleshooting

### Common Issues

1. **Bot Not Starting**
   - Check token is valid: `python -c "import requests; print(requests.get('https://api.telegram.org/bot<TOKEN>/getMe'))"`
   - Verify environment variables are loaded correctly

2. **Database Issues** 
   - Ensure bot_id field is properly set (default: "paypulse")
   - Check database migrations ran successfully

3. **Isolation Problems**
   - Verify API calls include correct bot_id parameter
   - Check cache keys include bot_id prefix

### Logs
Each bot logs with its own prefix:
```
2024-01-01 10:00:00 - Bot-paypulse - INFO - PayPulseBot started
2024-01-01 10:00:01 - Bot-quickmint - INFO - QuickMintBot started  
2024-01-01 10:00:02 - Bot-cashlink - INFO - CashLinkBot started
2024-01-01 10:00:03 - Bot-earnhive - INFO - EarnHiveBot started
```

## Next Steps

### Production Deployment
1. **Set Environment Variables**: Copy `env.example` to `.env` and configure
2. **Run Migration**: Database will auto-migrate on first run
3. **Start All Bots**: Use `start_bot.ps1` or `start_all_bots.sh`
4. **Monitor**: Check logs for successful startup of all bots

### Scaling
- **Load Balancing**: Each bot can be deployed on separate servers if needed
- **Database Sharding**: Can be done per bot_id for extreme scale
- **Caching**: Redis can be configured per bot if needed

---

## Summary

✅ **Complete Multi-Bot Implementation**
✅ **Full Database Isolation** 
✅ **Independent User Experiences**
✅ **Backward Compatibility**
✅ **Production Ready**

The system now supports running multiple bots with complete isolation while maintaining the existing functionality for single-bot setups.
