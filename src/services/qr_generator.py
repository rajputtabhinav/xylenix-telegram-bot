import qrcode
from qrcode.image.styledpil import StyledPilImage
from qrcode.image.styles.moduledrawers import RoundedModuleDrawer
from qrcode.image.styles.colormasks import SolidFillColorMask
from io import BytesIO

def generate_upi_qr(upi_id: str, amount: int, payee_name: str) -> bytes:
    """
    Generates a UPI QR code with a pre-filled amount.
    """
    # UPI URL format: upi://pay?pa=<upi_id>&pn=<payee_name>&am=<amount>&cu=INR
    upi_url = f"upi://pay?pa={upi_id}&pn={payee_name}&am={amount}&cu=INR"
    
    # Generate QR code
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=15,
        border=2,
    )
    qr.add_data(upi_url)
    qr.make(fit=True)
    
    # Create a styled image
    img = qr.make_image(
        image_factory=StyledPilImage,
        module_drawer=RoundedModuleDrawer(),
        color_mask=SolidFillColorMask(front_color=(6, 25, 52))
    )
    
    # Save image to a byte stream
    img_buffer = BytesIO()
    img.save(img_buffer, format="PNG")
    img_buffer.seek(0)
    
    return img_buffer.getvalue()
