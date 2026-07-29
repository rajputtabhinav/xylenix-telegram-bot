import logging
from typing import Optional
import httpx
from src.config import settings

logger = logging.getLogger(__name__)

class AIChatService:
    """AI Chat service using OpenAI API for user queries"""
    
    def __init__(self):
        self.api_key = settings.openai_api_key
        self.base_url = "https://api.openai.com/v1/chat/completions"
        
        # Bot context for AI responses
        self.bot_context = f"""You are Xylenix Bot's AI assistant. You help users with questions about the Xylenix referral system.

XYLENIX BOT FEATURES:
- Referral earning system where users earn money by referring friends
- Join with a one-time fee of ₹{settings.join_fee_inr}
- Earn ₹180 per referral for first {settings.tier1_threshold} referrals (Tier 1)
- Earn ₹{settings.tier2_reward_inr} per referral after {settings.tier1_threshold} referrals (Tier 2)
- Minimum withdrawal amount: ₹{settings.min_withdrawal_inr}
- AI-powered payment verification using screenshot uploads
- UPI-based payments and withdrawals
- Admin approval system for withdrawals

HOW IT WORKS:
1. User pays ₹{settings.join_fee_inr} joining fee
2. Upload payment screenshot for AI verification
3. Get unique referral link after verification
4. Share referral link to earn money
5. Withdraw earnings (minimum ₹{settings.min_withdrawal_inr})

RESPONSE GUIDELINES:
- Keep responses friendly, helpful, and encouraging
- Use emojis to make responses engaging
- Always mention specific amounts (₹{settings.join_fee_inr}, ₹180, etc.)
- Encourage users to start earning and referring friends
- If asked about technical issues, suggest contacting support
- Always stay positive about earning potential
- Don't provide information about other bots or competitors

IMPORTANT: Always respond in a helpful, encouraging tone that motivates users to use the bot and earn money!"""

    async def get_ai_response(self, user_message: str, user_context: Optional[dict] = None) -> str:
        """Get AI response for user message"""
        try:
            if not self.api_key:
                return self._get_fallback_response(user_message)
            
            # Add user context if available
            context_info = ""
            if user_context:
                if user_context.get('is_verified'):
                    context_info += f"User is verified and has earned ₹{user_context.get('total_earned', 0)} with {user_context.get('referrals_count', 0)} referrals. "
                else:
                    context_info += "User is not yet verified. "
            
            # Create the prompt
            prompt = f"{self.bot_context}\n\nUSER CONTEXT: {context_info}\nUSER MESSAGE: {user_message}\n\nProvide a helpful response about Xylenix bot features:"
            
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    self.base_url,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {self.api_key}"
                    },
                    json={
                        "model": "gpt-3.5-turbo",  # Using faster, cheaper model
                        "max_tokens": 300,  # Keep responses concise
                        "temperature": 0.7,  # Balanced creativity
                        "messages": [
                            {
                                "role": "system",
                                "content": self.bot_context
                            },
                            {
                                "role": "user", 
                                "content": f"USER CONTEXT: {context_info}\nUSER MESSAGE: {user_message}"
                            }
                        ]
                    }
                )
                
                if response.status_code == 200:
                    result = response.json()
                    ai_response = result["choices"][0]["message"]["content"].strip()
                    
                    # Ensure response isn't too long for Telegram
                    if len(ai_response) > 4000:
                        ai_response = ai_response[:3900] + "..."
                    
                    return ai_response
                else:
                    logger.error(f"OpenAI API error: {response.status_code} - {response.text}")
                    return self._get_fallback_response(user_message)
                    
        except Exception as e:
            logger.error(f"Error getting AI response: {e}")
            return self._get_fallback_response(user_message)
    
    def _get_fallback_response(self, user_message: str) -> str:
        """Enhanced fallback responses when AI is unavailable"""
        message_lower = user_message.lower()
        
        # Greeting patterns
        if any(word in message_lower for word in ['hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening']):
            return f"""👋 **Hello! Welcome to Xylenix!**

🚀 **Ready to start earning?** Here's how:

1️⃣ Pay ₹{settings.join_fee_inr} joining fee
2️⃣ Get verified instantly  
3️⃣ Share your referral link
4️⃣ Earn ₹180 per friend who joins!

💰 **Ask me anything about earning with Xylenix!**"""

        # How it works / earning questions
        elif any(word in message_lower for word in ['how', 'work', 'earn', 'money', 'income', 'profit']):
            return f"""🚀 **How Xylenix Works:**

💳 **Step 1:** Pay ₹{settings.join_fee_inr} one-time joining fee
📸 **Step 2:** Upload payment screenshot for instant verification  
🔗 **Step 3:** Get your unique referral link
👥 **Step 4:** Share with friends and earn ₹180 per referral!

🏆 **Bonus:** After {settings.tier1_threshold} referrals, earn ₹{settings.tier2_reward_inr} each!

💡 **Real earnings, real payments!** Use buttons below to start."""

        # Withdrawal questions
        elif any(word in message_lower for word in ['withdraw', 'withdrawal', 'payout', 'cash out']):
            return f"""💰 **Withdrawal Process:**

✅ **Minimum Amount:** ₹{settings.min_withdrawal_inr}
✅ **Payment Method:** UPI (Direct to your account)
✅ **Processing Time:** Within 24 hours
✅ **Steps:** Request → Upload UPI QR → Admin approval → Money sent!

📱 **No fees, no delays!** Your earnings go straight to your UPI."""

        # Referral system questions
        elif any(word in message_lower for word in ['referral', 'refer', 'friend', 'share', 'link']):
            return f"""👥 **Referral System Explained:**

🎖️ **Tier 1 (First {settings.tier1_threshold} referrals):** ₹180 each
🏆 **Tier 2 (After {settings.tier1_threshold} referrals):** ₹{settings.tier2_reward_inr} each

📱 **Best Places to Share:**
• WhatsApp groups and status
• Instagram stories and posts  
• Facebook groups and timeline
• Twitter and LinkedIn

💡 **Pro tip:** More referrals = Higher earnings per referral!"""

        # Legitimacy/trust questions
        elif any(word in message_lower for word in ['real', 'fake', 'scam', 'legit', 'trust', 'genuine', 'safe']):
            return f"""✅ **Xylenix is 100% Legitimate!**

🔐 **Why Trust Us:**
• AI-powered payment verification
• Transparent earning structure
• Real UPI payments to your account
• No hidden fees or charges
• Instant verification process

💰 **Proof:** Thousands of users already earning!
📱 **Try it:** Pay ₹{settings.join_fee_inr}, get verified, start earning!

🚀 **Join the earning community today!**"""

        # Payment/joining questions
        elif any(word in message_lower for word in ['pay', 'payment', 'join', 'fee', 'cost', 'price']):
            return f"""💳 **Joining Details:**

💰 **One-time Fee:** ₹{settings.join_fee_inr} only
🎯 **What You Get:** 
• Instant verification
• Your unique referral link
• Start earning immediately
• ₹180 per successful referral

📸 **Process:** Pay → Upload screenshot → Get verified → Start earning!

🚀 **ROI:** Just 1 referral covers your joining fee!"""

        # Help/support questions
        elif any(word in message_lower for word in ['help', 'support', 'problem', 'issue', 'stuck']):
            return """🆘 **Need Help? I'm Here!**

❓ **Common Questions:**
• **Payment rejected?** → Ensure clear, complete screenshot
• **Referral not counted?** → Friend must complete full verification
• **Withdrawal delayed?** → Admin processes within 24 hours

💬 **Still confused?** Ask me specific questions like:
• "How do I get more referrals?"
• "When will I get paid?"
• "Is this really profitable?"

🤝 **I'm here to help you succeed!**"""

        # Personal introductions
        elif any(word in message_lower for word in ['i am', 'my name', 'myself', 'abhinav', 'who are you']):
            return f"""👋 **Nice to meet you!**

🤖 **I'm Xylenix Assistant** - here to help you earn money through referrals!

💰 **Here's what I can help you with:**
• Explain how the earning system works
• Guide you through the joining process
• Answer questions about payments and withdrawals
• Share tips to get more referrals

🚀 **Ready to start earning ₹180 per referral?**"""

        # Default friendly response
        else:
            return f"""🤖 **I'm here to help you earn money!**

💰 **Quick Facts:**
• Join for just ₹{settings.join_fee_inr}
• Earn ₹180 per referral
• Minimum withdrawal ₹{settings.min_withdrawal_inr}
• UPI payments within 24 hours

❓ **Ask me anything like:**
• "How does this work?"
• "Is this really profitable?"
• "How do I get more referrals?"

🚀 **Let's get you earning!**"""

# Global AI chat service instance
ai_chat_service = AIChatService()
