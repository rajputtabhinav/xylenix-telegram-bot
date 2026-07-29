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
    """Advanced payment verification with OpenAI Vision and duplicate prevention"""
    
    def __init__(self):
        self.api_key = settings.openai_api_key
        self.base_url = "https://api.openai.com/v1/chat/completions"
        
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
                return {
                    "success": True,
                    "verified": False,
                    "message": "❌ **Screenshot Already Used**\n\nThis payment screenshot has already been verified. Each screenshot can only be used once for security reasons.",
                    "details": {"reason": "duplicate_screenshot"},
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
            
            # Step 5: Perform AI vision verification
            verification_result = await self._verify_with_openai_vision(image_data)
            
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
                    "message": f"❌ **Verification Failed**\n\nPlease upload a clear screenshot showing:\n• Amount: ₹{settings.join_fee_inr}\n• Transaction status: Success\n• Complete payment details",
                    "details": verification_result["details"],
                    "duplicate_detected": False
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
        """Check if screenshot has been used before (within same bot)"""
        try:
            async with get_async_db() as db:
                # Check exact hash match (within same bot)
                exact_result = await db.execute(
                    select(ScreenshotMetadata)
                    .where(
                        ScreenshotMetadata.image_hash == image_hash,
                        ScreenshotMetadata.bot_id == bot_id
                    )
                )
                exact_match = exact_result.scalar_one_or_none()
                
                if exact_match:
                    return {
                        "is_duplicate": True,
                        "match_type": "exact",
                        "previous_user_id": exact_match.user_id,
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
    
    async def _verify_with_openai_vision(self, image_data: bytes) -> Dict[str, Any]:
        """Use OpenAI Vision API to verify payment screenshot"""
        try:
            if not self.api_key:
                # No API key - Always approve and send to admin for manual verification
                logger.warning("No OpenAI API key configured - sending for manual admin verification")
                return {
                    "verified": False,
                    "message": "⏳ **Manual Verification Required**\n\n📋 Your payment screenshot has been sent to our admin team for verification.\n\n⏰ **Processing Time:** Usually within 1-3 hours\n📱 **You'll be notified** once verification is complete.",
                    "details": {"verification_method": "manual_admin", "requires_admin_approval": True},
                    "send_to_admin": True
                }
            
            # Convert image to base64
            image_base64 = base64.b64encode(image_data).decode('utf-8')
            image_url = "data:image/jpeg;base64," + image_base64
            
            # ULTRA-SIMPLE verification prompt - ONLY check amount and success
            verification_prompt = """You are a payment verification assistant. Look at the image and check ONLY two things:

1.  **Amount:** Is the amount exactly **₹200** (or "Rs 200", "200/-")?
2.  **Status:** Is the payment **successful** (look for words like "Success", "Paid", "Completed")?

**YOUR TASK:**
- If **BOTH** amount is 200 AND status is successful, respond with: `{"verified": true}`
- If it fails **EITHER** of these checks, respond with: `{"verified": false}`
- **IGNORE EVERYTHING ELSE:** Date, time, recipient, UPI ID, and transaction ID do not matter.

Be very lenient. If you see "200" and "Success", approve it."""

            async with httpx.AsyncClient(timeout=60.0) as client:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer " + self.api_key
                    },
                    json={
                        "model": "gpt-4o-2024-08-06",
                        "max_tokens": 500,
                        "temperature": 0.1,
                        "messages": [
                            {
                                "role": "user",
                                "content": [
                                    {
                                        "type": "text",
                                        "text": verification_prompt
                                    },
                                    {
                                        "type": "image_url",
                                        "image_url": {
                                            "url": image_url,
                                            "detail": "high"
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    ai_response = result["choices"][0]["message"]["content"].strip()
                    
                    # Parse AI response
                    try:
                        verification_data = json.loads(ai_response)
                        
                        # SUPER LENIENT verification - approve if AI says so
                        if verification_data.get("verified") is True:
                            return {
                                "verified": True,
                                "message": "✅ **Payment Verified Successfully!**\n\n🎉 Welcome to Xylenix! Your account is now verified and you can start earning through referrals.",
                                "details": verification_data
                            }
                        else:
                            return {
                                "verified": False,
                                "message": "❌ **Verification Failed**\n\nPlease upload a clear screenshot showing:\n• Amount: ₹200\n• Transaction status: Success",
                                "details": verification_data
                            }
                            
                    except json.JSONDecodeError:
                        logger.error("Invalid JSON from AI: " + ai_response)
                        return {
                            "verified": False,
                            "message": "AI response format error",
                            "details": {"raw_response": ai_response}
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
            return {
                "verified": False,
                "message": "Verification service error",
                "details": {"error": str(e)}
            }

# Global service instance
payment_verification_service = PaymentVerificationService()
