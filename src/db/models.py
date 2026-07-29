from datetime import datetime, timezone
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, Index, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
from sqlalchemy.dialects.postgresql import UUID
import uuid

Base = declarative_base()


class User(Base):
	__tablename__ = "users"

	user_id = Column(BigInteger, primary_key=True, index=True)  # Telegram ID
	bot_id = Column(String(50), primary_key=True, index=True, default="paypulse")  # Bot identifier for isolation
	username = Column(String(255), nullable=True, index=True)  # Index for username searches
	referred_by = Column(BigInteger, nullable=True, index=True)  # Removed FK constraint for multi-bot
	joined_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	referrals_count = Column(Integer, default=0, nullable=False, index=True)  # Index for leaderboards
	total_earned = Column(Integer, default=0, nullable=False)
	upi_id = Column(String(255), nullable=True, index=True)  # Index for UPI lookups
	is_verified = Column(Boolean, default=False, nullable=False, index=True)  # Index for verification status
	last_activity = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	verification_attempts = Column(Integer, default=0, nullable=False)
	is_banned = Column(Boolean, default=False, nullable=False, index=True)

	# Note: Relationships are complex with multi-bot architecture
	# We'll handle relationships programmatically rather than using SQLAlchemy relationships
	# transactions = relationship("Transaction", back_populates="user", lazy="select")
	# withdrawal_requests = relationship("WithdrawalRequest", back_populates="user", lazy="select")

	# Composite indexes for common queries
	__table_args__ = (
		Index('idx_user_bot_referred_verified', 'bot_id', 'referred_by', 'is_verified'),
		Index('idx_user_bot_joined_verified', 'bot_id', 'joined_at', 'is_verified'),
		Index('idx_user_bot_referrals_earned', 'bot_id', 'referrals_count', 'total_earned'),
		Index('idx_user_bot_id', 'bot_id', 'user_id'),  # Composite primary key index
	)


class Transaction(Base):
	__tablename__ = "transactions"

	txn_id = Column(Integer, primary_key=True, autoincrement=True)
	user_id = Column(BigInteger, nullable=False, index=True)  # Removed FK constraint for multi-bot
	bot_id = Column(String(50), nullable=False, index=True, default="paypulse")  # Bot identifier
	type = Column(String(32), nullable=False, index=True)  # join_fee / referral_payout
	amount = Column(Integer, nullable=False)
	status = Column(String(32), default="pending", nullable=False, index=True)  # pending/verified/paid
	created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc), nullable=False)
	verification_data = Column(Text, nullable=True)  # Store AI verification results as JSON
	reference_id = Column(String(255), nullable=True, index=True)  # External reference (UPI transaction ID)

	# user = relationship("User", back_populates="transactions", lazy="select")  # Removed for multi-bot

	# Composite indexes for analytics and reporting
	__table_args__ = (
		Index('idx_txn_bot_user_status_created', 'bot_id', 'user_id', 'status', 'created_at'),
		Index('idx_txn_bot_type_status_created', 'bot_id', 'type', 'status', 'created_at'),
		Index('idx_txn_bot_created_amount', 'bot_id', 'created_at', 'amount'),
		Index('idx_txn_user_bot', 'user_id', 'bot_id'),  # For relationship queries
	)


class WithdrawalRequest(Base):
	__tablename__ = "withdrawal_requests"

	req_id = Column(Integer, primary_key=True, autoincrement=True)
	user_id = Column(BigInteger, nullable=False, index=True)  # Removed FK constraint for multi-bot
	bot_id = Column(String(50), nullable=False, index=True, default="paypulse")  # Bot identifier
	amount = Column(Integer, nullable=False)
	upi_id = Column(String(255), nullable=False, index=True)
	status = Column(String(32), default="pending", nullable=False, index=True)  # pending/processing/paid/rejected
	requested_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	processed_at = Column(DateTime, nullable=True)
	processed_by = Column(String(255), nullable=True)  # Admin who processed
	notes = Column(Text, nullable=True)  # Admin notes or rejection reason
	transaction_ref = Column(String(255), nullable=True)  # Payment reference

	# user = relationship("User", back_populates="withdrawal_requests", lazy="select")  # Removed for multi-bot

	__table_args__ = (
		Index('idx_withdrawal_bot_status_requested', 'bot_id', 'status', 'requested_at'),
		Index('idx_withdrawal_bot_user_status', 'bot_id', 'user_id', 'status'),
		Index('idx_withdrawal_user_bot', 'user_id', 'bot_id'),  # For relationship queries
	)


class UserSession(Base):
	"""Track user sessions and state for complex workflows"""
	__tablename__ = "user_sessions"

	session_id = Column(String(255), primary_key=True, default=lambda: str(uuid.uuid4()))
	user_id = Column(BigInteger, nullable=False, index=True)  # Removed FK constraint for multi-bot
	bot_id = Column(String(50), nullable=False, index=True, default="paypulse")  # Bot identifier
	state = Column(String(50), nullable=False)  # awaiting_payment, awaiting_upi, etc.
	data = Column(Text, nullable=True)  # JSON data for session context
	created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
	expires_at = Column(DateTime, nullable=False, index=True)

	__table_args__ = (
		Index('idx_session_bot_user_state', 'bot_id', 'user_id', 'state'),
		Index('idx_session_bot_expires', 'bot_id', 'expires_at'),
	)


class SystemMetrics(Base):
	"""Store system metrics for monitoring"""
	__tablename__ = "system_metrics"

	metric_id = Column(Integer, primary_key=True, autoincrement=True)
	metric_name = Column(String(100), nullable=False, index=True)
	metric_value = Column(Integer, nullable=False)
	timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	metric_metadata = Column(Text, nullable=True)  # JSON for additional context

	__table_args__ = (
		Index('idx_metrics_name_timestamp', 'metric_name', 'timestamp'),
	)


class ScreenshotMetadata(Base):
	"""Store screenshot metadata for duplicate prevention"""
	__tablename__ = "screenshot_metadata"

	id = Column(Integer, primary_key=True, autoincrement=True)
	user_id = Column(BigInteger, nullable=False, index=True)
	bot_id = Column(String(50), nullable=False, index=True, default="paypulse")  # Bot identifier
	image_hash = Column(String(64), nullable=False, index=True)  # SHA256 hash (removed unique constraint for multi-bot)
	perceptual_hash = Column(String(16), nullable=False, index=True)  # pHash for similar images
	file_size = Column(Integer, nullable=False)
	image_dimensions = Column(String(20), nullable=False)  # "width x height"
	verification_status = Column(String(20), nullable=False, index=True)  # pending, approved, rejected
	verification_result = Column(Text, nullable=True)  # AI verification details as JSON
	uploaded_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False, index=True)
	verified_at = Column(DateTime, nullable=True)
	
	__table_args__ = (
		Index('idx_screenshot_bot_user_status', 'bot_id', 'user_id', 'verification_status'),
		Index('idx_screenshot_bot_uploaded_verified', 'bot_id', 'uploaded_at', 'verified_at'),
		UniqueConstraint('bot_id', 'image_hash', name='uq_bot_image_hash'),  # Unique per bot
	)
