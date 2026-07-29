import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Request, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from sqlalchemy import select, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from datetime import datetime, timezone, timedelta

from src.db.session import get_async_db
from src.db.models import User, Transaction, WithdrawalRequest, SystemMetrics
from src.services.cache import cache
from src.config import settings

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)
security = HTTPBearer()

async def verify_admin_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
	"""Verify admin authentication token"""
	if credentials.credentials != settings.admin_token:
		logger.warning(f"Invalid admin token attempt: {credentials.credentials[:10]}...")
		raise HTTPException(
			status_code=status.HTTP_401_UNAUTHORIZED,
			detail="Invalid admin token",
			headers={"WWW-Authenticate": "Bearer"},
		)
	return credentials.credentials

class AdminStatsResponse(BaseModel):
    total_users: int
    verified_users: int
    pending_verifications: int
    pending_withdrawals: int
    total_withdrawals_amount: int
    daily_new_users: int
    daily_verifications: int

class WithdrawalUpdateRequest(BaseModel):
    status: str
    notes: Optional[str] = None
    processed_by: Optional[str] = None

@router.get("/stats", response_model=AdminStatsResponse, dependencies=[Depends(verify_admin_token)])
@limiter.limit("30/minute")
async def get_admin_stats(
    request: Request,
    bot_id: Optional[str] = None,  # Optional bot filter
    db: AsyncSession = Depends(get_async_db)
):
    """Get admin dashboard statistics with optional bot filtering"""
    
    cache_key = f"admin_stats_{bot_id or 'all'}"
    cached_stats = await cache.get(cache_key)
    
    if cached_stats:
        return AdminStatsResponse(**cached_stats)
    
    async with db:
        # Get current date for daily stats
        today = datetime.now(timezone.utc).date()
        
        # Build base filters
        user_filter = []
        withdrawal_filter = []
        transaction_filter = []
        
        if bot_id:
            user_filter.append(User.bot_id == bot_id)
            withdrawal_filter.append(WithdrawalRequest.bot_id == bot_id)
            transaction_filter.append(Transaction.bot_id == bot_id)
        
        # Total users
        total_users_result = await db.execute(
            select(func.count(User.user_id)).where(*user_filter)
        )
        total_users = total_users_result.scalar()
        
        # Verified users
        verified_users_result = await db.execute(
            select(func.count(User.user_id)).where(
                User.is_verified == True, *user_filter
            )
        )
        verified_users = verified_users_result.scalar()
        
        # Pending verifications (users who joined but not verified)
        pending_verifications_result = await db.execute(
            select(func.count(User.user_id)).where(
                User.is_verified == False, *user_filter
            )
        )
        pending_verifications = pending_verifications_result.scalar()
        
        # Pending withdrawals
        pending_withdrawals_result = await db.execute(
            select(func.count(WithdrawalRequest.req_id))
            .where(WithdrawalRequest.status == "pending", *withdrawal_filter)
        )
        pending_withdrawals = pending_withdrawals_result.scalar()
        
        # Total pending withdrawal amount
        total_withdrawals_result = await db.execute(
            select(func.sum(WithdrawalRequest.amount))
            .where(WithdrawalRequest.status == "pending", *withdrawal_filter)
        )
        total_withdrawals_amount = total_withdrawals_result.scalar() or 0
        
        # Daily new users
        daily_users_result = await db.execute(
            select(func.count(User.user_id))
            .where(func.date(User.joined_at) == today, *user_filter)
        )
        daily_new_users = daily_users_result.scalar()
        
        # Daily verifications
        daily_verifications_result = await db.execute(
            select(func.count(Transaction.txn_id))
            .where(
                Transaction.type == "join_fee",
                Transaction.status == "verified",
                func.date(Transaction.created_at) == today,
                *transaction_filter
            )
        )
        daily_verifications = daily_verifications_result.scalar()
        
        stats = {
            "total_users": total_users,
            "verified_users": verified_users,
            "pending_verifications": pending_verifications,
            "pending_withdrawals": pending_withdrawals,
            "total_withdrawals_amount": total_withdrawals_amount,
            "daily_new_users": daily_new_users,
            "daily_verifications": daily_verifications
        }
        
        # Cache for 2 minutes
        await cache.set(cache_key, stats, ttl=120)
        
        return AdminStatsResponse(**stats)

