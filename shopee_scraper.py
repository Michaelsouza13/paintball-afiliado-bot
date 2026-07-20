import hashlib
import json
import logging
import time
from typing import List, Optional

import requests

from scraper import Offer

logger = logging.getLogger(__name__)

API_URL = "https://open-api.affiliate.shopee.com.br/graphql"


class ShopeeScraper:
    def __init__(self, app_id: str, app_secret: str,
                 max_offers: int = 5, keywords: str = "",
                 timeout: int = 30):
        self.app_id = app_id
        self.app_secret = app_secret
        self.max_offers = max_offers
        self.keywords = keywords
        self.timeout = timeout

    def _sign(self, timestamp: str, payload: str) -> str:
        raw = self.app_id + timestamp + payload + self.app_secret
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _call(self, query: str, variables: dict) -> Optional[dict]:
        payload_body = json.dumps({"query": query, "variables": variables}, ensure_ascii=False, separators=(",", ":"))
        timestamp = str(int(time.time()))
        signature = self._sign(timestamp, payload_body)
        headers = {
            "Authorization": f"SHA256 Credential={self.app_id}, Timestamp={timestamp}, Signature={signature}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(API_URL, data=payload_body, headers=headers, timeout=self.timeout)
            if not resp.ok:
                msg = f"Shopee API error {resp.status_code}: {resp.text[:300]}"
                logger.warning(msg)
                raise RuntimeError(msg)
            j = resp.json()
            if "errors" in j:
                msg = f"Shopee GraphQL: {j['errors']}"
                logger.warning(msg)
                raise RuntimeError(msg)
            return j
        except Exception as e:
            logger.error("Shopee API exception: %s", e)
            return None

    def scrape(self) -> List[Offer]:
        if self.max_offers <= 0:
            return []

        keywords = [k.strip() for k in self.keywords.split(",") if k.strip()]
        if not keywords:
            keywords = ["promocao"]

        all_products = []
        seen_ids = set()

        for kw in keywords:
            if len(all_products) >= self.max_offers:
                break
            logger.info("Shopee: buscando '%s'...", kw)
            query = """
            query($keyword: String!, $limit: Int, $page: Int) {
                productOfferV2(keyword: $keyword, limit: $limit, page: $page) {
                    nodes {
                        itemId
                        productName
                        productLink
                        offerLink
                        imageUrl
                        priceMin
                        priceMax
                        priceDiscountRate
                        commissionRate
                        shopId
                    }
                    pageInfo { page limit hasNextPage }
                }
            }
            """
            variables = {
                "keyword": kw,
                "limit": min(self.max_offers * 2, 50),
                "page": 1,
            }
            data = self._call(query, variables)
            if not data:
                continue

            try:
                nodes = data["data"]["productOfferV2"]["nodes"]
            except (KeyError, TypeError):
                logger.warning("Shopee: resposta inesperada para '%s'", kw)
                continue

            for node in nodes:
                if len(all_products) >= self.max_offers:
                    break
                try:
                    item_id = str(node.get("itemId", "") or "")
                    if not item_id:
                        continue

                    full_id = f"SH{item_id}"
                    if full_id in seen_ids:
                        continue
                    seen_ids.add(full_id)

                    title = (node.get("productName", "") or "").strip()
                    if not title:
                        continue

                    price_min = float(node.get("priceMin", 0) or 0)
                    price_max = float(node.get("priceMax", 0) or 0)
                    current_price = price_min if price_min > 0 else price_max
                    if current_price <= 0:
                        continue

                    discount_rate = float(node.get("priceDiscountRate", 0) or 0)
                    if discount_rate > 0:
                        old_price = current_price / (1 - discount_rate / 100)
                        discount_label = f"{int(discount_rate)}% OFF"
                    else:
                        old_price = current_price * 1.3
                        discount_label = ""

                    image_url = node.get("imageUrl", "") or ""
                    product_url = node.get("offerLink", "") or node.get("productLink", "") or ""

                    if product_url:
                        if product_url.startswith("https://") or product_url.startswith("http://"):
                            product_url = product_url.split("://", 1)[1]

                    all_products.append(Offer(
                        title=title,
                        product_id=full_id,
                        current_price=current_price,
                        old_price=old_price,
                        discount_label=discount_label,
                        image_url=image_url,
                        product_url=product_url,
                    ))
                except Exception as e:
                    logger.debug("Shopee: erro item: %s", e)
                    continue

        logger.info("Shopee: %d ofertas encontradas", len(all_products))
        return all_products[:self.max_offers]