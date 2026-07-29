import logging
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Request
from slowapi import Limiter
from slowapi.util import get_remote_address
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.session import get_async_db
from src.db.models import User, Transaction
from src.services.cache import cache
from src.config import settings

router = APIRouter()
limiter = Limiter(key_func=get_remote_address)
logger = logging.getLogger(__name__)

class UserResponse(BaseModel):
    user_id: int
    bot_id: str
    username: Optional[str]
    referrals_count: int
    total_earned: int
    is_verified: bool
    joined_at: str
    referral_link: str

class UserStatsResponse(BaseModel):
    total_users: int
    verified_users: int
    total_referrals: int
    total_earnings: int

@router.get("/{user_id}", response_model=UserResponse)
@limiter.limit("60/minute")
async def get_user(
    request: Request,
    user_id: int,
    bot_id: Optional[str] = "paypulse",  # Default bot
    db: AsyncSession = Depends(get_async_db)
):
    """Get user information with bot isolation"""
    # Check cache first (with bot_id)
    cache_key = await cache.get_user_cache_key(f"{bot_id}:{user_id}")
    cached_user = await cache.get(cache_key)
    
    if cached_user:
        return UserResponse(**cached_user)
    
    # Query database with bot isolation
    async with db:
        result = await db.execute(
            select(User).where(
                User.user_id == user_id,
                User.bot_id == bot_id
            )
        )
        user = result.scalar_one_or_none()
        
        if not user:
            raise HTTPException(status_code=404, detail="User not found for this bot")
        
        # Get bot username for referral link
        from src.config import settings
        bot_configs = settings.get_bot_configs()
        bot_config = next((c for c in bot_configs if c.bot_id == bot_id), None)
        bot_username = bot_config.username if bot_config else settings.bot_username
        
        user_data = {
            "user_id": user.user_id,
            "bot_id": user.bot_id,
            "username": user.username,
            "referrals_count": user.referrals_count,
            "total_earned": user.total_earned,
            "is_verified": user.is_verified,
            "joined_at": user.joined_at.isoformat(),
            "referral_link": f"t.me/{bot_username}?start={user.user_id}"
        }
        
        # Cache the result
        await cache.set(cache_key, user_data, ttl=settings.cache_ttl_user)
        
        return UserResponse(**user_data)

@router.get("/{user_id}/referrals")
@limiter.limit("30/minute")
async def get_user_referrals(
    request: Request,
    user_id: int,
    bot_id: Optional[str] = "paypulse",  # Default bot
    page: int = 1,
    limit: int = 20,
    db: AsyncSession = Depends(get_async_db)
):
    """Get user's referrals with pagination and bot isolation"""
    if limit > 100:
        limit = 100
        
    offset = (page - 1) * limit
    
    async with db:
        # Get referrals (for same bot)
        result = await db.execute(
            select(User)
            .where(
                User.referred_by == user_id,
                User.bot_id == bot_id
            )
            .offset(offset)
            .limit(limit)
            .order_by(User.joined_at.desc())
        )
        referrals = result.scalars().all()
        
        # Get total count (for same bot)
        count_result = await db.execute(
            select(func.count(User.user_id)).where(
                User.referred_by == user_id,
                User.bot_id == bot_id
            )
        )
        total_count = count_result.scalar()
        
        referral_data = []
        for referral in referrals:
            referral_data.append({
                "user_id": referral.user_id,
                "username": referral.username,
                "joined_at": referral.joined_at.isoformat(),
                "is_verified": referral.is_verified
            })
        
        return {
            "referrals": referral_data,
            "total_count": total_count,
            "page": page,
            "limit": limit,
            "total_pages": (total_count + limit - 1) // limit
        }

@router.get("/stats/global", response_model=UserStatsResponse)
@limiter.limit("10/minute")
async def get_global_stats(
    request: Request,
    db: AsyncSession = Depends(get_async_db)
):
    """Get global user statistics"""
    cache_key = "global_stats"
    cached_stats = await cache.get(cache_key)
    
    if cached_stats:
        return UserStatsResponse(**cached_stats)
    
    async with db:
        # Get user counts
        total_users_result = await db.execute(select(func.count(User.user_id)))
        total_users = total_users_result.scalar()
        
        verified_users_result = await db.execute(
            select(func.count(User.user_id)).where(User.is_verified == True)
        )
        verified_users = verified_users_result.scalar()
        
        # Get referral stats
        total_referrals_result = await db.execute(
            select(func.sum(User.referrals_count))
        )
        total_referrals = total_referrals_result.scalar() or 0
        
        total_earnings_result = await db.execute(
            select(func.sum(User.total_earned))
        )
        total_earnings = total_earnings_result.scalar() or 0
        
        stats = {
            "total_users": total_users,
            "verified_users": verified_users,
            "total_referrals": total_referrals,
            "total_earnings": total_earnings
        }
        
        # Cache for 5 minutes
        await cache.set(cache_key, stats, ttl=300)
        
        return UserStatsResponse(**stats)

@router.get("/leaderboard")
@limiter.limit("20/minute")
async def get_leaderboard(
    request: Request,
    limit: int = 50,
    db: AsyncSession = Depends(get_async_db)
):
    """Get referral leaderboard"""
    if limit > 100:
        limit = 100
        
    cache_key = f"leaderboard_{limit}"
    cached_leaderboard = await cache.get(cache_key)
    
    if cached_leaderboard:
        return cached_leaderboard
    
    async with db:
        result = await db.execute(
            select(User)
            .where(User.referrals_count > 0)
            .order_by(User.referrals_count.desc(), User.total_earned.desc())
            .limit(limit)
        )
        users = result.scalars().all()
        
        leaderboard = []
        for i, user in enumerate(users, 1):
            leaderboard.append({
                "rank": i,
                "user_id": user.user_id,
                "username": user.username or f"User{user.user_id}",
                "referrals_count": user.referrals_count,
                "total_earned": user.total_earned
            })
        
        result_data = {"leaderboard": leaderboard}
        
        # Cache for 2 minutes
        await cache.set(cache_key, result_data, ttl=120)
        
        return result_data