@router.get("/withdrawals/pending", dependencies=[Depends(verify_admin_token)])
@limiter.limit("60/minute")
async def get_pending_withdrawals(
    request: Request,
    page: int = 1,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db)
):
    """Get pending withdrawal requests for admin review"""
    
    if limit > 100:
        limit = 100
        
    offset = (page - 1) * limit
    
    async with db:
        # Get pending withdrawals with user info
        result = await db.execute(
            select(WithdrawalRequest, User)
            .join(User, WithdrawalRequest.user_id == User.user_id)
            .where(WithdrawalRequest.status == "pending")
            .order_by(WithdrawalRequest.requested_at.asc())
            .offset(offset)
            .limit(limit)
        )
        
        withdrawals_and_users = result.all()
        
        # Get total count
        count_result = await db.execute(
            select(func.count(WithdrawalRequest.req_id))
            .where(WithdrawalRequest.status == "pending")
        )
        total_count = count_result.scalar()
        
        withdrawal_list = []
        for withdrawal, user in withdrawals_and_users:
            withdrawal_list.append({
                "req_id": withdrawal.req_id,
                "user_id": withdrawal.user_id,
                "username": user.username,
                "amount": withdrawal.amount,
                "upi_id": withdrawal.upi_id,
                "requested_at": withdrawal.requested_at.isoformat(),
                "user_total_earned": user.total_earned,
                "user_referrals_count": user.referrals_count,
                "user_joined_at": user.joined_at.isoformat()
            })
        
        return {
            "withdrawals": withdrawal_list,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }

@router.put("/withdrawals/{withdrawal_id}", dependencies=[Depends(verify_admin_token)])
@limiter.limit("100/minute")
async def update_withdrawal_status(
    request: Request,
    withdrawal_id: int,
    update_request: WithdrawalUpdateRequest,
    db: AsyncSession = Depends(get_async_db)
):
    """Update withdrawal request status"""
    
    if update_request.status not in ["pending", "processing", "paid", "rejected"]:
        raise HTTPException(status_code=400, detail="Invalid status")
    
    async with db:
        # Get withdrawal request
        result = await db.execute(
            select(WithdrawalRequest).where(WithdrawalRequest.req_id == withdrawal_id)
        )
        withdrawal = result.scalar_one_or_none()
        
        if not withdrawal:
            raise HTTPException(status_code=404, detail="Withdrawal request not found")
        
        # Update withdrawal
        update_data = {
            "status": update_request.status,
            "processed_at": datetime.now(timezone.utc) if update_request.status != "pending" else None,
            "notes": update_request.notes,
            "processed_by": update_request.processed_by
        }
        
        await db.execute(
            update(WithdrawalRequest)
            .where(WithdrawalRequest.req_id == withdrawal_id)
            .values(**update_data)
        )
        
        await db.commit()
        
        logger.info(
            f"Updated withdrawal {withdrawal_id} status to {update_request.status} "
            f"by {update_request.processed_by}"
        )
        
        return {"message": "Withdrawal status updated successfully"}

@router.get("/users/recent")
@limiter.limit("60/minute")
async def get_recent_users(
    request: Request,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db)
):
    """Get recently joined users"""
    
    if limit > 100:
        limit = 100
    
    async with db:
        result = await db.execute(
            select(User)
            .order_by(User.joined_at.desc())
            .limit(limit)
        )
        users = result.scalars().all()
        
        user_list = []
        for user in users:
            user_list.append({
                "user_id": user.user_id,
                "username": user.username,
                "joined_at": user.joined_at.isoformat(),
                "is_verified": user.is_verified,
                "referrals_count": user.referrals_count,
                "total_earned": user.total_earned,
                "referred_by": user.referred_by
            })
        
        return {"users": user_list}

@router.get("/metrics/daily")
@limiter.limit("30/minute")
async def get_daily_metrics(
    request: Request,
    days: int = 7,
    db: AsyncSession = Depends(get_async_db)
):
    """Get daily metrics for the past N days"""
    
    if days > 30:
        days = 30
    
    async with db:
        # This would typically query SystemMetrics table
        # For now, return placeholder data
        metrics = []
        for i in range(days):
            date = datetime.now(timezone.utc).date() - timedelta(days=i)
            
            # Get daily user count
            daily_users_result = await db.execute(
                select(func.count(User.user_id))
                .where(func.date(User.joined_at) == date)
            )
            daily_users = daily_users_result.scalar()
            
            metrics.append({
                "date": date.isoformat(),
                "new_users": daily_users,
                "verifications": 0,  # Would calculate from transactions
                "withdrawals": 0     # Would calculate from withdrawal requests
            })
        
        return {"metrics": metrics[::-1]}  # Reverse to get chronological order
