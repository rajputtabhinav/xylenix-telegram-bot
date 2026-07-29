import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone

from src.db.session import get_async_db
from src.db.models import User, WithdrawalRequest
from src.services.queue import queue_service
from src.services.cache import cache
from src.config import settings
from src.utils.validation import validate_withdrawal_request

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

class WithdrawalCreateRequest(BaseModel):
    user_id: int
    bot_id: str = "paypulse"  # Default bot
    amount: int
    upi_id: str

class WithdrawalResponse(BaseModel):
    req_id: int
    user_id: int
    amount: int
    upi_id: str
    status: str
    requested_at: str
    processed_at: Optional[str] = None
    notes: Optional[str] = None

@router.post("/request", response_model=WithdrawalResponse)
@limiter.limit("5/hour")  # Very restrictive rate limit
async def create_withdrawal_request(
    request: Request,
    withdrawal_request: WithdrawalCreateRequest,
    db: AsyncSession = Depends(get_async_db)
):
    """Create a new withdrawal request"""
    
    # Comprehensive validation
    validate_withdrawal_request(
        withdrawal_request.user_id,
        withdrawal_request.amount,
        withdrawal_request.upi_id,
        settings.min_withdrawal_inr
    )
    
    async with db:
        # Get user and check balance (with bot isolation)
        user_result = await db.execute(
            select(User).where(
                User.user_id == withdrawal_request.user_id,
                User.bot_id == withdrawal_request.bot_id
            )
        )
        user = user_result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if not user.is_verified:
            raise HTTPException(status_code=400, detail="User must be verified to withdraw")
        
        if user.total_earned < withdrawal_request.amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Available: ₹{user.total_earned}"
            )
        
        # Check for pending withdrawals (with bot isolation)
        pending_result = await db.execute(
            select(WithdrawalRequest)
            .where(
                WithdrawalRequest.user_id == withdrawal_request.user_id,
                WithdrawalRequest.bot_id == withdrawal_request.bot_id,
                WithdrawalRequest.status == "pending"
            )
        )
        pending_withdrawal = pending_result.scalar_one_or_none()
        
        if pending_withdrawal:
            raise HTTPException(
                status_code=400,
                detail="You already have a pending withdrawal request"
            )
        
        # Create withdrawal request (with bot isolation)
        new_withdrawal = WithdrawalRequest(
            user_id=withdrawal_request.user_id,
            bot_id=withdrawal_request.bot_id,
            amount=withdrawal_request.amount,
            upi_id=withdrawal_request.upi_id,
            status="pending",
            requested_at=datetime.now(timezone.utc)
        )
        
        db.add(new_withdrawal)
        await db.commit()
        await db.refresh(new_withdrawal)
        
        # Queue processing task
        task_id = queue_service.enqueue_withdrawal_processing(new_withdrawal.req_id)
        
        logger.info(
            f"Created withdrawal request {new_withdrawal.req_id} for user {withdrawal_request.user_id}, "
            f"amount ₹{withdrawal_request.amount}, task {task_id}"
        )
        
        return WithdrawalResponse(
            req_id=new_withdrawal.req_id,
            user_id=new_withdrawal.user_id,
            amount=new_withdrawal.amount,
            upi_id=new_withdrawal.upi_id,
            status=new_withdrawal.status,
            requested_at=new_withdrawal.requested_at.isoformat(),
            processed_at=None,
            notes=None
        )

@router.get("/user/{user_id}", response_model=List[WithdrawalResponse])
@limiter.limit("30/minute")
async def get_user_withdrawals(
    request: Request,
    user_id: int,
    db: AsyncSession = Depends(get_async_db)
):
    """Get user's withdrawal history"""
    
    async with db:
        result = await db.execute(
            select(WithdrawalRequest)
            .where(WithdrawalRequest.user_id == user_id)
            .order_by(WithdrawalRequest.requested_at.desc())
        )
        withdrawals = result.scalars().all()
        
        withdrawal_list = []
        for withdrawal in withdrawals:
            withdrawal_list.append(WithdrawalResponse(
                req_id=withdrawal.req_id,
                user_id=withdrawal.user_id,
                amount=withdrawal.amount,
                upi_id=withdrawal.upi_id,
                status=withdrawal.status,
                requested_at=withdrawal.requested_at.isoformat(),
                processed_at=withdrawal.processed_at.isoformat() if withdrawal.processed_at else None,
                notes=withdrawal.notes
            ))
        
        return withdrawal_list

@router.get("/{withdrawal_id}", response_model=WithdrawalResponse)
@limiter.limit("60/minute")
async def get_withdrawal(
    request: Request,
    withdrawal_id: int,
    db: AsyncSession = Depends(get_async_db)
):
    """Get specific withdrawal request"""
    
    async with db:
        result = await db.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.req_id == withdrawal_id)
        )
        withdrawal = result.scalar_one_or_none()
        
        if not withdrawal:
            raise HTTPException(status_code=404, detail="Withdrawal request not found")
        
        return WithdrawalResponse(
            req_id=withdrawal.req_id,
            user_id=withdrawal.user_id,
            amount=withdrawal.amount,
            upi_id=withdrawal.upi_id,
            status=withdrawal.status,
            requested_at=withdrawal.requested_at.isoformat(),
            processed_at=withdrawal.processed_at.isoformat() if withdrawal.processed_at else None,
            notes=withdrawal.notes
        )
