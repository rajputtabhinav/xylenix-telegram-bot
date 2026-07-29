from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from src.config import settings
import logging

logger = logging.getLogger(__name__)

# Async engine for high-performance operations
if "sqlite" in settings.database_url:
	# SQLite configuration (no connection pooling)
	async_engine = create_async_engine(
		settings.database_url,
		echo=settings.env == "development"
	)
else:
	# PostgreSQL configuration with connection pooling
	async_engine = create_async_engine(
		settings.database_url,
		pool_size=settings.db_pool_size,
		max_overflow=settings.db_max_overflow,
		pool_pre_ping=True,
		pool_recycle=3600,  # Recycle connections every hour
		echo=settings.env == "development"
	)

AsyncSessionLocal = async_sessionmaker(
	bind=async_engine,
	class_=AsyncSession,
	expire_on_commit=False,
	autoflush=False,
	autocommit=False
)

# Sync engine for compatibility (migrations, etc.)
if "sqlite" in settings.database_url:
	# SQLite sync engine
	sync_engine = create_engine(
		settings.database_url.replace("+aiosqlite", ""),
		echo=settings.env == "development"
	)
else:
	# PostgreSQL sync engine with connection pooling
	sync_engine = create_engine(
		settings.database_url.replace("+asyncpg", "").replace("+psycopg", ""),
		pool_size=settings.db_pool_size,
		max_overflow=settings.db_max_overflow,
		pool_pre_ping=True,
		pool_recycle=3600
	)

SessionLocal = sessionmaker(bind=sync_engine, autoflush=False, autocommit=False)


@asynccontextmanager
async def get_async_db():
	"""Async database session context manager for high-performance operations."""
	async with AsyncSessionLocal() as session:
		try:
			yield session
			# Don't auto-commit - let the calling code handle commits
		except Exception as e:
			logger.error(f"Database error: {e}")
			await session.rollback()
			raise
		finally:
			await session.close()


def get_db():
	"""Sync database session context manager for compatibility."""
	from contextlib import contextmanager

	@contextmanager
	def _session_scope():
		session = SessionLocal()
		try:
			yield session
			session.commit()
		except Exception as e:
			logger.error(f"Database error: {e}")
			session.rollback()
			raise
		finally:
			session.close()

	return _session_scope()
