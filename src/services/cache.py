import json
import logging
from typing import Any, Optional, Dict
from src.config import settings
import time

logger = logging.getLogger(__name__)

class CacheService:
    def __init__(self):
        self.redis_client: Optional[Any] = None
        self.is_connected = False
        # In-memory fallback cache
        self._memory_cache: Dict[str, Dict[str, Any]] = {}
        self._use_memory_fallback = False

    async def connect(self):
        """Initialize Redis connection"""
        try:
            import redis.asyncio as redis
            self.redis_client = redis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
            # Test connection
            await self.redis_client.ping()
            self.is_connected = True
            logger.info("Redis connection established")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}. Using in-memory cache fallback.")
            self.is_connected = False
            self._use_memory_fallback = True
            logger.info("In-memory cache fallback enabled")

    async def disconnect(self):
        """Close Redis connection"""
        if self.redis_client:
            await self.redis_client.close()
            self.is_connected = False

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache"""
        if self._use_memory_fallback:
            cache_item = self._memory_cache.get(key)
            if cache_item and cache_item['expires'] > time.time():
                return cache_item['value']
            elif cache_item:
                del self._memory_cache[key]
            return None
            
        if not self.is_connected:
            return None
        
        try:
            value = await self.redis_client.get(key)
            if value:
                return json.loads(value)
            return None
        except Exception as e:
            logger.error(f"Cache get error for key {key}: {e}")
            return None

    async def set(self, key: str, value: Any, ttl: int = 300) -> bool:
        """Set value in cache with TTL"""
        if self._use_memory_fallback:
            self._memory_cache[key] = {
                'value': value,
                'expires': time.time() + ttl
            }
            return True
            
        if not self.is_connected:
            return False
        
        try:
            serialized_value = json.dumps(value, default=str)
            await self.redis_client.setex(key, ttl, serialized_value)
            return True
        except Exception as e:
            logger.error(f"Cache set error for key {key}: {e}")
            return False

    async def delete(self, key: str) -> bool:
        """Delete key from cache"""
        if self._use_memory_fallback:
            if key in self._memory_cache:
                del self._memory_cache[key]
                return True
            return False
            
        if not self.is_connected:
            return False
        
        try:
            await self.redis_client.delete(key)
            return True
        except Exception as e:
            logger.error(f"Cache delete error for key {key}: {e}")
            return False

    async def increment(self, key: str, amount: int = 1, ttl: int = 3600) -> Optional[int]:
        """Increment counter with TTL"""
        if self._use_memory_fallback:
            current_value = await self.get(key) or 0
            new_value = current_value + amount
            await self.set(key, new_value, ttl)
            return new_value
            
        if not self.is_connected:
            return None
        
        try:
            pipe = self.redis_client.pipeline()
            pipe.incr(key, amount)
            pipe.expire(key, ttl)
            results = await pipe.execute()
            return results[0]
        except Exception as e:
            logger.error(f"Cache increment error for key {key}: {e}")
            return None

    async def get_user_cache_key(self, user_id, suffix: str = "") -> str:
        """Generate cache key for user data"""
        return f"user:{user_id}:{suffix}" if suffix else f"user:{user_id}"

    async def get_rate_limit_key(self, user_id, action: str) -> str:
        """Generate rate limit key"""
        return f"rate_limit:{user_id}:{action}"

# Global cache instance
cache = CacheService()
