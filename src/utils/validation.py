import re
import logging
from typing import Optional
from fastapi import HTTPException

logger = logging.getLogger(__name__)

def validate_telegram_user_id(user_id: int) -> bool:
    """Validate Telegram user ID format"""
    # Telegram user IDs are positive integers, typically 9-10 digits
    return isinstance(user_id, int) and 1 <= user_id <= 9999999999

def validate_upi_id(upi_id: str) -> bool:
    """Validate UPI ID format"""
    if not upi_id or len(upi_id) > 255:
        return False
    
    # UPI ID pattern: username@bank_identifier
    upi_pattern = r'^[a-zA-Z0-9._-]+@[a-zA-Z0-9._-]+$'
    return bool(re.match(upi_pattern, upi_id))

def validate_amount(amount: int, min_amount: int = 0, max_amount: int = 100000) -> bool:
    """Validate amount is within reasonable bounds"""
    return isinstance(amount, int) and min_amount <= amount <= max_amount

def validate_file_type(content_type: str, allowed_types: list = None) -> bool:
    """Validate file content type"""
    if allowed_types is None:
        allowed_types = ['image/jpeg', 'image/png', 'image/webp', 'image/bmp']
    
    return content_type in allowed_types

def validate_file_size(size: int, max_size_mb: int = 10) -> bool:
    """Validate file size"""
    max_size_bytes = max_size_mb * 1024 * 1024
    return 0 < size <= max_size_bytes

def sanitize_string(value: str, max_length: int = 255) -> str:
    """Sanitize string input"""
    if not value:
        return ""
    
    # Remove potentially dangerous characters
    sanitized = re.sub(r'[<>"\';\\]', '', value)
    return sanitized[:max_length].strip()

def validate_pagination(page: int, limit: int, max_limit: int = 100) -> tuple[int, int]:
    """Validate and sanitize pagination parameters"""
    page = max(1, page)
    limit = min(max(1, limit), max_limit)
    return page, limit

class ValidationError(Exception):
    """Custom validation error"""
    pass

def validate_withdrawal_request(user_id: int, amount: int, upi_id: str, min_withdrawal: int):
    """Comprehensive withdrawal request validation"""
    errors = []
    
    if not validate_telegram_user_id(user_id):
        errors.append("Invalid user ID format")
    
    if not validate_amount(amount, min_withdrawal, 100000):
        errors.append(f"Amount must be between ₹{min_withdrawal} and ₹100,000")
    
    if not validate_upi_id(upi_id):
        errors.append("Invalid UPI ID format")
    
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))

def validate_verification_upload(user_id: int, content_type: str, file_size: int):
    """Comprehensive verification upload validation"""
    errors = []
    
    if not validate_telegram_user_id(user_id):
        errors.append("Invalid user ID format")
    
    if not validate_file_type(content_type):
        errors.append("File must be an image (JPEG, PNG, WebP, or BMP)")
    
    if not validate_file_size(file_size):
        errors.append("File size must be less than 10MB")
    
    if errors:
        raise HTTPException(status_code=400, detail="; ".join(errors))
