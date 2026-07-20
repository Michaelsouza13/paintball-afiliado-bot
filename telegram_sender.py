import logging
import random

import requests

from utils import format_price

logger = logging.getLogger(__name__)

CTA_PHRASES = [
    "Aproveite antes que acabe!",
    "Corre que \u00E9 oportunidade!",
    "Oferta por tempo limitado!",
    "Garanta a sua agora!",
    "N\u00E3o perca essa chance!",
]


class TelegramSender:
    API_BASE = "https://api.telegram.org"

    def __init__(self, bot_token: str):
        self.bot_token = bot_token
        self.api_url = f"{self.API_BASE}/bot{bot_token}"

    def send_message(self, chat_id: str, text: str, parse_mode: str = "HTML") -> bool:
        resp = requests.post(
            f"{self.api_url}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": False,
            },
            timeout=15,
        )
        if not resp.ok:
            logger.error("Telegram API error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return True

    def send_photo(self, chat_id: str, photo_url: str, caption: str, parse_mode: str = "HTML") -> bool:
        resp = requests.post(
            f"{self.api_url}/sendPhoto",
            json={
                "chat_id": chat_id,
                "photo": photo_url,
                "caption": caption,
                "parse_mode": parse_mode,
            },
            timeout=15,
        )
        if not resp.ok:
            logger.error("Telegram sendPhoto error %s: %s", resp.status_code, resp.text)
            resp.raise_for_status()
        return True

    def send_offer(self, chat_id: str, offer) -> bool:
        message = self._format_offer_message(offer)
        if offer.image_url:
            try:
                return self.send_photo(chat_id, offer.image_url, message)
            except Exception as e:
                logger.warning("Falha ao enviar foto, enviando so texto: %s", e)
        return self.send_message(chat_id, message)

    def _format_offer_message(self, offer) -> str:
        title = offer.title.strip()
        current = format_price(offer.current_price)
        old = format_price(offer.old_price) if offer.old_price else ""
        discount = offer.discount_label.strip() if offer.discount_label else ""
        url = offer.url.strip()

        platform = offer.source.upper()
        lines = [
            f"\U0001F525 <b>PROMO\u00E7\u00C3O {platform}</b> \U0001F525",
            "",
            f"\U0001F4CC <b>{title}</b>",
        ]

        if old:
            lines.append(f"\U0001F4B0 De: <s>{old}</s>")
        lines.append(f"\U0001F525 Por: <b>{current}</b>")

        if discount:
            lines.append(f"\U0001F3AF {discount}")

        if offer.installments_qty > 1 and offer.installment_value > 0:
            iv = format_price(offer.installment_value)
            lines.append(f"\U0001F4B3 {offer.installments_qty}x de {iv} sem juros")

        if offer.promo_code:
            lines.append(f"\U0001F39F <b>Cupom:</b> {offer.promo_code}")
            if offer.promo_value:
                lines.append(f"\U0001F4CB {offer.promo_value}")
        elif offer.coupon_label:
            lines.append(f"\U0001F39F {offer.coupon_label}")

        if offer.has_full_shipping:
            lines.append("\U0001F69A <b>Frete Gr\u00E1tis FULL</b> - Receba amanh\u00E3!")
        elif offer.has_free_shipping:
            lines.append("\U0001F69A Frete Gr\u00E1tis")

        lines.extend([
            "",
            f"\U0001F6D2 <a href='{url}'>Ver Oferta</a>",
            "",
            f"\U0001F4E2 {random.choice(CTA_PHRASES)}",
        ])

        return "\n".join(lines)
