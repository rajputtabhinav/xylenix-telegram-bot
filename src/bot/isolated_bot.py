"""
Isolated Bot Instance for Multi-Bot Architecture
Each instance operates with complete database isolation using bot_id
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ContextTypes, filters,
    CallbackQueryHandler, ConversationHandler
)
from telegram.error import TelegramError
from sqlalchemy import select, update, func
from datetime import datetime, timezone, timedelta

from src.config import BotConfig, settings
from src.db.session import get_async_db
from src.db.models import User, Transaction, WithdrawalRequest, UserSession, ScreenshotMetadata
from src.services.cache import cache
from src.services.queue import queue_service
from src.services.verification import verify_payment_screenshot
from src.services.qr_generator import generate_upi_qr
from src.services.ai_chat import ai_chat_service
from src.services.payment_verification import payment_verification_service
import httpx
import os
import uuid

# Conversation states
AWAITING_PAYMENT = 1
AWAITING_UPI = 2
AWAITING_WITHDRAWAL_AMOUNT = 3


class IsolatedXylenixBot:
    """Isolated bot instance with bot_id based database isolation"""
    
    def __init__(self, config: BotConfig):
        self.config = config
        self.application = None
        self.user_states: Dict[int, str] = {}
        self.logger = logging.getLogger(f"Bot-{config.bot_id}")
        
        self.logger.info(f"Initializing isolated bot: {config.bot_id} (@{config.username})")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /start command with referral tracking and bot isolation"""
        try:
            args = context.args if context.args else []
            referrer_id: Optional[int] = None
            if args:
                try:
                    referrer_id = int(args[0])
                    self.logger.info(f"New user {update.effective_user.id} joining with referrer {referrer_id}")
                except (ValueError, IndexError):
                    referrer_id = None

            user = update.effective_user
            if not user:
                self.logger.warning("No effective user in start command")
                return

            async with get_async_db() as db:
                # Check if user exists FOR THIS BOT
                result = await db.execute(
                    select(User).where(
                        User.user_id == user.id,
                        User.bot_id == self.config.bot_id
                    )
                )
                existing = result.scalar_one_or_none()
                
                if not existing:
                    # Validate referrer if provided (must be from same bot)
                    valid_referrer = None
                    if referrer_id:
                        referrer_result = await db.execute(
                            select(User).where(
                                User.user_id == referrer_id,
                                User.bot_id == self.config.bot_id  # Same bot isolation
                            )
                        )
                        valid_referrer = referrer_result.scalar_one_or_none()
                    
                    # ATOMIC TRANSACTION: Create user and update referrer in single transaction
                    try:
                        # Create new user for this bot with WELCOME BONUS
                        new_user = User(
                            user_id=user.id,
                            bot_id=self.config.bot_id,  # Isolated to this bot
                            username=user.username or None,
                            referred_by=valid_referrer.user_id if valid_referrer else None,
                            joined_at=datetime.now(timezone.utc),
                            total_earned=settings.welcome_bonus_inr  # NEW: Give ₹70 welcome bonus instantly!
                        )
                        db.add(new_user)
                        
                        # DON'T update referrer count here - only update when user gets VERIFIED
                        # Referral reward is given only after payment verification, not on join
                        
                        # Commit both operations atomically
                        await db.commit()
                        
                        # Notify referrer about new join (not reward - that comes after verification)
                        if valid_referrer:
                            try:
                                await context.bot.send_message(
                                    chat_id=valid_referrer.user_id,
                                    text=f"👥 **New Join Alert!**\n\n"
                                         f"@{user.username or user.first_name} joined {self.config.username} using your link!\n\n"
                                         f"💡 **They need to pay ₹{settings.join_fee_inr} and get verified for you to earn ₹180.**\n\n"
                                         f"🚀 Help them complete verification to get your reward!"
                                )
                            except TelegramError:
                                pass  # Referrer might have blocked the bot
                                
                    except Exception as e:
                        self.logger.error(f"Failed to create user {user.id}: {e}")
                        await db.rollback()
                        try:
                            await update.message.reply_text("🔄 **Registration Issue**\n\nPlease try /start again. If problem persists, contact support.")
                        except Exception as msg_error:
                            self.logger.error(f"Failed to send error message: {msg_error}")
                        return
                    
                    message = self._get_welcome_message(user.first_name or "User")
                    keyboard = self._get_main_keyboard(user_verified=False)
                else:
                    # ENSURE EXISTING USERS ALSO HAVE ₹70 STARTING AMOUNT
                    if existing.total_earned < settings.welcome_bonus_inr:
                        existing.total_earned = settings.welcome_bonus_inr
                        await db.commit()
                        self.logger.info(f"Updated existing user {existing.user_id} balance to ₹{settings.welcome_bonus_inr}")
                    
                    message = self._get_returning_user_message(existing)
                    keyboard = self._get_main_keyboard(user_verified=existing.is_verified)
                
                try:
                    await update.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
                except Exception as parse_error:
                    self.logger.error(f"Markdown parsing error in start: {parse_error}")
                    # Fallback without markdown
                    simple_message = f"🎉 Welcome to {self.config.username}!\n\n"
                    if not existing:
                        simple_message += f"💰 Start earning by referring friends!\n"
                        simple_message += f"• Join fee: ₹{settings.join_fee_inr}\n"
                        simple_message += f"• Earn ₹180 per referral\n"
                        simple_message += f"• Minimum withdrawal: ₹{settings.min_withdrawal_inr}\n\n"
                        simple_message += f"Click 'Pay Fee' below to get started!"
                    else:
                        if existing.is_verified:
                            simple_message += f"Welcome back! You have {existing.referrals_count} referrals and earned ₹{existing.total_earned}."
                        else:
                            simple_message += f"Please complete verification to start earning!"
                    
                    await update.message.reply_text(simple_message, reply_markup=keyboard)
                self.logger.info(f"Successfully processed /start for user {user.id} (referrer: {referrer_id})")
                
        except Exception as e:
            self.logger.error(f"Critical error in start command for user {user.id if user else 'unknown'}: {e}")
            # Log the full traceback for debugging
            import traceback
            self.logger.error(f"Full traceback: {traceback.format_exc()}")
            
            try:
                if update.message:
                    await update.message.reply_text(
                        f"🔄 **Welcome to {self.config.username}!**\n\n"
                        f"⚠️ There was a temporary issue. Let's try again!\n\n"
                        f"💰 **Ready to start earning?**\n"
                        f"• Join fee: ₹{settings.join_fee_inr}\n"
                        f"• Earn ₹180 per referral\n"
                        f"• Minimum withdrawal: ₹{settings.min_withdrawal_inr}\n\n"
                        f"Click the button below to get started:",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton(f"✅ Pay ₹{settings.join_fee_inr} Fee (QR Code)", callback_data="verify_payment")],
                            [InlineKeyboardButton("ℹ️ How it Works", callback_data="how_it_works")]
                        ])
                    )
            except Exception as msg_error:
                self.logger.error(f"Failed to send error message in start command: {msg_error}")
                # Final fallback - simple message
                try:
                    await update.message.reply_text(
                        f"Welcome to {self.config.username}! Ready to start earning? Try /start again."
                    )
                except:
                    pass

    async def referrals(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle /referrals command with bot isolation"""
        try:
            user = update.effective_user
            if not user:
                return
            
            # Check cache first (with bot_id)
            cache_key = await cache.get_user_cache_key(f"{self.config.bot_id}:{user.id}", "referrals")
            cached_data = await cache.get(cache_key)
            
            if cached_data:
                message = cached_data["message"]
                keyboard = InlineKeyboardMarkup(cached_data["keyboard"])
            else:
                async with get_async_db() as db:
                    result = await db.execute(
                        select(User).where(
                            User.user_id == user.id,
                            User.bot_id == self.config.bot_id
                        )
                    )
                    user_data = result.scalar_one_or_none()
                    
                    if not user_data:
                        await update.message.reply_text("Please /start first.")
                        return
                    
                    # Calculate tier and next reward
                    current_tier = "Tier 1" if user_data.referrals_count <= settings.tier1_threshold else "Tier 2"
                    current_reward = settings.tier1_reward_inr if user_data.referrals_count <= settings.tier1_threshold else settings.tier2_reward_inr
                    next_tier_at = settings.tier1_threshold + 1 if user_data.referrals_count <= settings.tier1_threshold else None
                    
                    message = f"""📊 *Your Referral Stats on {self.config.username}*

👥 Total Referrals: {user_data.referrals_count}
💰 Total Earned: ₹{user_data.total_earned}
🏆 Current Tier: {current_tier}
💵 Current Reward: ₹{current_reward} per referral

🔗 Your Referral Link:
`https://t.me/{self.config.username}?start={user_data.user_id}`

Share this link to earn rewards!"""
                    
                    if next_tier_at:
                        remaining = next_tier_at - user_data.referrals_count
                        message += f"\n\n🎯 {remaining} more referrals to reach Tier 2 (₹{settings.tier2_reward_inr} per referral)!"
                    
                    keyboard_data = [[
                        InlineKeyboardButton("🔄 Refresh", callback_data="refresh_referrals"),
                        InlineKeyboardButton("💰 Withdraw", callback_data="start_withdrawal")
                    ]]
                    keyboard = InlineKeyboardMarkup(keyboard_data)
                    
                    # Cache for 1 minute
                    await cache.set(cache_key, {
                        "message": message,
                        "keyboard": keyboard_data
                    }, ttl=60)
            
            await update.message.reply_text(message, reply_markup=keyboard, parse_mode="Markdown")
            
        except Exception as e:
            self.logger.error(f"Error in referrals command: {e}")
            await update.message.reply_text("Sorry, couldn't fetch your referral stats. Please try again.")

    async def handle_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle photo uploads for payment verification and withdrawal UPI QR with bot isolation"""
        try:
            user = update.effective_user
            if not user:
                return
            
            # Check if this is a withdrawal UPI QR upload
            if user.id in self.user_states and self.user_states[user.id].startswith("awaiting_upi_qr:"):
                await self._handle_withdrawal_upi_qr(update, context)
                return
            
            # Otherwise, handle payment verification
            await self._handle_payment_verification_photo(update, context)
            
        except Exception as e:
            self.logger.error(f"Error handling photo upload: {e}")
            # Only send error message if no other message was sent
            if not hasattr(update.message, '_photo_processed'):
                await update.message.reply_text(
                    "❌ **Failed to process your image.**\n\n"
                    "Please try again or contact support.",
                    parse_mode="Markdown"
                )
                update.message._photo_processed = True

    async def _handle_payment_verification_photo(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle payment verification photo upload with bot isolation"""
        user = update.effective_user
        
        # Check if user exists and is not already verified (for this bot)
        async with get_async_db() as db:
            result = await db.execute(
                select(User).where(
                    User.user_id == user.id,
                    User.bot_id == self.config.bot_id
                )
            )
            user_data = result.scalar_one_or_none()
            
            if not user_data:
                await update.message.reply_text("Please /start first.")
                return
            
            if user_data.is_verified:
                await update.message.reply_text("✅ You're already verified! Use /referrals to see your progress.")
                return
        
        # Check rate limiting (per bot)
        rate_limit_key = await cache.get_rate_limit_key(f"{self.config.bot_id}:{user.id}", "verification")
        current_attempts = await cache.get(rate_limit_key) or 0
        
        if current_attempts >= 10:
            await update.message.reply_text(
                "⚠️ Too many verification attempts. Please wait 1 hour before trying again."
            )
            return
        
        # Increment attempt counter
        await cache.increment(rate_limit_key, ttl=3600)
        
        # Send processing message
        processing_msg = await update.message.reply_text(
            "🔍 Processing your payment screenshot...\n\n"
            "This may take a few moments. I'll notify you once verification is complete."
        )
        
        # Download the photo
        photo = update.message.photo[-1]  # Get highest resolution
        file = await context.bot.get_file(photo.file_id)
        
        # Download file bytes
        async with httpx.AsyncClient() as client:
            response = await client.get(file.file_path)
            image_bytes = response.content
        
        # Use new AI vision verification service (with bot_id)
        verification_result = await payment_verification_service.verify_payment_screenshot(
            user_id=user.id,
            image_data=image_bytes,
            bot_id=self.config.bot_id  # Pass bot_id for isolation
        )
        
        # Delete processing message
        try:
            await context.bot.delete_message(
                chat_id=update.effective_chat.id,
                message_id=processing_msg.message_id
            )
        except Exception:
            pass  # Message might already be deleted
        
        # Send verification result to user first, then to admin
        user_message_sent = False
        
        if verification_result["success"]:
            # Check for duplicate screenshot first - CRITICAL SECURITY CHECK
            if verification_result.get("duplicate_detected", False):
                await update.message.reply_text(verification_result["message"], parse_mode="Markdown")
                user_message_sent = True
                # DO NOT PROCESS FURTHER - DUPLICATE DETECTED
                self.logger.warning(f"Duplicate screenshot detected for user {user.id} on bot {self.config.bot_id}")
                return
                
            elif verification_result["verified"]:
                # Update user verification status for this bot
                async with get_async_db() as db:
                    result = await db.execute(
                        select(User).where(
                            User.user_id == user.id,
                            User.bot_id == self.config.bot_id
                        )
                    )
                    user_data = result.scalar_one_or_none()
                    
                    if user_data and not user_data.is_verified:
                        user_data.is_verified = True
                        
                        # Update referrer if exists (for same bot only) - FIXED LOGIC
                        if user_data.referred_by:
                            self.logger.info(f"Processing referral reward for referrer {user_data.referred_by}")
                            referrer_result = await db.execute(
                                select(User).where(
                                    User.user_id == user_data.referred_by,
                                    User.bot_id == self.config.bot_id  # Same bot isolation
                                )
                            )
                            referrer = referrer_result.scalar_one_or_none()
                            
                            if referrer:
                                # Calculate reward BEFORE incrementing count
                                old_count = referrer.referrals_count
                                new_count = old_count + 1
                                # Special logic: 1st referral = ₹110, 2nd-15th = ₹180, 16th+ = ₹190
                                if new_count == 1:
                                    reward = settings.tier1_reward_inr  # ₹110 for first referral only
                                elif new_count <= settings.tier1_threshold:
                                    reward = 180  # ₹180 for referrals 2-15
                                else:
                                    reward = settings.tier2_reward_inr  # ₹190 for 16th+
                                
                                # Update referrer
                                referrer.referrals_count = new_count
                                referrer.total_earned += reward
                                
                                self.logger.info(f"Referrer {referrer.user_id} count: {old_count} → {new_count}, earned: +₹{reward}")
                                
                                # Notify referrer about the reward
                                try:
                                    await context.bot.send_message(
                                        chat_id=referrer.user_id,
                                        text=f"🎉 **Referral Verified!**\n\n"
                                             f"💰 **Earned:** ₹{reward}\n"
                                             f"👥 **Total Referrals:** {new_count}\n"
                                             f"💵 **Total Earned:** ₹{referrer.total_earned}\n\n"
                                             f"🚀 Keep sharing your link to earn more!"
                                    )
                                except TelegramError:
                                    pass
                            else:
                                self.logger.warning(f"Referrer {user_data.referred_by} not found for user {user_data.user_id}")
                        
                        await db.commit()
                        
                        # Send success message with referral link
                        referral_link = f"https://t.me/{self.config.username}?start={user.id}"
                        success_message = f"""{verification_result["message"]}

🔗 **Your Referral Link for {self.config.username}:**
`{referral_link}`

💰 **Start Earning:**
• Share this link with friends
• Earn ₹{settings.tier1_reward_inr} per referral (first {settings.tier1_threshold})
• Earn ₹{settings.tier2_reward_inr} per referral (after {settings.tier1_threshold})

🎯 **Minimum withdrawal:** ₹{settings.min_withdrawal_inr}"""
                        
                        keyboard = [
                            [InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")],
                            [InlineKeyboardButton("🔗 Copy Link", callback_data="get_referral_link")],
                            [InlineKeyboardButton("💰 Withdraw", callback_data="start_withdrawal")]
                        ]
                        
                        await update.message.reply_text(
                            success_message,
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(keyboard)
                        )
                        user_message_sent = True
                    else:
                        await update.message.reply_text(verification_result["message"], parse_mode="Markdown")
                        user_message_sent = True
            else:
                # Verification failed
                await update.message.reply_text(verification_result["message"], parse_mode="Markdown")
                user_message_sent = True
        else:
            # Service error
            await update.message.reply_text(verification_result["message"], parse_mode="Markdown")
            user_message_sent = True
        
        # Send to admin for verification - but NOT for duplicates (security handled)
        if not verification_result.get("duplicate_detected", False):
            await self._send_admin_payment_verification(context, user, image_bytes, verification_result)
        
        self.logger.info(f"Verification completed for user {user.id} on bot {self.config.bot_id}: {verification_result['verified']}")

    async def _send_admin_payment_verification(self, context, user, image_bytes, verification_result):
        """Send payment verification screenshot to admin with approval buttons"""
        try:
            if not settings.admin_chat_id:
                self.logger.warning("admin_chat_id not set - auto-approving payment verification")
                # Auto-approve when no admin configured
                await self._auto_approve_payment(user.id, self.config.bot_id, context)
                return
            
            # Get user data for admin notification
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user.id,
                        User.bot_id == self.config.bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    return
                
                # Determine verification status
                ai_status = "✅ AI APPROVED" if verification_result.get("verified") else "❌ AI REJECTED"
                if verification_result.get("details", {}).get("verification_method") == "manual_admin":
                    ai_status = "⏳ PENDING MANUAL REVIEW"
                
                # Create comprehensive admin message (fix markdown parsing)
                username_display = f"@{user.username}" if user.username else "No username"
                user_name = user.full_name or user.first_name or "N/A"
                
                admin_message = f"""🔍 PAYMENT VERIFICATION REQUEST

👤 User Information:
• Name: {user_name}
• Username: {username_display}
• User ID: {user.id}
• Bot: {self.config.username} ({self.config.bot_id})
• Joined: {user_data.joined_at.strftime('%d %b %Y, %I:%M %p')}

💰 Payment Details:
• Expected Amount: ₹{settings.join_fee_inr}
• Expected UPI: {', '.join(settings.receiver_upi_ids)}
• Submission Time: {datetime.now().strftime('%d %b %Y, %I:%M %p')}

🤖 AI Verification Status: {ai_status}"""
                
                # Add AI details if available (simplified to avoid parsing errors)
                if verification_result.get("details"):
                    details = verification_result["details"]
                    if isinstance(details, dict):
                        admin_message += f"\n\n🔍 AI Analysis:"
                        for key, value in details.items():
                            # Escape special characters to prevent parsing errors
                            clean_key = str(key).replace('_', ' ').title()
                            clean_value = str(value).replace('*', '').replace('`', '').replace('[', '').replace(']', '')
                            admin_message += f"\n• {clean_key}: {clean_value}"
                
                admin_message += f"\n\n📱 Payment Screenshot below:"
                
                # Create approval buttons for admin
                keyboard = [
                    [
                        InlineKeyboardButton("✅ APPROVE PAYMENT", callback_data=f"approve_payment:{user.id}:{self.config.bot_id}"),
                        InlineKeyboardButton("❌ REJECT PAYMENT", callback_data=f"reject_payment:{user.id}:{self.config.bot_id}")
                    ],
                    [
                        InlineKeyboardButton("📊 User Profile", callback_data=f"user_profile:{user.id}:{self.config.bot_id}"),
                        InlineKeyboardButton("📋 All Pending", callback_data=f"pending_verifications:{self.config.bot_id}")
                    ]
                ]
                
                # Send payment screenshot to admin with verification details
                try:
                    await context.bot.send_photo(
                        chat_id=int(settings.admin_chat_id),
                        photo=image_bytes,
                        caption=admin_message,
                        reply_markup=InlineKeyboardMarkup(keyboard)
                        # Removed parse_mode to prevent parsing errors
                    )
                except Exception as send_error:
                    self.logger.error(f"Failed to send admin photo: {send_error}")
                    # Fallback: send as text message
                    await context.bot.send_message(
                        chat_id=int(settings.admin_chat_id),
                        text=f"{admin_message}\n\n[Payment Screenshot - Please check manually]",
                        reply_markup=InlineKeyboardMarkup(keyboard)
                    )
                
                self.logger.info(f"Payment verification sent to admin for user {user.id} on bot {self.config.bot_id}")
                
        except Exception as e:
            self.logger.error(f"Error sending admin payment verification: {e}")

    async def _handle_admin_approve_payment(self, query, context):
        """Handle admin approval of payment verification"""
        try:
            # Parse callback data: approve_payment:user_id:bot_id
            _, user_id, bot_id = query.data.split(":")
            user_id = int(user_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            # Update user verification status
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    await query.answer("❌ User not found")
                    return
                
                if user_data.is_verified:
                    await query.answer("✅ User already verified")
                    return
                
                # Approve the payment
                user_data.is_verified = True
                
                # Update referrer if exists - FIXED LOGIC
                if user_data.referred_by:
                    self.logger.info(f"Admin approval: Processing referral reward for referrer {user_data.referred_by}")
                    referrer_result = await db.execute(
                        select(User).where(
                            User.user_id == user_data.referred_by,
                            User.bot_id == bot_id
                        )
                    )
                    referrer = referrer_result.scalar_one_or_none()
                    
                    if referrer:
                        # Calculate reward BEFORE incrementing count
                        old_count = referrer.referrals_count
                        new_count = old_count + 1
                        # Special logic: 1st referral = ₹110, 2nd-15th = ₹180, 16th+ = ₹190
                        if new_count == 1:
                            reward = settings.tier1_reward_inr  # ₹110 for first referral only
                        elif new_count <= settings.tier1_threshold:
                            reward = 180  # ₹180 for referrals 2-15
                        else:
                            reward = settings.tier2_reward_inr  # ₹190 for 16th+
                        
                        # Update referrer
                        referrer.referrals_count = new_count
                        referrer.total_earned += reward
                        
                        self.logger.info(f"Admin approval: Referrer {referrer.user_id} count: {old_count} → {new_count}, earned: +₹{reward}")
                        
                        # Notify referrer about the reward
                        try:
                            await context.bot.send_message(
                                chat_id=referrer.user_id,
                                text=f"🎉 **Referral Verified by Admin!**\n\n"
                                     f"💰 **Earned:** ₹{reward}\n"
                                     f"👥 **Total Referrals:** {new_count}\n"
                                     f"💵 **Total Earned:** ₹{referrer.total_earned}\n\n"
                                     f"🚀 Keep sharing your link to earn more!"
                            )
                        except TelegramError:
                            pass
                    else:
                        self.logger.warning(f"Admin approval: Referrer {user_data.referred_by} not found")
                
                await db.commit()
                
                # Update admin message
                await query.edit_message_caption(
                    caption=f"✅ **PAYMENT APPROVED**\n\n"
                           f"👤 **User:** {user_data.username or f'User {user_id}'}\n"
                           f"🤖 **Bot:** {bot_id}\n"
                           f"💰 **Amount:** ₹{settings.join_fee_inr}\n"
                           f"⏰ **Approved:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
                           f"👨‍💼 **Approved by:** Admin\n\n"
                           f"🎉 **User has been verified and can now start earning!**",
                    parse_mode="Markdown"
                )
                
                # Notify user of approval
                try:
                    referral_link = f"https://t.me/{self.config.username}?start={user_id}"
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 **Payment Approved!**\n\n"
                             f"✅ Your payment has been verified by our admin team.\n"
                             f"🚀 You can now start earning money through referrals!\n\n"
                             f"🔗 **Your Referral Link:**\n"
                             f"`{referral_link}`\n\n"
                             f"💰 **Start Earning:**\n"
                             f"• Share this link with friends\n"
                             f"• Earn ₹{settings.tier1_reward_inr} per referral (first {settings.tier1_threshold})\n"
                             f"• Earn ₹{settings.tier2_reward_inr} per referral (after {settings.tier1_threshold})\n\n"
                             f"🎯 **Minimum withdrawal:** ₹{settings.min_withdrawal_inr}",
                        parse_mode="Markdown"
                    )
                except Exception as notify_error:
                    self.logger.error(f"Failed to notify user {user_id}: {notify_error}")
                
                await query.answer("✅ Payment approved!")
                self.logger.info(f"Admin approved payment for user {user_id} on bot {bot_id}")
                
        except Exception as e:
            self.logger.error(f"Error in admin approve payment: {e}")
            await query.answer("❌ Error processing approval")

    async def _handle_admin_reject_payment(self, query, context):
        """Handle admin rejection of payment verification"""
        try:
            # Parse callback data: reject_payment:user_id:bot_id
            _, user_id, bot_id = query.data.split(":")
            user_id = int(user_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            # Update admin message
            await query.edit_message_caption(
                caption=f"❌ **PAYMENT REJECTED**\n\n"
                       f"👤 **User:** User {user_id}\n"
                       f"🤖 **Bot:** {bot_id}\n"
                       f"💰 **Amount:** ₹{settings.join_fee_inr}\n"
                       f"⏰ **Rejected:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
                       f"👨‍💼 **Rejected by:** Admin\n\n"
                       f"📝 **User will be notified to upload a better screenshot.**",
                parse_mode="Markdown"
            )
            
            # Notify user of rejection
            try:
                await context.bot.send_message(
                    chat_id=user_id,
                    text=f"❌ **Payment Verification Rejected**\n\n"
                         f"📋 Your payment screenshot could not be verified.\n\n"
                         f"📝 **Please upload a new screenshot that clearly shows:**\n"
                         f"• Amount: ₹{settings.join_fee_inr}\n"
                         f"• Transaction status: Success\n"
                         f"• Complete payment details\n"
                         f"• Clear, readable text\n\n"
                         f"💡 Take a new screenshot and try again.",
                    parse_mode="Markdown"
                )
            except Exception as notify_error:
                self.logger.error(f"Failed to notify user {user_id}: {notify_error}")
            
            await query.answer("❌ Payment rejected - user notified")
            self.logger.info(f"Admin rejected payment for user {user_id} on bot {bot_id}")
            
        except Exception as e:
            self.logger.error(f"Error in admin reject payment: {e}")
            await query.answer("❌ Error processing rejection")

    async def _handle_admin_user_profile(self, query, context):
        """Handle admin request for user profile"""
        try:
            # Parse callback data: user_profile:user_id:bot_id
            _, user_id, bot_id = query.data.split(":")
            user_id = int(user_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            # Get detailed user information
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    await query.answer("❌ User not found")
                    return
                
                # Get user's withdrawal history
                withdrawals_result = await db.execute(
                    select(WithdrawalRequest).where(
                        WithdrawalRequest.user_id == user_id,
                        WithdrawalRequest.bot_id == bot_id
                    ).order_by(WithdrawalRequest.requested_at.desc()).limit(5)
                )
                withdrawals = withdrawals_result.scalars().all()
                
                # Create user profile message
                profile_message = f"""👤 **USER PROFILE**

🆔 **Basic Information:**
• **User ID:** `{user_data.user_id}`
• **Username:** @{user_data.username if user_data.username else 'No username'}
• **Bot:** {bot_id}
• **Joined:** {user_data.joined_at.strftime('%d %b %Y, %I:%M %p')}
• **Verified:** {'✅ Yes' if user_data.is_verified else '❌ No'}
• **Banned:** {'❌ Yes' if user_data.is_banned else '✅ No'}

📊 **Referral Stats:**
• **Total Referrals:** {user_data.referrals_count}
• **Total Earned:** ₹{user_data.total_earned}
• **Referred By:** {user_data.referred_by or 'Direct join'}
• **Current Tier:** {'Tier 2' if user_data.referrals_count > settings.tier1_threshold else 'Tier 1'}

💰 **Withdrawal History:**"""
                
                if withdrawals:
                    for w in withdrawals:
                        status_emoji = "✅" if w.status == "approved" else "⏳" if w.status == "pending" else "❌"
                        profile_message += f"\n• {status_emoji} ₹{w.amount} - {w.requested_at.strftime('%d %b %Y')} ({w.status})"
                else:
                    profile_message += "\n• No withdrawals yet"
                
                # Send user profile
                await query.message.reply_text(profile_message, parse_mode="Markdown")
                await query.answer("📊 User profile loaded")
                
        except Exception as e:
            self.logger.error(f"Error in admin user profile: {e}")
            await query.answer("❌ Error loading profile")

    async def _handle_admin_pending_verifications(self, query, context):
        """Handle admin request for pending verifications"""
        try:
            # Parse callback data: pending_verifications:bot_id
            _, bot_id = query.data.split(":")
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            # Get pending verifications for this bot
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.bot_id == bot_id,
                        User.is_verified == False
                    ).order_by(User.joined_at.desc()).limit(10)
                )
                pending_users = result.scalars().all()
                
                if not pending_users:
                    await query.message.reply_text(
                        f"✅ **No Pending Verifications**\n\n"
                        f"🤖 **Bot:** {bot_id}\n"
                        f"📊 All users are verified!"
                    )
                    await query.answer("✅ No pending verifications")
                    return
                
                # Create pending verifications list
                pending_message = f"⏳ **PENDING VERIFICATIONS**\n\n🤖 **Bot:** {bot_id}\n\n"
                
                for user in pending_users:
                    time_since = datetime.now(timezone.utc) - user.joined_at
                    hours_ago = int(time_since.total_seconds() / 3600)
                    pending_message += f"👤 @{user.username or f'User{user.user_id}'} - {hours_ago}h ago\n"
                
                pending_message += f"\n📱 **Total Pending:** {len(pending_users)}"
                
                # Send pending verifications list
                await query.message.reply_text(pending_message, parse_mode="Markdown")
                await query.answer(f"📋 {len(pending_users)} pending verifications")
                
        except Exception as e:
            self.logger.error(f"Error in admin pending verifications: {e}")
            await query.answer("❌ Error loading pending verifications")

    async def _handle_withdrawal_upi_qr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle withdrawal UPI QR code upload with bot isolation"""
        user = update.effective_user
        user_id = user.id
        
        # Extract withdrawal amount from user state
        state_data = self.user_states[user_id]
        withdrawal_amount = int(state_data.split(":")[1])
        
        # Download the photo
        photo = update.message.photo[-1]  # Get highest resolution
        file = await context.bot.get_file(photo.file_id)
        
        # Download file bytes for admin notification
        async with httpx.AsyncClient() as client:
            response = await client.get(file.file_path)
            image_bytes = response.content
        
        # Get user data for admin notification (bot-isolated)
        async with get_async_db() as db:
            result = await db.execute(
                select(User).where(
                    User.user_id == user_id,
                    User.bot_id == self.config.bot_id
                )
            )
            user_data = result.scalar_one_or_none()
            
            if not user_data:
                del self.user_states[user_id]
                await update.message.reply_text("❌ User not found. Please /start again.")
                return
            
            # Create withdrawal request in database with bot isolation
            withdrawal_request = WithdrawalRequest(
                user_id=user_id,
                bot_id=self.config.bot_id,  # Bot isolation
                amount=withdrawal_amount,
                upi_id="pending_extraction",  # Will be updated after QR processing
                status="pending",
                notes=f"UPI QR uploaded by {user.full_name or user.username} on {self.config.username}"
            )
            
            db.add(withdrawal_request)
            await db.commit()
            await db.refresh(withdrawal_request)
            
            # Clear user state
            del self.user_states[user_id]
            
            # Send confirmation to user
            await update.message.reply_text(
                f"✅ **Withdrawal Request Submitted!**\n\n"
                f"💰 **Amount:** ₹{withdrawal_amount}\n"
                f"🆔 **Request ID:** #{withdrawal_request.req_id}\n"
                f"🤖 **Bot:** {self.config.username}\n\n"
                f"⏳ **Status:** Under Review\n\n"
                f"📋 Your withdrawal request has been sent to our admin team.\n"
                f"You'll be notified once it's processed (usually within 24 hours).\n\n"
                f"💡 **Current Balance:** ₹{user_data.total_earned - withdrawal_amount} (after processing)"
            )
            
            # Send detailed notification to admin
            await self._send_admin_withdrawal_notification(
                context, withdrawal_request, user_data, image_bytes
            )

    async def _send_admin_withdrawal_notification(self, context, withdrawal_request, user_data, image_bytes):
        """Send withdrawal request notification to admin with bot info"""
        try:
            # Calculate additional stats (bot-isolated)
            async with get_async_db() as db:
                # Get deposit amount (join fee if verified)
                deposit_amount = settings.join_fee_inr if user_data.is_verified else 0
                
                # Get total withdrawals so far (for this bot)
                withdrawal_result = await db.execute(
                    select(func.sum(WithdrawalRequest.amount))
                    .where(
                        WithdrawalRequest.user_id == user_data.user_id,
                        WithdrawalRequest.bot_id == self.config.bot_id,
                        WithdrawalRequest.status == "approved"
                    )
                )
                total_withdrawn = withdrawal_result.scalar() or 0
                
                # Calculate net balance after this withdrawal
                balance_after_withdrawal = user_data.total_earned - withdrawal_request.amount
                
            # Create comprehensive admin message
            admin_message = f"""🚨 **NEW WITHDRAWAL REQUEST**

👤 **User Information:**
• **Name:** {user_data.username or 'N/A'}
• **User ID:** `{user_data.user_id}`
• **Telegram:** @{user_data.username if user_data.username else 'No username'}
• **Bot:** {self.config.username} ({self.config.bot_id})
• **Joined:** {user_data.joined_at.strftime('%d %b %Y, %I:%M %p')}
• **Verified:** {'✅ Yes' if user_data.is_verified else '❌ No'}

💰 **Financial Summary:**
• **Current Balance:** ₹{user_data.total_earned}
• **Requested Amount:** ₹{withdrawal_request.amount}
• **Balance After:** ₹{balance_after_withdrawal}
• **Total Deposited:** ₹{deposit_amount}
• **Previously Withdrawn:** ₹{total_withdrawn}

📊 **Referral Stats:**
• **Total Referrals:** {user_data.referrals_count}
• **Total Earned:** ₹{user_data.total_earned}
• **Current Tier:** {'Tier 2' if user_data.referrals_count > settings.tier1_threshold else 'Tier 1'}

🆔 **Request Details:**
• **Request ID:** #{withdrawal_request.req_id}
• **Status:** {withdrawal_request.status.upper()}
• **Requested At:** {withdrawal_request.requested_at.strftime('%d %b %Y, %I:%M %p')}

📱 **UPI QR Code uploaded below** ⬇️"""
            
            # Create approval buttons with bot context
            keyboard = [
                [
                    InlineKeyboardButton("✅ APPROVE", callback_data=f"approve_withdrawal:{withdrawal_request.req_id}:{self.config.bot_id}"),
                    InlineKeyboardButton("❌ REJECT", callback_data=f"reject_withdrawal:{withdrawal_request.req_id}:{self.config.bot_id}")
                ],
                [
                    InlineKeyboardButton("📊 User Details", callback_data=f"user_details:{user_data.user_id}:{self.config.bot_id}"),
                    InlineKeyboardButton("📋 All Requests", callback_data=f"all_withdrawal_requests:{self.config.bot_id}")
                ]
            ]
            
            # Send message with UPI QR image to admin
            if settings.admin_chat_id:
                await context.bot.send_photo(
                    chat_id=int(settings.admin_chat_id),
                    photo=image_bytes,
                    caption=admin_message,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode="Markdown"
                )
            else:
                self.logger.warning("admin_chat_id not set - cannot send withdrawal notification")
                
        except Exception as e:
            self.logger.error(f"Error sending admin notification: {e}")

    def _get_welcome_message(self, name: str) -> str:
        """Get welcome message for new users with welcome bonus"""
        return f"""🎉 *Welcome to {self.config.username}, {name}!*

🎊 **CONGRATULATIONS! You've received ₹{settings.welcome_bonus_inr} in your wallet!** 🎊

💰 *Your Current Balance: ₹{settings.welcome_bonus_inr}*

🚀 *Start Earning MORE Money by Referring Friends!*

💰 *Earning Structure:*
• Earn ₹180 per referral (First 15 referrals)
• Earn ₹{settings.tier2_reward_inr} per referral (After 15 referrals)
• Minimum withdrawal: ₹{settings.min_withdrawal_inr}

📝 *How to Unlock Your ₹{settings.welcome_bonus_inr} + Start Earning:*
1️⃣ Pay one-time joining fee of ₹{settings.join_fee_inr} to get VERIFIED
2️⃣ Upload payment screenshot for verification
3️⃣ Get your unique referral link
4️⃣ Share with friends and earn ₹180 per referral!

💡 *After 1st referral: ₹{settings.welcome_bonus_inr} + ₹180 = ₹{settings.welcome_bonus_inr + 180}!*

📢 *Join our {settings.telegram_channel_name} channel for:*
• Daily earning tips and strategies
• Success stories from top earners
• Regular updates and announcements
• Exclusive earning opportunities

💳 *Payment Details:*
💵 Amount: ₹{settings.join_fee_inr}

🔄 Click "Pay ₹{settings.join_fee_inr} Fee (QR Code)" below to unlock your earnings:"""

    def _get_returning_user_message(self, user: User) -> str:
        """Get message for returning users"""
        if user.is_verified:
            # Calculate tier info
            current_tier = "Tier 1" if user.referrals_count <= settings.tier1_threshold else "Tier 2"
            current_reward = settings.tier1_reward_inr if user.referrals_count <= settings.tier1_threshold else settings.tier2_reward_inr
            
            return f"""👋 *Welcome back to {self.config.username}!*

🏆 *Your Current Stats:*
👥 Total Referrals: *{user.referrals_count}*
💰 Total Earned: *₹{user.total_earned}*
🎖️ Current Tier: *{current_tier}*
💵 Earning per referral: *₹{current_reward}*

🚀 *Ready to earn more?*
Use the buttons below to manage your account!"""
        else:
            return f"""👋 **Welcome back to {self.config.username}!**

🎊 **You have ₹{user.total_earned} in your account!** 🎊

⚠️ **Account Status: Not Verified**
💡 **Get verified to unlock withdrawals and start referring!**

To unlock your ₹{user.total_earned} and start earning MORE:
1️⃣ Pay ₹{settings.join_fee_inr} joining fee to get VERIFIED
2️⃣ Upload payment screenshot
3️⃣ Start referring friends for ₹180 each!

💳 **Payment Details:**
💵 Amount: ₹{settings.join_fee_inr}

🔄 Click "Pay ₹{settings.join_fee_inr} Fee (QR Code)" below after payment:"""

    def _get_main_keyboard(self, user_verified=False):
        """Get main inline keyboard based on user status"""
        if user_verified:
            keyboard = [
                [InlineKeyboardButton("💳 My Wallet", callback_data="show_wallet"),
                 InlineKeyboardButton("📊 My Referrals & Stats", callback_data="show_referrals")],
                [InlineKeyboardButton("💰 Withdraw Money", callback_data="start_withdrawal"),
                 InlineKeyboardButton("🔗 Get Referral Link", callback_data="get_referral_link")],
                [InlineKeyboardButton("📈 Leaderboard", callback_data="show_leaderboard"),
                 InlineKeyboardButton("ℹ️ Help & Support", callback_data="show_help")],
                [InlineKeyboardButton(f"📢 Join {settings.telegram_channel_name} Channel", url=settings.telegram_channel_url)],
                [InlineKeyboardButton("🚀 Earn MORE! Use Our Other Bots", callback_data="show_other_bots")]
            ]
        else:
            keyboard = [
                [InlineKeyboardButton("💳 My Wallet", callback_data="show_wallet"),
                 InlineKeyboardButton(f"✅ Pay ₹{settings.join_fee_inr} Fee (QR Code)", callback_data="verify_payment")],
                [InlineKeyboardButton("ℹ️ How it Works", callback_data="how_it_works"),
                 InlineKeyboardButton("📈 View Earnings Plan", callback_data="earnings_plan")],
                [InlineKeyboardButton(f"📢 Join {settings.telegram_channel_name} Channel", url=settings.telegram_channel_url)],
                [InlineKeyboardButton("🚀 Earn MORE! Use Our Other Bots", callback_data="show_other_bots")]
            ]
        return InlineKeyboardMarkup(keyboard)

    # Add other handler methods (handle_callback_query, handle_text_message, etc.)
    # These would need similar bot isolation modifications...
    # For brevity, I'll add key ones:

    async def handle_callback_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle inline keyboard callbacks with bot isolation"""
        try:
            query = update.callback_query
            await query.answer()
            
            # Debug logging to track which callback is triggered
            self.logger.info(f"Processing callback query: '{query.data}' from user {query.from_user.id}")
            
            if query.data == "verify_payment":
                # Generate QR code with bot isolation
                user_id = query.from_user.id
                
                # Round-robin selection of UPI ID
                upi_index = user_id % len(settings.receiver_upi_ids)
                selected_upi = settings.receiver_upi_ids[upi_index]
                
                # Generate QR code
                qr_image_bytes = generate_upi_qr(
                    upi_id=selected_upi,
                    amount=settings.join_fee_inr,
                    payee_name=f"{settings.payee_name} - {self.config.username}"
                )
                
                # Create the message
                caption = f"""✅ **Step 1: Pay the Joining Fee for {self.config.username}**

**Scan the QR Code** above with any UPI app to pay **₹{settings.join_fee_inr}**.

**Details:**
- **Amount**: ₹{settings.join_fee_inr} (pre-filled)
- **Payee**: {settings.payee_name}
- **Bot**: {self.config.username}

After paying, **take a screenshot** of the successful transaction.

Then, come back here and send the screenshot to complete your verification.
"""
                
                # Add "I have paid" button
                keyboard = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ I Have Paid, Upload Screenshot", callback_data="upload_screenshot")]
                ])

                # Send the QR code image
                await context.bot.send_photo(
                    chat_id=query.message.chat_id,
                    photo=qr_image_bytes,
                    caption=caption,
                    parse_mode="Markdown",
                    reply_markup=keyboard
                )
                # Don't delete the message to maintain chat history
                # await query.message.delete() # Commented out to keep chat history
                
            elif query.data == "upload_screenshot":
                # Since this callback comes from a photo message (QR code), we need to edit the caption
                self.logger.info("Processing upload_screenshot callback - attempting to edit photo caption")
                try:
                    await query.edit_message_caption(
                        caption="📸 **Upload Payment Screenshot**\n\n"
                                "Please upload a clear screenshot of your successful payment.\n\n"
                                "**Requirements:**\n"
                                "✅ Clear, complete screenshot\n"
                                "✅ Shows payment amount and success status\n"
                                "✅ All text must be readable\n\n"
                                "💡 Send the screenshot as a photo now:",
                        parse_mode="Markdown",
                        reply_markup=None  # Remove the button after clicking
                    )
                    self.logger.info("Successfully edited photo caption for upload_screenshot")
                except Exception as caption_error:
                    self.logger.error(f"Failed to edit photo caption: {caption_error}")
                    # Fallback: send a new message if editing caption fails
                    try:
                        await query.message.reply_text(
                            "📸 **Upload Payment Screenshot**\n\n"
                            "Please upload a clear screenshot of your successful payment.\n\n"
                            "**Requirements:**\n"
                            "✅ Clear, complete screenshot\n"
                            "✅ Shows payment amount and success status\n"
                            "✅ All text must be readable\n\n"
                            "💡 Send the screenshot as a photo now:",
                            parse_mode="Markdown"
                        )
                        self.logger.info("Sent fallback message for upload_screenshot")
                    except Exception as fallback_error:
                        self.logger.error(f"Fallback message also failed: {fallback_error}")
                        # Final fallback: send a simple message to user's chat
                        await context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text="📸 Please upload a clear screenshot of your payment now."
                        )
                
            elif query.data == "show_wallet":
                await self._handle_show_wallet(query, context)
                
            elif query.data == "show_referrals":
                await self._handle_show_referrals(query, context)
                
            elif query.data == "get_referral_link":
                await self._handle_get_referral_link(query, context)
                
            elif query.data == "start_withdrawal":
                await self._handle_start_withdrawal(query, context)
                
            elif query.data == "refresh_referrals":
                # Clear cache and show updated referrals
                user_id = query.from_user.id
                try:
                    cache_key = await cache.get_user_cache_key(f"{self.config.bot_id}:{user_id}", "referrals")
                    await cache.delete(cache_key)
                    await self._handle_show_referrals(query, context)
                except Exception as cache_error:
                    self.logger.error(f"Cache error in refresh_referrals: {cache_error}")
                    # Still show referrals even if cache fails
                    await self._handle_show_referrals(query, context)
                
            elif query.data == "show_leaderboard":
                await self._handle_show_leaderboard(query, context)
                
            elif query.data == "show_help":
                await self._handle_show_help(query, context)
                
            elif query.data == "how_it_works":
                await self._handle_how_it_works(query, context)
                
            elif query.data == "earnings_plan":
                await self._handle_earnings_plan(query, context)
            
            # Admin verification handlers
            elif query.data.startswith("approve_payment:"):
                await self._handle_admin_approve_payment(query, context)
            
            elif query.data.startswith("reject_payment:"):
                await self._handle_admin_reject_payment(query, context)
            
            elif query.data.startswith("user_profile:"):
                await self._handle_admin_user_profile(query, context)
            
            elif query.data.startswith("pending_verifications:"):
                await self._handle_admin_pending_verifications(query, context)
            
            elif query.data == "show_other_bots":
                await self._handle_show_other_bots(query, context)
            
            elif query.data == "back_to_main":
                await self._handle_back_to_main(query, context)
            
            # Admin withdrawal handlers
            elif query.data.startswith("approve_withdrawal:"):
                await self._handle_admin_approve_withdrawal(query, context)
            
            elif query.data.startswith("reject_withdrawal:"):
                await self._handle_admin_reject_withdrawal(query, context)
            
            elif query.data.startswith("user_details:"):
                await self._handle_admin_user_details(query, context)
            
            elif query.data.startswith("all_withdrawal_requests:"):
                await self._handle_admin_all_withdrawal_requests(query, context)
            
            elif query.data == "restart_bot":
                # Handle restart button - redirect to start
                await query.answer("Restarting...")
                # Create a fake update for the start command
                fake_update = Update(
                    update_id=update.update_id,
                    message=query.message,
                    callback_query=None
                )
                await self.start(fake_update, context)
            
        except Exception as e:
            self.logger.error(f"Error handling callback query '{query.data}': {e}")
            try:
                # Try to answer the callback query to prevent "loading" state
                await query.answer("❌ Error occurred. Please try again.")
                
                # Send error message - try to reply to the message first
                try:
                    await query.message.reply_text(
                        "❌ Something went wrong. Please try /start to restart.",
                        reply_markup=InlineKeyboardMarkup([[
                            InlineKeyboardButton("🔄 Restart", callback_data="restart_bot")
                        ]])
                    )
                except:
                    # Fallback: send new message
                    await context.bot.send_message(
                        chat_id=query.message.chat_id,
                        text="❌ Something went wrong. Please try /start to restart."
                    )
            except Exception as error_handling_error:
                self.logger.error(f"Error in error handling: {error_handling_error}")
                # Final fallback
                try:
                    await query.answer("Error occurred")
                except:
                    pass

    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle text messages with AI chat service"""
        try:
            user = update.effective_user
            if not user:
                return
            
            message_text = update.message.text
            if not message_text:
                return
            
            user_id = user.id
            self.logger.info(f"Processing text message from user {user_id}: {message_text[:50]}...")
            
            # Check if user is in a specific state (withdrawal amount input)
            if user_id in self.user_states and self.user_states[user_id] == "awaiting_withdrawal_amount":
                await self._handle_withdrawal_amount_input(update, context)
                return
            
            # Get user context for personalized AI response
            user_context = None
            try:
                async with get_async_db() as db:
                    result = await db.execute(
                        select(User).where(
                            User.user_id == user_id,
                            User.bot_id == self.config.bot_id
                        )
                    )
                    user_data = result.scalar_one_or_none()
                    
                    if user_data:
                        user_context = {
                            'is_verified': user_data.is_verified,
                            'total_earned': user_data.total_earned,
                            'referrals_count': user_data.referrals_count,
                            'bot_username': self.config.username
                        }
            except Exception as e:
                self.logger.error(f"Error getting user context: {e}")
            
            # Get AI response
            try:
                ai_response = await ai_chat_service.get_ai_response(message_text, user_context)
                
                # Add keyboard based on user status
                keyboard = None
                if user_context and user_context.get('is_verified'):
                    keyboard = self._get_main_keyboard(user_verified=True)
                else:
                    keyboard = self._get_main_keyboard(user_verified=False)
                
                await update.message.reply_text(
                    ai_response, 
                    parse_mode="Markdown", 
                    reply_markup=keyboard
                )
                
            except Exception as e:
                self.logger.error(f"Error getting AI response: {e}")
                # Fallback response - only send if no previous message sent
                if not hasattr(update.message, '_ai_response_sent'):
                    await update.message.reply_text(
                        f"👋 Hello! I'm {self.config.username}'s assistant.\n\n"
                        "💰 Ready to start earning? Use the buttons below!",
                        reply_markup=self._get_main_keyboard(user_verified=False)
                    )
                    update.message._ai_response_sent = True
                
        except Exception as e:
            self.logger.error(f"Error in handle_text_message: {e}")
            # Only send error if no response was already sent
            if not hasattr(update.message, '_error_sent'):
                await update.message.reply_text("Sorry, I didn't understand that. Please try again.")
                update.message._error_sent = True

    async def _handle_withdrawal_amount_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle withdrawal amount input from user"""
        user_id = update.effective_user.id
        message_text = update.message.text
        
        try:
            # Try to parse amount
            amount = int(message_text.strip())
            
            # Get user data to validate withdrawal
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == self.config.bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    await update.message.reply_text("Please /start first.")
                    return
                
                # Validate amount
                if amount < settings.min_withdrawal_inr:
                    await update.message.reply_text(
                        f"❌ **Minimum withdrawal amount is ₹{settings.min_withdrawal_inr}**\n\n"
                        f"Please enter an amount between ₹{settings.min_withdrawal_inr} and ₹{user_data.total_earned}:"
                    )
                    return
                
                if amount > user_data.total_earned:
                    await update.message.reply_text(
                        f"❌ **Insufficient balance!**\n\n"
                        f"Your balance: ₹{user_data.total_earned}\n"
                        f"Requested: ₹{amount}\n\n"
                        f"Please enter an amount between ₹{settings.min_withdrawal_inr} and ₹{user_data.total_earned}:"
                    )
                    return
                
                # Amount is valid, proceed to UPI QR upload
                self.user_states[user_id] = f"awaiting_upi_qr:{amount}"
                
                await update.message.reply_text(
                    f"✅ **Withdrawal Amount Confirmed: ₹{amount}**\n\n"
                    f"📱 **Step 2: Upload Your UPI QR Code**\n\n"
                    f"Please upload a clear photo of your UPI QR code.\n\n"
                    f"**Requirements:**\n"
                    f"✅ Clear, readable QR code\n"
                    f"✅ Your UPI ID should be visible\n"
                    f"✅ Take photo directly from your UPI app\n\n"
                    f"📸 **Send the QR code photo now:**",
                    parse_mode="Markdown"
                )
                
        except ValueError:
            # Invalid number format
            await update.message.reply_text(
                "❌ **Invalid amount format!**\n\n"
                "Please enter a valid number (e.g., 250, 500, 1000):"
            )
        except Exception as e:
            self.logger.error(f"Error handling withdrawal amount: {e}")
            await update.message.reply_text(
                "❌ **Something went wrong!**\n\n"
                "Please try again or contact support."
            )

    async def _handle_show_referrals(self, query, context):
        """Handle show referrals button"""
        user_id = query.from_user.id
        
        async with get_async_db() as db:
            result = await db.execute(
                select(User).where(
                    User.user_id == user_id,
                    User.bot_id == self.config.bot_id
                )
            )
            user_data = result.scalar_one_or_none()
            
            if not user_data:
                # Send new message instead of editing to maintain chat history
                await query.message.reply_text("Please /start first.")
                return
            
            # Calculate tier and rewards
            current_tier = "Tier 1" if user_data.referrals_count <= settings.tier1_threshold else "Tier 2"
            current_reward = settings.tier1_reward_inr if user_data.referrals_count <= settings.tier1_threshold else settings.tier2_reward_inr
            next_tier_at = settings.tier1_threshold + 1 if user_data.referrals_count <= settings.tier1_threshold else None
            
            message = f"""📊 *Your Referral Stats on {self.config.username}*

👥 Total Referrals: {user_data.referrals_count}
💰 Total Earned: ₹{user_data.total_earned}
🏆 Current Tier: {current_tier}
💵 Current Reward: ₹{current_reward} per referral

🔗 Your Referral Link:
`https://t.me/{self.config.username}?start={user_data.user_id}`

Share this link to earn rewards!"""
            
            if next_tier_at:
                remaining = next_tier_at - user_data.referrals_count
                message += f"\n\n🎯 {remaining} more referrals to reach Tier 2 (₹{settings.tier2_reward_inr} per referral)!"
            
            keyboard = [
                [InlineKeyboardButton("🔄 Refresh", callback_data="refresh_referrals"),
                 InlineKeyboardButton("💰 Withdraw", callback_data="start_withdrawal")],
                [InlineKeyboardButton("🔗 Copy Link", callback_data="get_referral_link")]
            ]
            
            # Send new message to maintain chat history instead of editing
            try:
                await query.message.reply_text(
                    message, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.logger.error(f"Error sending referral stats: {e}")
                # Fallback without markdown
                await query.message.reply_text(
                    f"📊 Your Referral Stats on {self.config.username}\n\n"
                    f"👥 Total Referrals: {user_data.referrals_count}\n"
                    f"💰 Total Earned: ₹{user_data.total_earned}\n"
                    f"🔗 Your Referral Link: https://t.me/{self.config.username}?start={user_data.user_id}",
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

    async def _handle_get_referral_link(self, query, context):
        """Handle get referral link button"""
        user_id = query.from_user.id
        
        async with get_async_db() as db:
            result = await db.execute(
                select(User).where(
                    User.user_id == user_id,
                    User.bot_id == self.config.bot_id
                )
            )
            user_data = result.scalar_one_or_none()
            
            if not user_data:
                await query.edit_message_text("Please /start first.")
                return
                
            if not user_data.is_verified:
                await query.edit_message_text(
                    "⚠️ Please complete verification first to get your referral link!\n\n"
                    "Click 'Pay ₹200 Fee (QR Code)' to start verification."
                )
                return
            
            referral_link = f"https://t.me/{self.config.username}?start={user_data.user_id}"
            
            message = f"""🔗 *Your Referral Link for {self.config.username}:*

`{referral_link}`

📋 *Share this link and earn:*
• ₹{settings.tier1_reward_inr} per referral (First {settings.tier1_threshold})
• ₹{settings.tier2_reward_inr} per referral (After {settings.tier1_threshold})

📱 *Best places to share:*
• WhatsApp status & groups
• Instagram stories & posts  
• Facebook timeline & groups
• Twitter & LinkedIn

💡 *Tip:* Add a personal message when sharing!"""

            keyboard = [
                [InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")],
                [InlineKeyboardButton("💰 Withdraw", callback_data="start_withdrawal")]
            ]
            
            try:
                await query.edit_message_text(
                    message, 
                    reply_markup=InlineKeyboardMarkup(keyboard), 
                    parse_mode="Markdown"
                )
            except Exception as e:
                self.logger.error(f"Error editing referral link message: {e}")
                # Fallback without markdown
                await query.edit_message_text(
                    f"🔗 Your Referral Link for {self.config.username}:\n\n{referral_link}\n\nShare this link to earn money!", 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )

    async def _handle_start_withdrawal(self, query, context):
        """Handle start withdrawal button"""
        user_id = query.from_user.id
        
        async with get_async_db() as db:
            result = await db.execute(
                select(User).where(
                    User.user_id == user_id,
                    User.bot_id == self.config.bot_id
                )
            )
            user_data = result.scalar_one_or_none()
            
            if not user_data:
                await query.edit_message_text("Please /start first.")
                return
                
            if not user_data.is_verified:
                await query.edit_message_text(
                    "⚠️ Please complete verification first!\n\n"
                    "Click 'Pay ₹200 Fee (QR Code)' to start verification."
                )
                return
            
            if user_data.total_earned < settings.min_withdrawal_inr:
                await query.edit_message_text(
                    f"💰 **Withdrawal Requirements:**\n\n"
                    f"Current Balance: ₹{user_data.total_earned}\n"
                    f"Minimum Withdrawal: ₹{settings.min_withdrawal_inr}\n\n"
                    f"You need ₹{settings.min_withdrawal_inr - user_data.total_earned} more to withdraw.\n\n"
                    f"🚀 Share your referral link to earn more!"
                )
                return
            
            message = f"""💰 **Withdrawal Request**

📊 **Your Balance:** ₹{user_data.total_earned}
⭐ **Min. Amount:** ₹{settings.min_withdrawal_inr}
🏷️ **Max. Amount:** ₹{user_data.total_earned}

💡 **How much would you like to withdraw?**

Please reply with the amount (e.g., 250, 500, 1000)"""

            # Set user state for withdrawal
            self.user_states[user_id] = "awaiting_withdrawal_amount"
            
            await query.edit_message_text(message, parse_mode="Markdown")

    async def _handle_show_leaderboard(self, query, context):
        """Handle show leaderboard button"""
        try:
            async with get_async_db() as db:
                # Get top 10 users for this bot
                result = await db.execute(
                    select(User)
                    .where(User.bot_id == self.config.bot_id)
                    .order_by(User.total_earned.desc())
                    .limit(10)
                )
                top_users = result.scalars().all()
                
                if not top_users:
                    await query.edit_message_text("📈 Leaderboard\n\nNo users found yet. Be the first to start earning!")
                    return
                
                message = f"📈 Top Earners on {self.config.username}\n\n"
                
                for i, user in enumerate(top_users, 1):
                    emoji = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
                    username = user.username or f"User{user.user_id}"
                    message += f"{emoji} {username}: ₹{user.total_earned} ({user.referrals_count} referrals)\n"
                
                message += f"\n🚀 Start referring to climb the leaderboard!"
                
                keyboard = [
                    [InlineKeyboardButton("🔗 Get My Link", callback_data="get_referral_link")],
                    [InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")]
                ]
                
                await query.edit_message_text(
                    message, 
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
        except Exception as e:
            self.logger.error(f"Error in leaderboard: {e}")
            await query.edit_message_text("Sorry, couldn't load leaderboard. Please try again.")

    async def _handle_show_help(self, query, context):
        """Handle show help button"""
        try:
            message = f"""🆘 Help & Support - {self.config.username}

❓ Frequently Asked Questions:

💳 Payment Issues:
• Ensure screenshot shows complete payment details
• Amount should be exactly ₹{settings.join_fee_inr}
• Screenshot should be clear and readable

👥 Referral Issues:
• Friend must complete verification to count
• Referral reward: ₹180 (first {settings.tier1_threshold}), then ₹{settings.tier2_reward_inr}
• Check your stats to track referrals

💰 Withdrawal Process:
• Minimum: ₹{settings.min_withdrawal_inr}
• Upload your UPI QR code
• Admin processes within 24 hours
• Direct UPI payment to your account

🔧 Technical Issues:
• Try refreshing your stats
• Restart the bot with /start
• Ensure good internet connection

💬 Still need help? Ask me anything like:
• "How to get more referrals?"
• "When will I receive payment?"
• "Is this really profitable?"

🤖 I'm here to help you succeed!"""

            keyboard = [
                [InlineKeyboardButton("🔗 Get Referral Link", callback_data="get_referral_link")],
                [InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")],
                [InlineKeyboardButton(f"📢 Join {settings.telegram_channel_name} Channel", url=settings.telegram_channel_url)]
            ]
            
            await query.edit_message_text(
                message, 
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
        except Exception as e:
            self.logger.error(f"Error in help handler: {e}")
            await query.edit_message_text(
                f"🆘 Help & Support - {self.config.username}\n\n"
                f"For help with payments, referrals, or withdrawals, please contact support.\n\n"
                f"💰 Join fee: ₹{settings.join_fee_inr}\n"
                f"💵 Referral rewards: ₹180-₹{settings.tier2_reward_inr}\n"
                f"🏦 Minimum withdrawal: ₹{settings.min_withdrawal_inr}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")
                ]])
            )

    async def _handle_how_it_works(self, query, context):
        """Handle how it works button"""
        message = f"""🚀 *How {self.config.username} Works:*

*💳 Step 1: Join the Platform*
• Pay one-time fee of ₹{settings.join_fee_inr}
• Upload payment screenshot
• Get instantly verified

*🔗 Step 2: Get Your Link*
• Receive unique referral link
• Share with friends and family
• Track your referrals in real-time

*💰 Step 3: Start Earning*
• Earn ₹180 per referral (First {settings.tier1_threshold})
• Earn ₹{settings.tier2_reward_inr} per referral (After {settings.tier1_threshold})
• No limit on earnings!

*🏦 Step 4: Withdraw Money*
• Minimum withdrawal: ₹{settings.min_withdrawal_inr}
• Direct UPI payments
• Process within 24 hours

*📱 Best Platforms to Share:*
• WhatsApp (Groups & Status)
• Instagram (Stories & Posts)
• Facebook (Timeline & Groups)
• Twitter, LinkedIn, etc.

*🎯 Success Tips:*
• Share with personal message
• Explain the earning opportunity
• Help friends with verification

Ready to start? Click 'Pay Fee' below!"""

        keyboard = [
            [InlineKeyboardButton("✅ Pay ₹200 Fee (QR Code)", callback_data="verify_payment")],
            [InlineKeyboardButton("📈 View Earnings Plan", callback_data="earnings_plan")]
        ]
        
        try:
            await query.edit_message_text(
                message, 
                reply_markup=InlineKeyboardMarkup(keyboard), 
                parse_mode="Markdown"
            )
        except Exception as e:
            self.logger.error(f"Error in how_it_works: {e}")
            # Fallback without markdown
            await query.edit_message_text(
                f"🚀 How {self.config.username} Works:\n\n"
                f"1. Pay ₹{settings.join_fee_inr} joining fee\n"
                f"2. Get verified instantly\n"
                f"3. Share your referral link\n"
                f"4. Earn ₹180 per referral\n"
                f"5. Withdraw minimum ₹{settings.min_withdrawal_inr}\n\n"
                f"Ready to start earning?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    async def _handle_earnings_plan(self, query, context):
        """Handle earnings plan button"""
        message = f"""📈 *Earnings Plan - {self.config.username}*

💰 *Tier-based Reward System:*

🥉 *Tier 1 (First {settings.tier1_threshold} Referrals)*
• Reward: ₹180 per successful referral
• Total possible: ₹{180 * settings.tier1_threshold}

🥈 *Tier 2 (After {settings.tier1_threshold} Referrals)*
• Reward: ₹{settings.tier2_reward_inr} per successful referral
• No upper limit!

💡 *Example Earnings:*

👥 *10 Referrals:* ₹{180 * 10}
👥 *20 Referrals:* ₹{(180 * settings.tier1_threshold) + (settings.tier2_reward_inr * (20 - settings.tier1_threshold))}
👥 *50 Referrals:* ₹{(180 * settings.tier1_threshold) + (settings.tier2_reward_inr * (50 - settings.tier1_threshold))}
👥 *100 Referrals:* ₹{(180 * settings.tier1_threshold) + (settings.tier2_reward_inr * (100 - settings.tier1_threshold))}

🏦 *Withdrawal Details:*
• Minimum: ₹{settings.min_withdrawal_inr}
• Method: Direct UPI transfer
• Processing: Within 24 hours
• Fees: Completely FREE!

🚀 *ROI Calculation:*
• Investment: ₹{settings.join_fee_inr} (one-time)
• Break-even: Just 1 successful referral!
• Profit: Everything after first referral

*The more you share, the more you earn!*"""

        keyboard = [
                        [InlineKeyboardButton(f"✅ Join Now - Pay ₹{settings.join_fee_inr}", callback_data="verify_payment")],
            [InlineKeyboardButton("ℹ️ How it Works", callback_data="how_it_works")]
        ]
        
        try:
            await query.edit_message_text(
                message, 
                reply_markup=InlineKeyboardMarkup(keyboard), 
                parse_mode="Markdown"
            )
        except Exception as e:
            self.logger.error(f"Error in earnings_plan: {e}")
            # Fallback without markdown
            await query.edit_message_text(
                f"📈 Earnings Plan - {self.config.username}\n\n"
                f"💰 Tier 1: ₹180 per referral (first {settings.tier1_threshold})\n"
                f"💎 Tier 2: ₹{settings.tier2_reward_inr} per referral (after {settings.tier1_threshold})\n\n"
                f"💡 Example: 10 referrals = ₹{180 * 10}\n"
                f"🏦 Minimum withdrawal: ₹{settings.min_withdrawal_inr}\n"
                f"🚀 Investment: ₹{settings.join_fee_inr} (one-time)\n\n"
                f"Ready to start earning?",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    async def error_handler(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        """Handle errors"""
        self.logger.error(f"Exception while handling an update: {context.error}")
        
        if isinstance(update, Update) and update.effective_message:
            await update.effective_message.reply_text(
                "❌ Something went wrong. Please try again later."
            )

    def run_bot(self) -> None:
        """Run the isolated bot instance - simplified for async management"""
        # This method is now handled by the MultiBotManager
        # keeping for backward compatibility with single bot mode
        if not self.config.token:
            raise RuntimeError(f"Bot token is not configured for {self.config.bot_id}")
        
        self.application = Application.builder().token(self.config.token).build()
        
        # Add handlers in specific order to prevent conflicts
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("referrals", self.referrals))
        self.application.add_handler(CallbackQueryHandler(self.handle_callback_query))  # Handle callbacks first
        self.application.add_handler(MessageHandler(filters.PHOTO, self.handle_photo))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_text_message))
        
        # Add error handler
        self.application.add_error_handler(self.error_handler)
        
        self.logger.info(f"Starting bot {self.config.bot_id} (@{self.config.username})")
        
        # Use run_polling - this handles the event loop internally
        self.application.run_polling(
            poll_interval=1.0,
            timeout=10,
            bootstrap_retries=5,
            read_timeout=30,
            write_timeout=30,
            connect_timeout=30,
        )

    async def _handle_show_other_bots(self, query, context):
        """Handle show other bots button - cross-promotion feature"""
        try:
            from src.config import settings
            
            # Get all bot configurations
            all_configs = settings.get_bot_configs()
            current_bot_id = self.config.bot_id
            
            # Filter out current bot
            other_bots = [config for config in all_configs if config.bot_id != current_bot_id]
            
            message = f"""🚀 MASSIVE EARNING OPPORTUNITY!

You're currently using {self.config.username} - but did you know we have {len(other_bots)} MORE BOTS where you can earn even MORE money?

💰 Multiply Your Earnings {len(all_configs)}X!
Each bot has its own referral system, so you can:
• Join all {len(all_configs)} bots
• Get {len(all_configs)} different referral links  
• Earn ₹180-₹190 per referral on EACH bot
• Total potential: ₹{180 * len(all_configs)} per friend!

🤖 Our Other Money-Making Bots:"""

            # Create buttons for other bots
            keyboard = []
            for config in other_bots:
                bot_link = f"https://t.me/{config.username}"
                keyboard.append([InlineKeyboardButton(f"💰 Join @{config.username}", url=bot_link)])
            
            # Add back button
            keyboard.append([InlineKeyboardButton("🔙 Back to Main Menu", callback_data="back_to_main")])
            
            message += f"""

🎯 Strategy for MAXIMUM Earnings:
1. Join all {len(all_configs)} bots (₹200 each = ₹{settings.join_fee_inr * len(all_configs)} total investment)
2. Get verified on each bot
3. Share all {len(all_configs)} referral links with same friends
4. Earn ₹{180 * len(all_configs)} per friend instead of just ₹180!

💡 Example: 10 friends × ₹{180 * len(all_configs)} = ₹{180 * len(all_configs) * 10} total earnings!

🚀 Click the bots below to join them now:"""

            await query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            
        except Exception as e:
            self.logger.error(f"Error showing other bots: {e}")
            await query.answer("❌ Error loading other bots")

    async def _handle_back_to_main(self, query, context):
        """Handle back to main menu button"""
        try:
            user_id = query.from_user.id
            
            # Get user data to determine verified status
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == self.config.bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if user_data:
                    if user_data.is_verified:
                        message = f"""👋 **Welcome back to {self.config.username}!**

🏆 **Your Current Stats:**
👥 Total Referrals: **{user_data.referrals_count}**
💰 Total Earned: **₹{user_data.total_earned}**

🚀 **Ready to earn more?**
Use the buttons below to manage your account!"""
                    else:
                        message = f"""👋 **Welcome back to {self.config.username}!**

⚠️ **Account Status: Not Verified**

To start earning, you need to:
1️⃣ Pay ₹{settings.join_fee_inr} joining fee
2️⃣ Upload payment screenshot

🔄 Click "Pay ₹200 Fee (QR Code)" below after payment:"""
                    
                    keyboard = self._get_main_keyboard(user_verified=user_data.is_verified)
                else:
                    message = f"Welcome to {self.config.username}! Please /start first."
                    keyboard = self._get_main_keyboard(user_verified=False)
            
            await query.edit_message_text(
                message,
                reply_markup=keyboard,
                parse_mode="Markdown"
            )
            
        except Exception as e:
            self.logger.error(f"Error in back to main: {e}")
            await query.answer("❌ Error loading main menu")

    async def _handle_admin_approve_withdrawal(self, query, context):
        """Handle admin approval of withdrawal request"""
        try:
            # Parse callback data: approve_withdrawal:req_id:bot_id
            _, req_id, bot_id = query.data.split(":")
            req_id = int(req_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            async with get_async_db() as db:
                # Get withdrawal request
                result = await db.execute(
                    select(WithdrawalRequest).where(
                        WithdrawalRequest.req_id == req_id,
                        WithdrawalRequest.bot_id == bot_id
                    )
                )
                withdrawal = result.scalar_one_or_none()
                
                if not withdrawal:
                    await query.answer("❌ Withdrawal request not found")
                    return
                
                if withdrawal.status != "pending":
                    await query.answer("❌ Request already processed")
                    return
                
                # Update withdrawal status
                withdrawal.status = "approved"
                withdrawal.processed_at = datetime.now(timezone.utc)
                withdrawal.processed_by = "Admin"
                
                # Update user's total earned (subtract withdrawn amount)
                user_result = await db.execute(
                    select(User).where(
                        User.user_id == withdrawal.user_id,
                        User.bot_id == bot_id
                    )
                )
                user_data = user_result.scalar_one_or_none()
                
                if user_data:
                    user_data.total_earned -= withdrawal.amount
                
                await db.commit()
                
                # Update admin message
                await query.edit_message_caption(
                    caption=f"✅ **WITHDRAWAL APPROVED**\n\n"
                           f"👤 **User:** {withdrawal.user_id}\n"
                           f"💰 **Amount:** ₹{withdrawal.amount}\n"
                           f"🏦 **UPI:** {withdrawal.upi_id}\n"
                           f"⏰ **Approved:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
                           f"👨‍💼 **Approved by:** Admin\n\n"
                           f"💳 **Please process UPI payment manually!**",
                    parse_mode="Markdown"
                )
                
                # Notify user
                try:
                    await context.bot.send_message(
                        chat_id=withdrawal.user_id,
                        text=f"🎉 **Withdrawal Approved!**\n\n"
                             f"💰 **Amount:** ₹{withdrawal.amount}\n"
                             f"🆔 **Request ID:** #{withdrawal.req_id}\n"
                             f"⏰ **Approved:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n\n"
                             f"💳 **Your payment will be processed within 24 hours.**\n"
                             f"You'll receive the money directly in your UPI account.",
                        parse_mode="Markdown"
                    )
                except Exception as notify_error:
                    self.logger.error(f"Failed to notify user {withdrawal.user_id}: {notify_error}")
                
                await query.answer("✅ Withdrawal approved!")
                self.logger.info(f"Admin approved withdrawal request {req_id} for user {withdrawal.user_id}")
                
        except Exception as e:
            self.logger.error(f"Error in admin approve withdrawal: {e}")
            await query.answer("❌ Error processing approval")

    async def _handle_admin_reject_withdrawal(self, query, context):
        """Handle admin rejection of withdrawal request"""
        try:
            # Parse callback data: reject_withdrawal:req_id:bot_id
            _, req_id, bot_id = query.data.split(":")
            req_id = int(req_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            async with get_async_db() as db:
                # Get withdrawal request
                result = await db.execute(
                    select(WithdrawalRequest).where(
                        WithdrawalRequest.req_id == req_id,
                        WithdrawalRequest.bot_id == bot_id
                    )
                )
                withdrawal = result.scalar_one_or_none()
                
                if not withdrawal:
                    await query.answer("❌ Withdrawal request not found")
                    return
                
                if withdrawal.status != "pending":
                    await query.answer("❌ Request already processed")
                    return
                
                # Update withdrawal status
                withdrawal.status = "rejected"
                withdrawal.processed_at = datetime.now(timezone.utc)
                withdrawal.processed_by = "Admin"
                withdrawal.notes = "Rejected by admin"
                
                await db.commit()
                
                # Update admin message
                await query.edit_message_caption(
                    caption=f"❌ **WITHDRAWAL REJECTED**\n\n"
                           f"👤 **User:** {withdrawal.user_id}\n"
                           f"💰 **Amount:** ₹{withdrawal.amount}\n"
                           f"⏰ **Rejected:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n"
                           f"👨‍💼 **Rejected by:** Admin\n\n"
                           f"📝 **User will be notified.**",
                    parse_mode="Markdown"
                )
                
                # Notify user
                try:
                    await context.bot.send_message(
                        chat_id=withdrawal.user_id,
                        text=f"❌ **Withdrawal Request Rejected**\n\n"
                             f"💰 **Amount:** ₹{withdrawal.amount}\n"
                             f"🆔 **Request ID:** #{withdrawal.req_id}\n"
                             f"⏰ **Rejected:** {datetime.now().strftime('%d %b %Y, %I:%M %p')}\n\n"
                             f"📝 **Reason:** Your UPI QR code was unclear or invalid.\n"
                             f"💡 **Next Steps:** Upload a clearer UPI QR code and try again.",
                        parse_mode="Markdown"
                    )
                except Exception as notify_error:
                    self.logger.error(f"Failed to notify user {withdrawal.user_id}: {notify_error}")
                
                await query.answer("❌ Withdrawal rejected - user notified")
                self.logger.info(f"Admin rejected withdrawal request {req_id} for user {withdrawal.user_id}")
                
        except Exception as e:
            self.logger.error(f"Error in admin reject withdrawal: {e}")
            await query.answer("❌ Error processing rejection")

    async def _handle_admin_user_details(self, query, context):
        """Handle admin request for detailed user information"""
        try:
            # Parse callback data: user_details:user_id:bot_id
            _, user_id, bot_id = query.data.split(":")
            user_id = int(user_id)
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            # This is the same as _handle_admin_user_profile - reuse that function
            await self._handle_admin_user_profile(query, context)
                
        except Exception as e:
            self.logger.error(f"Error in admin user details: {e}")
            await query.answer("❌ Error loading user details")

    async def _handle_admin_all_withdrawal_requests(self, query, context):
        """Handle admin request for all pending withdrawal requests"""
        try:
            # Parse callback data: all_withdrawal_requests:bot_id
            _, bot_id = query.data.split(":")
            
            # Verify this is from admin chat
            if str(query.message.chat_id) != settings.admin_chat_id:
                await query.answer("❌ Unauthorized")
                return
            
            async with get_async_db() as db:
                # Get all pending withdrawal requests for this bot
                result = await db.execute(
                    select(WithdrawalRequest).where(
                        WithdrawalRequest.bot_id == bot_id,
                        WithdrawalRequest.status == "pending"
                    ).order_by(WithdrawalRequest.requested_at.desc()).limit(10)
                )
                pending_withdrawals = result.scalars().all()
                
                if not pending_withdrawals:
                    await query.message.reply_text(
                        f"✅ **No Pending Withdrawals**\n\n"
                        f"🤖 **Bot:** {bot_id}\n"
                        f"📊 All withdrawal requests are processed!"
                    )
                    await query.answer("✅ No pending withdrawals")
                    return
                
                # Create pending withdrawals list
                total_amount = sum(w.amount for w in pending_withdrawals)
                pending_message = f"💰 **PENDING WITHDRAWAL REQUESTS**\n\n🤖 **Bot:** {bot_id}\n\n"
                
                for withdrawal in pending_withdrawals:
                    time_since = datetime.now(timezone.utc) - withdrawal.requested_at
                    hours_ago = int(time_since.total_seconds() / 3600)
                    pending_message += f"💰 #{withdrawal.req_id} - User {withdrawal.user_id} - ₹{withdrawal.amount} ({hours_ago}h ago)\n"
                
                pending_message += f"\n📊 **Total Pending:** {len(pending_withdrawals)} requests\n"
                pending_message += f"💵 **Total Amount:** ₹{total_amount}"
                
                # Send pending withdrawals list
                await query.message.reply_text(pending_message, parse_mode="Markdown")
                await query.answer(f"📋 {len(pending_withdrawals)} pending withdrawals")
                
        except Exception as e:
            self.logger.error(f"Error in admin all withdrawal requests: {e}")
            await query.answer("❌ Error loading withdrawal requests")

    async def _handle_show_wallet(self, query, context):
        """Handle wallet button - show user balance and transaction history"""
        try:
            user_id = query.from_user.id
            
            async with get_async_db() as db:
                # Get user data
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == self.config.bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data:
                    await query.edit_message_text("Please /start first.")
                    return
                
                # Get recent transactions/withdrawals for transaction history
                withdrawals_result = await db.execute(
                    select(WithdrawalRequest).where(
                        WithdrawalRequest.user_id == user_id,
                        WithdrawalRequest.bot_id == self.config.bot_id
                    ).order_by(WithdrawalRequest.requested_at.desc()).limit(5)
                )
                recent_withdrawals = withdrawals_result.scalars().all()
                
                # Calculate wallet info
                current_balance = user_data.total_earned
                total_referrals = user_data.referrals_count
                
                # Calculate total withdrawn (approved withdrawals)
                total_withdrawn = 0
                for withdrawal in recent_withdrawals:
                    if withdrawal.status == "approved":
                        total_withdrawn += withdrawal.amount
                
                # Determine wallet status
                if user_data.is_verified:
                    status = "✅ Verified"
                    status_color = "🟢"
                else:
                    status = "⚠️ Not Verified"
                    status_color = "🟡"
                
                # Create wallet message
                wallet_message = f"""💳 My Wallet - {self.config.username}

{status_color} Account Status: {status}

💰 Current Balance: ₹{current_balance}
👥 Total Referrals: {total_referrals}
📤 Total Withdrawn: ₹{total_withdrawn}

📊 Balance Breakdown:
• Total Earned: ₹{current_balance}
• Available for Withdrawal: ₹{current_balance if user_data.is_verified else 0}

💡 Balance Details:"""

                # Add balance explanation based on verification status
                if not user_data.is_verified:
                    wallet_message += f"""
🎊 You have ₹{current_balance} in your wallet!
⚠️ To unlock withdrawals: Pay ₹{settings.join_fee_inr} verification fee
🚀 After verification: Start referring friends for ₹180 each!
💡 Need ₹{settings.min_withdrawal_inr - current_balance} more to reach minimum withdrawal of ₹{settings.min_withdrawal_inr}"""
                else:
                    if current_balance >= settings.min_withdrawal_inr:
                        wallet_message += f"""
✅ Withdrawal Available: You can withdraw ₹{settings.min_withdrawal_inr}+ anytime!
💰 Earning: ₹180 per referral (first {settings.tier1_threshold})
🎯 Keep referring: Build your earnings!"""
                    else:
                        needed = settings.min_withdrawal_inr - current_balance
                        wallet_message += f"""
📈 Almost there: Need ₹{needed} more to withdraw
💰 Earning: ₹180 per referral
🎯 Keep referring: You're doing great!"""

                # Add recent transaction history if any
                if recent_withdrawals:
                    wallet_message += f"\n\n📋 Recent Withdrawals:"
                    for withdrawal in recent_withdrawals[:3]:  # Show last 3
                        status_emoji = "✅" if withdrawal.status == "approved" else "⏳" if withdrawal.status == "pending" else "❌"
                        date_str = withdrawal.requested_at.strftime('%d %b')
                        wallet_message += f"\n{status_emoji} ₹{withdrawal.amount} - {date_str} ({withdrawal.status})"

                # Create keyboard
                keyboard = []
                if user_data.is_verified:
                    if current_balance >= settings.min_withdrawal_inr:
                        keyboard.append([InlineKeyboardButton("💰 Withdraw Money", callback_data="start_withdrawal")])
                    keyboard.append([InlineKeyboardButton("📊 My Stats", callback_data="show_referrals")])
                    keyboard.append([InlineKeyboardButton("🔗 Get Referral Link", callback_data="get_referral_link")])
                else:
                    keyboard.append([InlineKeyboardButton(f"✅ Get Verified - Pay ₹{settings.join_fee_inr}", callback_data="verify_payment")])
                    keyboard.append([InlineKeyboardButton("ℹ️ How it Works", callback_data="how_it_works")])

                keyboard.append([InlineKeyboardButton("🔄 Refresh Wallet", callback_data="show_wallet")])
                keyboard.append([InlineKeyboardButton("🔙 Back to Main", callback_data="back_to_main")])

                await query.edit_message_text(
                    wallet_message,
                    reply_markup=InlineKeyboardMarkup(keyboard)
                )
                
        except Exception as e:
            self.logger.error(f"Error in wallet handler: {e}")
            await query.edit_message_text(
                f"💳 **My Wallet - {self.config.username}**\n\n"
                f"⚠️ Unable to load wallet details.\n"
                f"Please try again or contact support.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Try Again", callback_data="show_wallet")
                ]])
            )

    async def _auto_approve_payment(self, user_id: int, bot_id: str, context):
        """Auto-approve payment when no admin is configured"""
        try:
            async with get_async_db() as db:
                result = await db.execute(
                    select(User).where(
                        User.user_id == user_id,
                        User.bot_id == bot_id
                    )
                )
                user_data = result.scalar_one_or_none()
                
                if not user_data or user_data.is_verified:
                    return
                
                # Auto-approve the payment
                user_data.is_verified = True
                
                # Update referrer if exists
                if user_data.referred_by:
                    referrer_result = await db.execute(
                        select(User).where(
                            User.user_id == user_data.referred_by,
                            User.bot_id == bot_id
                        )
                    )
                    referrer = referrer_result.scalar_one_or_none()
                    
                    if referrer:
                        # Calculate reward
                        old_count = referrer.referrals_count
                        new_count = old_count + 1
                        if new_count == 1:
                            reward = settings.tier1_reward_inr  # ₹110 for first referral only
                        elif new_count <= settings.tier1_threshold:
                            reward = 180  # ₹180 for referrals 2-15
                        else:
                            reward = settings.tier2_reward_inr  # ₹190 for 16th+
                        
                        # Update referrer
                        referrer.referrals_count = new_count
                        referrer.total_earned += reward
                        
                        # Notify referrer
                        try:
                            await context.bot.send_message(
                                chat_id=referrer.user_id,
                                text=f"🎉 **Referral Verified!**\n\n"
                                     f"💰 **Earned:** ₹{reward}\n"
                                     f"👥 **Total Referrals:** {new_count}\n"
                                     f"💵 **Total Earned:** ₹{referrer.total_earned}\n\n"
                                     f"🚀 Keep sharing your link to earn more!"
                            )
                        except Exception:
                            pass
                
                await db.commit()
                
                # Notify user of approval
                try:
                    referral_link = f"https://t.me/{self.config.username}?start={user_id}"
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=f"🎉 **Payment Verified Successfully!**\n\n"
                             f"✅ Your payment has been automatically verified.\n"
                             f"🚀 You can now start earning money through referrals!\n\n"
                             f"🔗 **Your Referral Link:**\n"
                             f"`{referral_link}`\n\n"
                             f"💰 **Start Earning:**\n"
                             f"• Share this link with friends\n"
                             f"• Earn ₹180 per referral\n\n"
                             f"🎯 **Minimum withdrawal:** ₹{settings.min_withdrawal_inr}",
                        parse_mode="Markdown"
                    )
                except Exception as notify_error:
                    self.logger.error(f"Failed to notify user {user_id}: {notify_error}")
                
                self.logger.info(f"Auto-approved payment for user {user_id} on bot {bot_id}")
                
        except Exception as e:
            self.logger.error(f"Error in auto-approve payment: {e}")

    async def stop_bot(self) -> None:
        """Stop the bot instance"""
        if self.application:
            try:
                await self.application.stop()
                self.logger.info(f"Bot {self.config.bot_id} stopped")
            except Exception as e:
                self.logger.error(f"Error stopping bot {self.config.bot_id}: {e}")
        else:
            self.logger.warning(f"Bot {self.config.bot_id} application not running")
