import logging
from typing import Any, Dict, Optional
from src.config import settings
import uuid
import asyncio

logger = logging.getLogger(__name__)

# Try to initialize Celery, fall back to mock if Redis unavailable
try:
    from celery import Celery
    celery_app = Celery(
        "xylenix",
        broker=settings.celery_broker_url,
        backend=settings.celery_result_backend,
        include=["src.services.tasks"]
    )
    CELERY_AVAILABLE = True
except Exception as e:
    logger.warning(f"Celery initialization failed: {e}. Using mock queue service.")
    celery_app = None
    CELERY_AVAILABLE = False

# Celery configuration
if celery_app:
    celery_app.conf.update(
        task_serializer="json",
        accept_content=["json"],
        result_serializer="json",
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_time_limit=30 * 60,  # 30 minutes
        task_soft_time_limit=25 * 60,  # 25 minutes
        worker_prefetch_multiplier=1,
        task_acks_late=True,
        worker_disable_rate_limits=False,
        task_routes={
            "src.services.tasks.verify_payment_task": {"queue": "verification"},
            "src.services.tasks.process_withdrawal_task": {"queue": "withdrawals"},
            "src.services.tasks.update_referral_stats_task": {"queue": "stats"},
        },
    )

class QueueService:
    """Service for managing background tasks"""
    
    @staticmethod
    def enqueue_payment_verification(user_id: int, image_data: bytes, message_id: int) -> str:
        """Queue payment verification task"""
        if CELERY_AVAILABLE:
            try:
                from src.services.tasks import verify_payment_task
                task = verify_payment_task.delay(
                    user_id=user_id,
                    image_data=image_data,
                    message_id=message_id
                )
                logger.info(f"Queued payment verification task {task.id} for user {user_id}")
                return task.id
            except Exception as e:
                logger.error(f"Failed to queue verification task: {e}")
        
        # Mock implementation for development
        task_id = str(uuid.uuid4())
        logger.info(f"Mock: Queued payment verification task {task_id} for user {user_id}")
        
        # Process immediately for development (in real production, this would be async)
        asyncio.create_task(_mock_process_verification(user_id, image_data, message_id, task_id))
        return task_id

    @staticmethod
    def enqueue_withdrawal_processing(withdrawal_id: int) -> str:
        """Queue withdrawal processing task"""
        from src.services.tasks import process_withdrawal_task
        
        task = process_withdrawal_task.delay(withdrawal_id=withdrawal_id)
        logger.info(f"Queued withdrawal processing task {task.id} for withdrawal {withdrawal_id}")
        return task.id

    @staticmethod
    def enqueue_referral_stats_update(user_id: int) -> str:
        """Queue referral statistics update"""
        from src.services.tasks import update_referral_stats_task
        
        task = update_referral_stats_task.delay(user_id=user_id)
        logger.info(f"Queued referral stats update task {task.id} for user {user_id}")
        return task.id

    @staticmethod
    def get_task_status(task_id: str) -> Dict[str, Any]:
        """Get task status"""
        if CELERY_AVAILABLE and celery_app:
            try:
                task = celery_app.AsyncResult(task_id)
                return {
                    "task_id": task_id,
                    "status": task.status,
                    "result": task.result if task.ready() else None,
                    "traceback": task.traceback if task.failed() else None,
                }
            except Exception as e:
                logger.error(f"Failed to get task status: {e}")
        
        # Mock status for development
        return {
            "task_id": task_id,
            "status": "SUCCESS",
            "result": {"status": "mock_completed", "message": "Development mode - verification skipped"},
            "traceback": None,
        }

async def _mock_process_verification(user_id: int, image_data: bytes, message_id: int, task_id: str):
    """Mock verification processing for development"""
    await asyncio.sleep(2)  # Simulate processing time
    logger.info(f"Mock: Completed verification task {task_id} for user {user_id}")
    # In real implementation, this would update the database with verification results

# Global queue service instance
queue_service = QueueService()
