import json
import logging
import re
from typing import List, Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


class Offer:
    def __init__(self, title: str, product_id: str, current_price: float,
                 old_price: Optional[float] = None,
                 discount_label: str = "", image_url: str = "",
                 product_url: str = "",
                 shipping_tags: Optional[list] = None,
                 promo_code: str = "", promo_value: str = "",
                 coupon_label: str = "",
                 installments_qty: int = 0,
                 installment_value: float = 0.0):
        self.title = title
        self.product_id = product_id
        self.current_price = current_price
        self.old_price = old_price
        self.discount_label = discount_label
        self.image_url = image_url
        self._product_url = product_url
        self.shipping_tags = shipping_tags or []
        self.promo_code = promo_code
        self.promo_value = promo_value
        self.coupon_label = coupon_label
        self.installments_qty = installments_qty
        self.installment_value = installment_value

    @property
    def id(self) -> str:
        return self.product_id

    @property
    def clean_url(self) -> str:
        if self._product_url:
            return f"https://{self._product_url}"
        return f"https://www.mercadolivre.com.br/p/{self.product_id}"

    @property
    def discount_percent(self) -> int:
        m = re.search(r'(\d+)%', self.discount_label)
        if m:
            return int(m.group(1))
        if self.old_price and self.current_price:
            return int((1 - self.current_price / self.old_price) * 100)
        return 0

    @property
    def source(self) -> str:
        if self.product_id.startswith("ML"):
            return "Mercado Livre"
        elif self.product_id.startswith("AE"):
            return "AliExpress"
        elif self.product_id.startswith("SH"):
            return "Shopee"
        return "Oferta"

    @property
    def has_full_shipping(self) -> bool:
        return "fulfillment" in self.shipping_tags

    @property
    def has_free_shipping(self) -> bool:
        return "free_shipping" in self.shipping_tags or self.has_full_shipping

    @property
    def score(self) -> float:
        s = self.discount_percent * 1.0
        if self.promo_code or self.coupon_label:
            s += 15
        if self.has_full_shipping:
            s += 15
        elif self.has_free_shipping:
            s += 10
        if self.installments_qty > 1:
            s += 5
        return s

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "product_id": self.product_id,
            "current_price": self.current_price,
            "old_price": self.old_price,
            "discount_label": self.discount_label,
            "image_url": self.image_url,
            "discount_percent": self.discount_percent,
            "promo_code": self.promo_code,
            "promo_value": self.promo_value,
            "coupon_label": self.coupon_label,
            "installments_qty": self.installments_qty,
            "installment_value": self.installment_value,
        }


