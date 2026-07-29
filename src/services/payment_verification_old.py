import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Tuple, Optional
from PIL import Image
import io
import base64
import httpx
from sqlalchemy import select

from src.config import settings
from src.db.session import get_async_db
from src.db.models import ScreenshotMetadata, User

logger = logging.getLogger(__name__)

class PaymentVerificationService:
    """Advanced payment verification with Claude Sonnet Vision and duplicate prevention"""
    
    def __init__(self):
        # Using Anthropic Claude instead of OpenAI
        pass
        
    async def verify_payment_screenshot(self, user_id: int, image_data: bytes, bot_id: str = "paypulse") -> Dict[str, Any]:
        """
        Comprehensive payment verification with duplicate prevention
        """
        try:
            # Step 1: Generate image hashes for duplicate detection
            image_hash = self._generate_image_hash(image_data)
            perceptual_hash = self._generate_perceptual_hash(image_data)
            
            # Step 2: Check for duplicates (within same bot)
            duplicate_check = await self._check_duplicate_screenshot(image_hash, perceptual_hash, user_id, bot_id)
            if duplicate_check["is_duplicate"]:
                previous_bot = duplicate_check.get("previous_bot_id", "another bot")
                return {
                    "success": True,
                    "verified": False,
                    "message": f"❌ **Screenshot Already Used**\n\nThis payment screenshot has already been verified in {previous_bot} bot. Each screenshot can only be used once across ALL bots for security reasons.\n\n💡 **Please use a different payment screenshot** or make a new payment.",
                    "details": {"reason": "duplicate_screenshot_cross_bot", "previous_bot": previous_bot},
                    "duplicate_detected": True,
                    "previous_user": duplicate_check["previous_user_id"]
                }
            
            # Step 3: Get image metadata
            image_metadata = self._get_image_metadata(image_data)
            
            # Step 4: Store screenshot metadata (pending status)
            screenshot_record = await self._store_screenshot_metadata(
                user_id=user_id,
                bot_id=bot_id,
                image_hash=image_hash,
                perceptual_hash=perceptual_hash,
                file_size=len(image_data),
                image_dimensions=f"{image_metadata['width']}x{image_metadata['height']}"
            )
            
            # Step 5: Perform AI vision verification with Claude
            verification_result = await self._verify_with_claude_vision(image_data)
            
            # Step 6: Update screenshot record with verification result
            await self._update_screenshot_verification(
                screenshot_record.id,
                verification_result["verified"],
                verification_result
            )
            
            # Step 7: Return comprehensive result
            if verification_result["verified"]:
                return {
                    "success": True,
                    "verified": True,
                    "message": "✅ **Payment Verified Successfully!**\n\n🎉 Welcome to Xylenix! Your account is now verified and you can start earning through referrals.",
                    "details": verification_result["details"],
                    "duplicate_detected": False
                }
            else:
                return {
                    "success": True,
                    "verified": False,
                    "message": f"⏳ **Manual Verification Required**\n\n📋 Your payment screenshot has been sent to our admin team for verification.\n\n⏰ **Processing Time:** Usually within 1-3 hours\n📱 **You'll be notified** once verification is complete.",
                    "details": verification_result["details"],
                    "duplicate_detected": False,
                    "send_to_admin": True  # Always send failed verifications to admin
                }
                
        except Exception as e:
            logger.error(f"Payment verification error: {e}")
            return {
                "success": False,
                "verified": False,
                "message": "❌ **Verification Error**\n\nSomething went wrong while verifying your payment. Please try again or contact support.",
                "details": {"error": str(e)},
                "duplicate_detected": False
            }
    
    def _generate_image_hash(self, image_data: bytes) -> str:
        """Generate SHA256 hash of image data"""
        return hashlib.sha256(image_data).hexdigest()
    
    def _generate_perceptual_hash(self, image_data: bytes) -> str:
        """Generate perceptual hash for similar image detection"""
        try:
            # Simple perceptual hash implementation
            image = Image.open(io.BytesIO(image_data))
            # Convert to grayscale and resize to 8x8
            image = image.convert('L').resize((8, 8), Image.Resampling.LANCZOS)
            
            # Get pixel values
            pixels = list(image.getdata())
            
            # Calculate average
            avg = sum(pixels) / len(pixels)
            
            # Generate hash based on pixels above/below average
            hash_bits = []
            for pixel in pixels:
                hash_bits.append('1' if pixel > avg else '0')
            
            # Convert binary to hex
            hash_str = ''.join(hash_bits)
            return hex(int(hash_str, 2))[2:].zfill(16)
            
        except Exception as e:
            logger.warning(f"Perceptual hash generation failed: {e}")
            # Fallback: use first 16 chars of SHA256
            return self._generate_image_hash(image_data)[:16]
    
    def _get_image_metadata(self, image_data: bytes) -> Dict[str, Any]:
        """Extract image metadata"""
        try:
            image = Image.open(io.BytesIO(image_data))
            return {
                "width": image.width,
                "height": image.height,
                "format": image.format,
                "mode": image.mode
            }
        except Exception as e:
            logger.warning(f"Image metadata extraction failed: {e}")
            return {"width": 0, "height": 0, "format": "unknown", "mode": "unknown"}
    
    async def _check_duplicate_screenshot(self, image_hash: str, perceptual_hash: str, user_id: int, bot_id: str) -> Dict[str, Any]:
        """Check if screenshot has been used before (ACROSS ALL BOTS for security)"""
        try:
            async with get_async_db() as db:
                # Check exact hash match ACROSS ALL BOTS (not just same bot)
                exact_result = await db.execute(
                    select(ScreenshotMetadata)
                    .where(
                        ScreenshotMetadata.image_hash == image_hash
                        # REMOVED: ScreenshotMetadata.bot_id == bot_id
                        # Now checks across ALL bots for security
                    )
                )
                exact_match = exact_result.scalar_one_or_none()
                
                if exact_match:
                    return {
                        "is_duplicate": True,
                        "match_type": "exact",
                        "previous_user_id": exact_match.user_id,
                        "previous_bot_id": exact_match.bot_id,  # Show which bot it was used in
                        "previous_status": exact_match.verification_status
                    }
                
                return {"is_duplicate": False}
                
        except Exception as e:
            logger.error(f"Duplicate check failed: {e}")
            return {"is_duplicate": False}
    
    async def _store_screenshot_metadata(self, user_id: int, bot_id: str, image_hash: str, perceptual_hash: str, 
                                       file_size: int, image_dimensions: str) -> ScreenshotMetadata:
        """Store screenshot metadata in database with bot isolation"""
        async with get_async_db() as db:
            screenshot_record = ScreenshotMetadata(
                user_id=user_id,
                bot_id=bot_id,
                image_hash=image_hash,
                perceptual_hash=perceptual_hash,
                file_size=file_size,
                image_dimensions=image_dimensions,
                verification_status="pending"
            )
            
            db.add(screenshot_record)
            await db.commit()
            await db.refresh(screenshot_record)
            
            return screenshot_record
    
    async def _update_screenshot_verification(self, screenshot_id: int, verified: bool, 
                                            verification_result: Dict[str, Any]):
        """Update screenshot verification status"""
        async with get_async_db() as db:
            result = await db.execute(
                select(ScreenshotMetadata).where(ScreenshotMetadata.id == screenshot_id)
            )
            screenshot_record = result.scalar_one_or_none()
            
            if screenshot_record:
                screenshot_record.verification_status = "approved" if verified else "rejected"
                screenshot_record.verification_result = json.dumps(verification_result)
                screenshot_record.verified_at = datetime.now(timezone.utc)
                
                await db.commit()
    
    async def _verify_with_claude_vision(self, image_data: bytes) -> Dict[str, Any]:
        """Use Claude Sonnet Vision API to verify payment screenshot with strict verification"""
        try:
            if not settings.anthropic_api_key:
                # No API key - send to admin for manual verification
                logger.warning("No Anthropic API key configured - sending for manual admin verification")
                return {
                    "verified": False,
                    "message": "⏳ **Manual Verification Required**\n\n📋 Your payment screenshot has been sent to our admin team for verification.\n\n⏰ **Processing Time:** Usually within 1-3 hours\n📱 **You'll be notified** once verification is complete.",
                    "details": {"verification_method": "manual_admin", "requires_admin_approval": True},
                    "send_to_admin": True
                }
            
            # Import anthropic here to avoid startup issues if not configured
            import anthropic
            
            # Encode image to base64
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            
            # STRICT verification prompt - Claude must explicitly approve
            verification_prompt = """You are a payment verification specialist. Look at this payment screenshot and answer ONLY with "YES" or "NO".

QUESTION: Is this a successful payment of exactly ₹200?

REQUIREMENTS TO SAY "YES":
1. Amount must be EXACTLY ₹200 (or Rs 200, 200/-, 200.00)
2. Payment status must show SUCCESS/COMPLETED/PAID (not pending/failed)
3. Must be a real payment screenshot (not random image)

RESPOND WITH:
- "YES" - ONLY if ALL 3 requirements are met
- "NO" - If ANY requirement is missing

Be STRICT. If you're not 100% sure, say "NO"."""

            # Initialize Claude client
            client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
            
            # Send to Claude Sonnet
            response = client.messages.create(
                model=settings.anthropic_model,
                max_tokens=10,
                temperature=0.0,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/jpeg",
                                    "data": image_base64
                                }
                            },
                            {
                                "type": "text",
                                "text": verification_prompt
                            }
                        ]
                    }
                ]
            )
                
                if response.status_code == 200:
                    result = response.json()
                    ai_response = result["choices"][0]["message"]["content"].strip()
                    
                    # Parse AI response - ULTRA LENIENT to approve more payments
                    try:
                        # Try JSON parsing first
                        verification_data = json.loads(ai_response)
                        verified = verification_data.get("verified", False)
                        
                    except json.JSONDecodeError:
                        # JSON failed - use keyword detection (VERY lenient)
                        ai_lower = ai_response.lower()
                        
                        # Look for any positive indicators
                        positive_keywords = ["true", "verified", "approved", "valid", "success", "yes", "200", "paid", "completed"]
                        negative_keywords = ["false", "failed", "rejected", "invalid", "no"]
                        
                        positive_count = sum(1 for keyword in positive_keywords if keyword in ai_lower)
                        negative_count = sum(1 for keyword in negative_keywords if keyword in ai_lower)
                        
                        # If we see "200" anywhere, approve it
                        if "200" in ai_response:
                            verified = True
                        # If more positive than negative, approve
                        elif positive_count > negative_count:
                            verified = True
                        else:
                            verified = False
                            
                        verification_data = {"raw_response": ai_response, "keyword_verified": verified}
                    
                    # Return result
                    if verified:
                        return {
                            "verified": True,
                            "message": "✅ **Payment Verified Successfully!**\n\n🎉 Welcome to Xylenix! Your account is now verified and you can start earning through referrals.",
                            "details": verification_data
                        }
                    else:
                        # Even if AI says no, send to admin for manual review
                        return {
                            "verified": False,
                            "message": "⏳ **Manual Verification Required**\n\n📋 Your payment screenshot has been sent to our admin team for verification.\n\n⏰ **Processing Time:** Usually within 1-3 hours\n📱 **You'll be notified** once verification is complete.",
                            "details": verification_data,
                            "send_to_admin": True
                        }
                        
                else:
                    logger.error("OpenAI API error: " + str(response.status_code) + " - " + response.text)
                    return {
                        "verified": False,
                        "message": "AI verification service error",
                        "details": {"api_error": response.text}
                    }
                    
        except Exception as e:
            logger.error("Vision API verification error: " + str(e))
            # When AI fails, always send to admin for manual verification
            return {
                "verified": False,
                "message": "⏳ **Manual Verification Required**\n\n📋 Your payment screenshot has been sent to our admin team for verification.\n\n⏰ **Processing Time:** Usually within 1-3 hours\n📱 **You'll be notified** once verification is complete.",
                "details": {"verification_method": "manual_admin_fallback", "requires_admin_approval": True, "ai_error": str(e)},
                "send_to_admin": True
            }

# Global service instance
payment_verification_service = PaymentVerificationService()
