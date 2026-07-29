import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Request, UploadFile, File
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_async_db
from src.services.queue import queue_service
from src.services.cache import cache
from src.config import settings
from src.utils.validation import validate_verification_upload

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

class VerificationRequest(BaseModel):
    user_id: int
    message_id: Optional[int] = None

class VerificationResponse(BaseModel):
    task_id: str
    status: str
    message: str

@router.post("/upload", response_model=VerificationResponse)
@limiter.limit("5/minute")
async def upload_payment_screenshot(
    request: Request,
    user_id: int,
    file: UploadFile = File(...),
    message_id: Optional[int] = None,
    db: AsyncSession = Depends(get_async_db)
):
    """Upload payment screenshot for verification"""
    
    # Comprehensive validation
    validate_verification_upload(user_id, file.content_type or "", file.size or 0)
    
    # Check rate limiting for user
    rate_limit_key = await cache.get_rate_limit_key(user_id, "verification")
    current_attempts = await cache.increment(rate_limit_key, ttl=3600)  # 1 hour window
    
    if current_attempts and current_attempts > 10:  # Max 10 attempts per hour
        raise HTTPException(
            status_code=429, 
            detail="Too many verification attempts. Please try again later."
        )
    
    try:
        # Read image data
        image_data = await file.read()
        
        # Queue verification task
        task_id = queue_service.enqueue_payment_verification(
            user_id=user_id,
            image_data=image_data,
            message_id=message_id or 0
        )
        
        logger.info(f"Queued payment verification for user {user_id}, task {task_id}")
        
        return VerificationResponse(
            task_id=task_id,
            status="queued",
            message="Payment verification has been queued for processing"
        )
        
    except Exception as e:
        logger.error(f"Failed to process verification upload for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to process verification request")

@router.get("/status/{task_id}")
@limiter.limit("60/minute")
async def get_verification_status(
    request: Request,
    task_id: str
):
    """Get verification task status"""
    try:
        task_status = queue_service.get_task_status(task_id)
        
        return {
            "task_id": task_id,
            "status": task_status["status"],
            "result": task_status.get("result"),
            "meta": task_status.get("meta") if task_status["status"] in ["PROCESSING", "SUCCESS"] else None,
            "error": task_status.get("traceback") if task_status["status"] == "FAILURE" else None
        }
        
    except Exception as e:
        logger.error(f"Failed to get task status for {task_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to get verification status")

@router.get("/user/{user_id}/attempts")
@limiter.limit("30/minute")
async def get_user_verification_attempts(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_async_db)
):
    """Get user's verification attempts"""
    
    # Get remaining attempts from rate limit
    rate_limit_key = await cache.get_rate_limit_key(user_id, "verification")
    current_attempts = await cache.get(rate_limit_key) or 0
    remaining_attempts = max(0, 10 - int(current_attempts))
    
    return {
        "user_id": user_id,
        "attempts_used": current_attempts,
        "attempts_remaining": remaining_attempts,
        "reset_time": "1 hour from last attempt"
    }
