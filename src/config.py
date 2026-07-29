import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv(override=True)


@dataclass
class BotConfig:
	"""Configuration for a single bot instance"""
	bot_id: str
	token: str
	username: str
	
	def __post_init__(self):
		if not self.token:
			raise ValueError(f"Bot token is required for {self.bot_id}")
		if not self.username:
			raise ValueError(f"Bot username is required for {self.bot_id}")

@dataclass 
class Settings:
	# Default Bot Configuration (backward compatibility)
	bot_token: str = os.getenv("BOT_TOKEN", "")
	bot_username: str = os.getenv("BOT_USERNAME", "Pay_PulseBot")
	bot_id: str = os.getenv("BOT_ID", "paypulse")
	
	# Multi-Bot Configurations
	def get_bot_configs(self) -> list[BotConfig]:
		"""Get all available bot configurations"""
		bots = []
		
		# PayPulseBot
		paypulse_token = os.getenv("PAYPULSE_BOT_TOKEN", "")
		if paypulse_token:
			bots.append(BotConfig(
				bot_id="paypulse",
				token=paypulse_token,
				username=os.getenv("PAYPULSE_BOT_USERNAME", "Pay_PulseBot")
			))
		
		# QuickMintBot
		quickmint_token = os.getenv("QUICKMINT_BOT_TOKEN", "")
		if quickmint_token:
			bots.append(BotConfig(
				bot_id="quickmint", 
				token=quickmint_token,
				username=os.getenv("QUICKMINT_BOT_USERNAME", "Quick_MintBot")
			))
			
		# CashLinkBot
		cashlink_token = os.getenv("CASHLINK_BOT_TOKEN", "")
		if cashlink_token:
			bots.append(BotConfig(
				bot_id="cashlink",
				token=cashlink_token,
				username=os.getenv("CASHLINK_BOT_USERNAME", "Cash_LinkBot")
			))
			
		# EarnHiveBot
		earnhive_token = os.getenv("EARNHIVE_BOT_TOKEN", "")
		if earnhive_token:
			bots.append(BotConfig(
				bot_id="earnhive",
				token=earnhive_token,
				username=os.getenv("EARNHIVE_BOT_USERNAME", "Earn_HiveBot")
			))
		
		# XylenixBot (5th bot)
		xylenix_token = os.getenv("XYLENIX_BOT_TOKEN", "")
		if xylenix_token:
			bots.append(BotConfig(
				bot_id="xylenix",
				token=xylenix_token,
				username=os.getenv("XYLENIX_BOT_USERNAME", "xylenixbot")
			))
		
		# Fallback to default bot if no multi-bots configured
		if not bots and self.bot_token:
			bots.append(BotConfig(
				bot_id=self.bot_id,
				token=self.bot_token,
				username=self.bot_username
			))
			
		return bots

	# Database Configuration
	database_url: str = os.getenv(
		"DATABASE_URL",
		"sqlite+aiosqlite:///./xylenix.db",  # Default to SQLite for development
	)
	
	# Redis Configuration
	redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379")
	
	# Celery Configuration
	celery_broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
	celery_result_backend: str = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")

	# App Configuration
	app_secret_key: str = os.getenv("APP_SECRET_KEY", "change-me")
	env: str = os.getenv("ENV", "development")
	max_workers: int = int(os.getenv("MAX_WORKERS", "4"))
	db_pool_size: int = int(os.getenv("DB_POOL_SIZE", "20"))
	db_max_overflow: int = int(os.getenv("DB_MAX_OVERFLOW", "0"))

	# Business Rules
	join_fee_inr: int = int(os.getenv("JOIN_FEE_INR", "200"))
	welcome_bonus_inr: int = int(os.getenv("WELCOME_BONUS_INR", "70"))  # NEW: Welcome bonus for new users
	tier1_threshold: int = int(os.getenv("TIER1_THRESHOLD", "15"))
	tier1_reward_inr: int = int(os.getenv("TIER1_REWARD_INR", "110"))  # CHANGED: Reduced from 180 to 110
	tier2_reward_inr: int = int(os.getenv("TIER2_REWARD_INR", "190"))
	min_withdrawal_inr: int = int(os.getenv("MIN_WITHDRAWAL_INR", "250"))
	receiver_upi_ids: list[str] = field(default_factory=lambda: os.getenv("RECEIVER_UPI_IDS", "abhinavrajput2424@axl,abhinavrajput24241@ybl").split(','))
	payee_name: str = os.getenv("PAYEE_NAME", "Xylenix Project")
	
	# Telegram Channel
	telegram_channel_url: str = os.getenv("TELEGRAM_CHANNEL_URL", "https://t.me/myearnhive")
	telegram_channel_name: str = os.getenv("TELEGRAM_CHANNEL_NAME", "MyEarnHive")

	# External APIs
	anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
	anthropic_model: str = os.getenv("ANTHROPIC_MODEL", "claude-3-5-sonnet-20241022")
	openai_api_key: str = os.getenv("OPENAI_API_KEY", "")  # Deprecated - using Anthropic now
	
	# Admin Security
	admin_token: str = os.getenv("ADMIN_TOKEN", "admin-secret-token-change-me")
	admin_username: str = os.getenv("ADMIN_USERNAME", "admin")
	admin_password: str = os.getenv("ADMIN_PASSWORD", "change-me-password")
	admin_chat_id: str = os.getenv("ADMIN_CHAT_ID", "")
	
	# Rate Limiting
	rate_limit_requests: int = int(os.getenv("RATE_LIMIT_REQUESTS", "100"))
	rate_limit_window: int = int(os.getenv("RATE_LIMIT_WINDOW", "60"))  # seconds
	
	# Cache TTL (Time To Live)
	cache_ttl_user: int = int(os.getenv("CACHE_TTL_USER", "300"))  # 5 minutes
	cache_ttl_referrals: int = int(os.getenv("CACHE_TTL_REFERRALS", "60"))  # 1 minute


settings = Settings()