class MercadoLivreScraper:
    BASE_URL = "https://www.mercadolivre.com.br/ofertas"

    CATEGORIAS = {
        "celulares": "MLB1051",
        "eletronicos": "MLB1000",
        "informatica": "MLB1648",
        "eletrodomesticos": "MLB1144",
        "casa": "MLB1073",
        "moda": "MLB1430",
        "esportes": "MLB1276",
        "ferramentas": "MLB1500",
        "brinquedos": "MLB1132",
        "supermercado": "MLB1403",
        "automotivo": "MLB1743",
        "moveis": "MLB1892",
        "pet": "MLB1071",
        "saude": "MLB1407",
        "bebes": "MLB1384",
    }

    HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    def __init__(self, category: str = "", pages: int = 1, promotion_type: str = "", timeout: int = 30):
        self.category = category
        self.pages = pages
        self.promotion_type = promotion_type
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(self.HEADERS)

    def _make_url(self, page: int) -> str:
        url = self.BASE_URL
        params = {}
        if self.category:
            params["category"] = self.CATEGORIAS.get(self.category.lower(), self.category)
        if self.promotion_type:
            params["promotion_type"] = self.promotion_type
        if page > 1:
            params["page"] = str(page)
        if params:
            url += "?" + "&".join(f"{k}={v}" for k, v in params.items())
        return url

    def scrape(self, max_offers: int = 20, seen_ids: set = None,
               target_new: int = 0, max_pages: int = 20) -> List[Offer]:
        import time

        seen = set()
        all_offers = []
        new_count = 0
        target = target_new if target_new > 0 else max_offers
        page_limit = max(self.pages, max_pages)
        pages_used = 0

        for page in range(1, page_limit + 1):
            pages_used = page
            if page > 1:
                time.sleep(1.5)

            url = self._make_url(page)
            logger.info("Buscando pagina %d (max %d)...", page, page_limit)

            try:
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = "utf-8"
            except Exception as e:
                logger.warning("Erro na pagina %d: %s", page, e)
                continue

            page_offers = self._extract_from_json(resp.text)
            if not page_offers:
                page_offers = self._extract_from_html(resp.text)

            new_in_page = 0
            for offer in page_offers:
                if offer.id in seen:
                    continue
                seen.add(offer.id)
                if seen_ids is not None and offer.id in seen_ids:
                    continue
                all_offers.append(offer)
                new_count += 1
                new_in_page += 1

            logger.info("  -> %d ofertas (%d novas) na pagina %d",
                        len(page_offers), new_in_page, page)

            if not page_offers:
                break

            if seen_ids is not None and new_count >= target:
                logger.info("Ja temos %d ofertas novas, parando", new_count)
                break

        if seen_ids is not None:
            logger.info("Total: %d ofertas novas em %d pagina(s)",
                        len(all_offers), pages_used)
        else:
            logger.info("Total de %d ofertas em %d pagina(s)", len(all_offers), pages_used)
        return all_offers[:max_offers]

    def _extract_from_json(self, html: str) -> List[Offer]:
        match = re.search(r'_n\.ctx\.r\s*=\s*(\{.+?\});', html, re.DOTALL)
        if not match:
            return []

        try:
            ctx = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        app_props = ctx.get("appProps", {})
        page_props = app_props.get("pageProps", {})
        data = page_props.get("data", {})
        items = data.get("items", [])

        offers = []
        for item in items:
            try:
                card = item.get("card", {})
                meta = card.get("metadata", {})
                product_id = meta.get("id", "")
                if not product_id:
                    continue

                components = card.get("components", [])
                title = ""
                current_price = 0.0
                old_price = None
                discount_label = ""
                shipping_tags = []
                coupon_label = ""

                for comp in components:
                    ctype = comp.get("type")

                    if ctype == "title":
                        title = comp.get("title", {}).get("text", "")

                    if ctype == "price":
                        price_data = comp.get("price", {})
                        current = price_data.get("current_price", {})
                        current_price = current.get("value", 0.0)
                        previous = price_data.get("previous_price", {})
                        if previous.get("value"):
                            old_price = previous["value"]
                        discount = price_data.get("discount_label", {})
                        discount_label = discount.get("text", "")
                        inst = price_data.get("installments", {})
                        installments_qty = int(inst.get("quantity", 0) or 0)
                        installment_value = float(inst.get("amount", 0.0) or 0.0)

                    if ctype in ("shipping", "shipping_v2"):
                        tags = comp.get("shipping", {}).get("tags", [])
                        if tags:
                            shipping_tags = tags

                    if ctype == "promotions":
                        promos = comp.get("promotions", [])
                        for promo in promos:
                            if promo.get("type") == "coupon":
                                coupon_label = promo.get("text", "")

                if not title or not product_id:
                    continue

                image_url = ""
                pics = card.get("pictures", {}).get("pictures", [])
                if pics:
                    image_url = (
                        f"https://http2.mlstatic.com/D_{pics[0]['id']}-O.jpg"
                    )

                product_url = meta.get("url", "")
                offers.append(Offer(
                    title=title,
                    product_id=product_id,
                    current_price=current_price,
                    old_price=old_price,
                    discount_label=discount_label,
                    installments_qty=installments_qty,
                    installment_value=installment_value,
                    image_url=image_url,
                    product_url=product_url,
                    shipping_tags=shipping_tags,
                    coupon_label=coupon_label,
                ))
            except Exception as e:
                logger.debug("Error parsing item: %s", e)
                continue

        return offers

    def _extract_from_html(self, html: str) -> List[Offer]:
        soup = BeautifulSoup(html, "html.parser")
        offers = []
        seen = set()

        cards = soup.find_all("li", class_=re.compile(r"ui-search-layout__item"))
        if not cards:
            cards = soup.find_all("div", class_=re.compile(r"poly-card"))

        if not cards:
            cards = soup.find_all("a", href=re.compile(r"/p/MLB\d+"))

        for card in cards:
            if card.name == "a":
                href = card.get("href", "")
                title_el = card
            else:
                link = card.find("a", href=re.compile(r"/p/MLB\d+"))
                if not link:
                    continue
                href = link.get("href", "")
                title_el = link

            match = re.search(r"/p/(MLB\d+)", href)
            if not match:
                continue
            pid = match.group(1)
            if pid in seen:
                continue
            seen.add(pid)

            title = title_el.get("title", "") or title_el.get_text(strip=True)

            current_price = 0.0
            old_price = None
            discount_label = ""

            price_el = card.find("span", class_=re.compile(r"andes-money-amount__fraction"))
            if price_el:
                try:
                    current_price = float(price_el.get_text(strip=True).replace(".", "").replace(",", "."))
                except ValueError:
                    current_price = 0.0

            prev_price_el = card.find("s", class_=re.compile(r"andes-money-amount"))
            if prev_price_el:
                prev_fraction = prev_price_el.find("span", class_=re.compile(r"andes-money-amount__fraction"))
                if prev_fraction:
                    try:
                        old_price = float(prev_fraction.get_text(strip=True).replace(".", "").replace(",", "."))
                    except ValueError:
                        old_price = None

            discount_el = card.find("span", class_=re.compile(r"andes-discount"))
            if discount_el:
                discount_label = discount_el.get_text(strip=True)

            offers.append(Offer(
                title=title,
                product_id=pid,
                current_price=current_price,
                old_price=old_price,
                discount_label=discount_label,
                product_url=href,
            ))

        return offers
