import base64
import logging
from dataclasses import dataclass
from typing import Optional, Dict, Any
from anthropic import Anthropic
from src.config import settings
import re
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

@dataclass
class VerificationResult:
	is_valid: bool
	amount_inr: Optional[int] = None
	payer_upi: Optional[str] = None
	receiver_upi: Optional[str] = None
	timestamp_str: Optional[str] = None
	notes: Optional[str] = None
	confidence_score: Optional[float] = None
	raw_response: Optional[str] = None

class PaymentVerificationService:
	def __init__(self):
		self.anthropic_client = None
		if settings.anthropic_api_key:
			try:
				self.anthropic_client = Anthropic(api_key=settings.anthropic_api_key)
			except Exception as e:
				logger.error(f"Failed to initialize Anthropic client: {e}")

	def verify_payment_screenshot(self, image_bytes: bytes) -> VerificationResult:
		"""Verify payment screenshot using Anthropic Vision API"""
		if not self.anthropic_client:
			return VerificationResult(
				is_valid=False,
				notes="Anthropic API not configured",
				confidence_score=0.0
			)

		try:
			# Convert image to base64
			image_base64 = base64.b64encode(image_bytes).decode('utf-8')
			
			# Create the verification prompt
			prompt = self._create_verification_prompt()
			
			# Call Anthropic Vision API
			response = self.anthropic_client.messages.create(
				model="claude-3-sonnet-20240229",
				max_tokens=1000,
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
								"text": prompt
							}
						]
					}
				]
			)
			
			# Parse the response
			return self._parse_verification_response(response.content[0].text)
			
		except Exception as e:
			logger.error(f"Payment verification failed: {e}")
			return VerificationResult(
				is_valid=False,
				notes=f"Verification error: {str(e)}",
				confidence_score=0.0
			)

	def _create_verification_prompt(self) -> str:
		"""Create the verification prompt for Anthropic"""
		upi_ids_str = ", ".join(settings.receiver_upi_ids)
		return f"""
Analyze this payment screenshot and extract the following information:

1. Transaction amount (look for ₹ symbol)
2. Sender/Payer UPI ID
3. Receiver UPI ID
4. Transaction date and time
5. Transaction status (success/failed)
6. Any transaction reference ID

Expected payment details:
- Amount: ₹{settings.join_fee_inr}
- Expected receiver UPI: One of [{upi_ids_str}]

Respond in this exact JSON format:
{{
  "amount": <extracted_amount_as_number>,
  "payer_upi": "<sender_upi_id>",
  "receiver_upi": "<receiver_upi_id>",
  "timestamp": "<transaction_date_time>",
  "status": "<success_or_failed>",
  "reference_id": "<transaction_reference>",
  "confidence": <confidence_score_0_to_1>,
  "notes": "<any_observations_or_issues>"
}}

Be very careful to extract exact values. If any information is unclear or missing, set confidence to a lower value and mention it in notes.
"""

	def _parse_verification_response(self, response_text: str) -> VerificationResult:
		"""Parse Anthropic response and create verification result"""
		try:
			# Extract JSON from response
			json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
			if not json_match:
				return VerificationResult(
					is_valid=False,
					notes="Could not parse AI response",
					raw_response=response_text
				)
			
			import json
			data = json.loads(json_match.group())
			
			# Extract fields
			amount = data.get('amount', 0)
			payer_upi = data.get('payer_upi', '')
			receiver_upi = data.get('receiver_upi', '')
			timestamp = data.get('timestamp', '')
			status = data.get('status', '').lower()
			confidence = data.get('confidence', 0.0)
			notes = data.get('notes', '')
			
			# Validate payment
			is_valid = self._validate_payment(
				amount, receiver_upi, status, confidence
			)
			
			return VerificationResult(
				is_valid=is_valid,
				amount_inr=amount,
				payer_upi=payer_upi,
				receiver_upi=receiver_upi,
				timestamp_str=timestamp,
				notes=notes,
				confidence_score=confidence,
				raw_response=response_text
			)
			
		except Exception as e:
			logger.error(f"Failed to parse verification response: {e}")
			return VerificationResult(
				is_valid=False,
				notes=f"Response parsing error: {str(e)}",
				raw_response=response_text
			)

	def _validate_payment(self, amount: int, receiver_upi: str, status: str, confidence: float) -> bool:
		"""Validate extracted payment details"""
		# Check amount
		if amount != settings.join_fee_inr:
			return False
		
		# Check if receiver UPI is in the valid list
		if receiver_upi.lower() not in [upi.lower() for upi in settings.receiver_upi_ids]:
			return False
		
		# Check transaction status
		if status not in ['success', 'successful', 'completed']:
			return False
		
		# Check confidence threshold
		if confidence < 0.7:  # 70% confidence threshold
			return False
		
		return True

# Global verification service instance
verification_service = PaymentVerificationService()

def verify_payment_screenshot(image_bytes: bytes) -> VerificationResult:
	"""Convenience function for payment verification"""
	return verification_service.verify_payment_screenshot(image_bytes)
