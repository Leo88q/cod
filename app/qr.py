"""QR-коды (SVG, без внешних зависимостей além do пакета qrcode)."""
import io

import qrcode
import qrcode.image.svg


def qr_svg(data: str, box_size: int = 8) -> str:
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=box_size)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode("utf-8")
