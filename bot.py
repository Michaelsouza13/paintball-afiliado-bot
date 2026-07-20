import json
import logging
import os
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional
from urllib.parse import urlencode, urlparse, urlunparse

import requests

from scraper import MercadoLivreScraper, Offer
from shopee_scraper import ShopeeScraper
from storage import load_sent_ids, save_sent_ids
from telegram_sender import TelegramSender
from utils import format_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


def make_affiliate_url(clean_url: str, affiliate_tag: str) -> str:
    if not affiliate_tag:
        return clean_url

    parsed = urlparse(clean_url)
    params = {}

    if affiliate_tag.startswith("matt:"):
        parts = affiliate_tag.split(":")
        if len(parts) >= 3:
            params["matt_word"] = parts[1]
            params["matt_tool"] = parts[2]
    else:
        params["tag"] = affiliate_tag

    existing = parsed.query
    new_query = urlencode(params)
    query = f"{existing}&{new_query}" if existing else new_query

    return urlunparse(parsed._replace(query=query))


def _retry_with_backoff(func, max_retries=3, base_delay=10, *args, **kwargs):
    for attempt in range(max_retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            delay = base_delay * (2 ** attempt)
            logger.warning("Tentativa %d/%d falhou: %s. Retry em %ds...",
                           attempt + 1, max_retries, e, delay)
            time.sleep(delay)
    return None


def _send_error_alert(error_msg: str):
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        try:
            sender = TelegramSender(bot_token)
            sender.send_message(
                chat_id,
                f"\U0001F6A8 *Paintball Bot \u2014 Bot falhou*\n\nErro: `{error_msg}`",
            )
        except Exception as e:
            logger.error("Falha ao enviar alerta de erro no Telegram: %s", e)


def _interleave_offers(offers: list) -> list:
    groups = defaultdict(list)
    for o in offers:
        prefix = o.product_id[:2]
        groups[prefix].append(o)
    result = []
    while any(groups.values()):
        for prefix in ["ML", "SH"]:
            if groups[prefix]:
                result.append(groups[prefix].pop(0))
    return result


def _balance_offers(all_offers: list, max_offers: int) -> list:
    if not all_offers or max_offers <= 0:
        return []

    quota = max(max_offers // 2, 1)

    groups = defaultdict(list)
    for o in all_offers:
        groups[o.product_id[:2]].append(o)

    result = []

    for i in range(quota):
        for prefix in ["ML", "SH"]:
            pool = groups.get(prefix, [])
            if i < len(pool) and len(result) < max_offers:
                result.append(pool[i])

    remaining = _interleave_offers(
        [o for prefix in ["ML", "SH"]
         for o in groups.get(prefix, [])[quota:]]
    )
    for o in remaining:
        if len(result) >= max_offers:
            break
        result.append(o)

    logger.info("Balanceamento: quota=%d, total=%d, ML=%d SH=%d",
                quota, len(result),
                sum(1 for o in result if o.product_id[:2] == "ML"),
                sum(1 for o in result if o.product_id[:2] == "SH"))
    return result


def _scrape_ml_task(category, pages, ml_max_pages, ml_target, promotion_type, seen):
    categories = [c.strip() for c in category.split(",")] if category else [""]
    promo_types = [""] if promotion_type else ["", "lightning"]
    offers = []
    for ci, cat in enumerate(categories):
        for ptype in promo_types:
            cat_label = cat if cat else "todas"
            pt_label = f"promotion_type={ptype}" if ptype else "todas"
            logger.info("ML [%s, %s]...", cat_label, pt_label)
            scraper = MercadoLivreScraper(category=cat, pages=pages, promotion_type=ptype)
            try:
                result = _retry_with_backoff(
                    scraper.scrape, max_retries=2, base_delay=5,
                    max_offers=ml_target, seen_ids=seen,
                    target_new=ml_target, max_pages=ml_max_pages,
                ) or []
            except Exception as e:
                logger.error("Erro ML [%s, %s]: %s", cat_label, pt_label, e)
                continue
            for o in result:
                if o.id not in seen:
                    seen.add(o.id)
                    offers.append(o)
            logger.info("  -> %d ofertas ML (%s, %s)", len(result), cat_label, pt_label)
        if ci < len(categories) - 1:
            time.sleep(2)
    return offers


def _scrape_sh_task(app_id, app_secret, max_offers, keywords, seen):
    if not app_id or not app_secret:
        return []
    logger.info("Shopee...")
    scraper = ShopeeScraper(
        app_id=app_id, app_secret=app_secret,
        max_offers=max_offers, keywords=keywords,
    )
    try:
        result = _retry_with_backoff(scraper.scrape, max_retries=3, base_delay=10) or []
    except Exception as e:
        logger.error("Erro Shopee: %s", e)
        return []
    new = []
    for o in result:
        if o.id not in seen:
            seen.add(o.id)
            new.append(o)
    logger.info("  -> %d ofertas Shopee (%d novas)", len(result), len(new))
    return new


def main():
    for var in ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]:
        if var not in os.environ:
            logger.error("Missing required env var: %s", var)
            sys.exit(1)

    bot_token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    affiliate_tag = os.environ.get("AFFILIATE_TAG", "")
    category = os.environ.get("ML_CATEGORY", "")
    pages = int(os.environ.get("ML_PAGES", "3"))
    ml_max_pages = int(os.environ.get("ML_MAX_PAGES", "20"))
    ml_max_offers = int(os.environ.get("ML_MAX_OFFERS", "0")) or 0
    max_offers = int(os.environ.get("MAX_OFFERS_PER_RUN", "10"))
    promotion_type = os.environ.get("ML_PROMOTION_TYPE", "")
    send_delay = int(os.environ.get("SEND_DELAY_SECONDS", "60"))

    sh_app_id = os.environ.get("SHOPEE_APP_ID", "")
    sh_app_secret = os.environ.get("SHOPEE_APP_SECRET", "")
    sh_max_offers = int(os.environ.get("SHOPEE_MAX_OFFERS", "5"))
    sh_keywords = os.environ.get("SHOPEE_KEYWORDS",
        "paintball,marcador paintball,mascara paintball,co2 paintball,"
        "bola paintball,equipamento paintball,kit paintball,"
        "carregador paintball,cilindro co2,calça paintball,luva paintball,"
        "colete paintball,gatilho paintball,aguia paintball,spyder paintball,"
        "tippmann paintball,dye paintball,planet eclipse")

    ml_target = ml_max_offers if ml_max_offers > 0 else max_offers
    logger.info("Limite de coleta: ML=%d SH=%d (max_offers=%d)",
                ml_target, sh_max_offers, max_offers)

    sender_tg = TelegramSender(bot_token)

    all_offers = []
    sent_ids = load_sent_ids()
    sent_ids_set = set(sent_ids.keys())

    logger.info("Iniciando scraping paralelo (ML, SH)...")
    t0 = time.time()

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                _scrape_ml_task, category, pages, ml_max_pages, ml_target, promotion_type, sent_ids_set.copy()
            ): "ML",
            executor.submit(
                _scrape_sh_task, sh_app_id, sh_app_secret, sh_max_offers, sh_keywords, sent_ids_set.copy()
            ): "SH",
        }

        for future in as_completed(futures):
            name = futures[future]
            try:
                result = future.result()
                for o in result:
                    if o.id not in sent_ids_set:
                        sent_ids_set.add(o.id)
                        all_offers.append(o)
                logger.info("Thread %s concluida: %d ofertas novas", name, len(result))
            except Exception as e:
                logger.error("Thread %s falhou: %s", name, e)

    elapsed = time.time() - t0
    logger.info("Scraping paralelo concluido em %.1fs: %d ofertas", elapsed, len(all_offers))

    offers_found = len(all_offers)
    logger.info("Total coletado: %d ofertas", offers_found)

    if not all_offers:
        logger.info("Nenhuma oferta encontrada")
        return

    offers = _balance_offers(all_offers, max_offers)

    new_offers = [o for o in offers if o.id not in sent_ids]

    if not new_offers:
        logger.info("Nenhuma oferta nova para enviar")
        return

    new_offers.sort(key=lambda o: o.score, reverse=True)

    logger.info("Enviando %d ofertas (delay %ds entre cada)", len(new_offers), send_delay)

    total_sent = 0
    for i, offer in enumerate(new_offers):
        if i > 0:
            logger.info("Aguardando %d segundos...", send_delay)
            time.sleep(send_delay)

        try:
            if offer.product_id.startswith("ML"):
                offer.url = make_affiliate_url(offer.clean_url, affiliate_tag)
            else:
                offer.url = offer.clean_url
        except Exception as e:
            logger.error("Falha ao gerar URL para '%s': %s", offer.title[:40], e)
            continue

        try:
            sender_tg.send_offer(chat_id, offer)
            sent_ids[offer.id] = time.time()
            total_sent += 1
            src = offer.product_id[:2] if len(offer.product_id) >= 2 else "??"
            logger.info("[%s] Telegram: %s", src, offer.title[:60])
        except Exception as e:
            logger.error("Falha no Telegram: %s", e)

    if total_sent > 0:
        save_sent_ids(sent_ids)

    logger.info("Concluido. %d oferta(s) enviada(s)", total_sent)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        logger.critical("Bot falhou: %s", e, exc_info=True)
        _send_error_alert(str(e))
        sys.exit(1)
