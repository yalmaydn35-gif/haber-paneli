#!/usr/bin/env python3
"""
Haber Botu - Muhalif / bağımsız haber sitelerinden anlık haber takibi.

Siteleri HTML kazıyarak değil, RSS beslemeleri üzerinden okur (daha hızlı,
daha stabil ve sitelere daha az yük bindirir). Bir beslemenin adresi
bilinmiyorsa ya da değişmişse, sitenin ana sayfasındaki
<link rel="alternate" type="application/rss+xml"> etiketinden otomatik bulur.

Kurulum:
    pip install feedparser requests beautifulsoup4

Kullanım:
    python haber_botu.py                       # terminalde canlı akış
    python haber_botu.py --aralik 60           # 60 sn'de bir kontrol
    python haber_botu.py --filtre "ekonomi,seçim"
    python haber_botu.py --sadece-yeni         # açılışta eski haberleri gösterme

Telegram'a göndermek için (opsiyonel):
    export TELEGRAM_BOT_TOKEN="123456:ABC..."
    export TELEGRAM_CHAT_ID="123456789"
"""

import argparse
import calendar
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin

import feedparser
import requests
from bs4 import BeautifulSoup

# ---------------------------------------------------------------------------
# KAYNAKLAR — istediğin gibi ekle / çıkar.
# "feed" boş ya da çalışmıyorsa ana sayfadan otomatik keşfedilir.
# ---------------------------------------------------------------------------
KAYNAKLAR = [
    {"ad": "Sözcü",       "site": "https://www.sozcu.com.tr",    "feed": "https://www.sozcu.com.tr/feeds-son-dakika"},
    {"ad": "Cumhuriyet",  "site": "https://www.cumhuriyet.com.tr", "feed": "https://www.cumhuriyet.com.tr/rss/son_dakika.xml"},
    {"ad": "BirGün",      "site": "https://www.birgun.net",      "feed": "https://www.birgun.net/rss/home"},
    {"ad": "Halk TV",     "site": "https://halktv.com.tr",       "feed": "https://halktv.com.tr/service/rss.php"},
    {"ad": "T24",         "site": "https://t24.com.tr",          "feed": "https://t24.com.tr/rss"},
    {"ad": "Medyascope",  "site": "https://medyascope.tv",       "feed": "https://medyascope.tv/feed/"},
    {"ad": "Bianet",      "site": "https://bianet.org",          "feed": ""},
    {"ad": "Diken",       "site": "https://www.diken.com.tr",    "feed": "https://www.diken.com.tr/feed/"},
    {"ad": "Evrensel",    "site": "https://www.evrensel.net",    "feed": "https://www.evrensel.net/rss/haber.xml"},
    {"ad": "Karar",       "site": "https://www.karar.com",       "feed": ""},
    {"ad": "Oda TV",      "site": "https://www.odatv.com",       "feed": ""},
    {"ad": "Artı Gerçek", "site": "https://artigercek.com",      "feed": ""},
    {"ad": "Serbestiyet", "site": "https://serbestiyet.com",     "feed": "https://serbestiyet.com/feed/"},
    {"ad": "Yeniçağ",     "site": "https://www.yenicaggazetesi.com.tr", "feed": ""},
    {"ad": "Sendika.org", "site": "https://sendika.org",         "feed": "https://sendika.org/feed/"},
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HaberBotu/1.0 (+kisisel kullanim)"
}
ZAMAN_ASIMI = 15
GORULEN_DOSYA = Path(__file__).with_name("gorulen_haberler.json")
MAX_GORULEN = 5000

# Terminal renkleri
R = {"bas": "\033[1m", "mavi": "\033[94m", "gri": "\033[90m",
     "yesil": "\033[92m", "kirmizi": "\033[91m", "son": "\033[0m"}
if not sys.stdout.isatty():
    R = {k: "" for k in R}


# ---------------------------------------------------------------------------
def feed_kesfet(site: str) -> str | None:
    """Ana sayfadaki RSS/Atom link etiketini bulur."""
    try:
        r = requests.get(site, headers=HEADERS, timeout=ZAMAN_ASIMI)
        r.raise_for_status()
    except requests.RequestException:
        return None
    soup = BeautifulSoup(r.text, "html.parser")
    for tip in ("application/rss+xml", "application/atom+xml"):
        etiket = soup.find("link", rel="alternate", type=tip)
        if etiket and etiket.get("href"):
            return urljoin(site, etiket["href"])
    # Yaygın yolları dene
    for yol in ("/feed/", "/rss", "/rss.xml", "/feed"):
        aday = urljoin(site, yol)
        if feedparser.parse(aday, request_headers=HEADERS).entries:
            return aday
    return None


def beslemeyi_oku(kaynak: dict) -> list[dict]:
    """Bir kaynağın haberlerini döndürür; gerekirse feed adresini keşfeder."""
    url = kaynak.get("feed")
    d = feedparser.parse(url, request_headers=HEADERS) if url else None

    if not d or not d.entries:
        kesfedilen = feed_kesfet(kaynak["site"])
        if kesfedilen:
            kaynak["feed"] = kesfedilen  # bir sonraki turda doğrudan kullan
            d = feedparser.parse(kesfedilen, request_headers=HEADERS)

    if not d or not d.entries:
        return []

    haberler = []
    for e in d.entries:
        link = e.get("link")
        if not link:
            continue
        zaman = e.get("published_parsed") or e.get("updated_parsed")
        ts = calendar.timegm(zaman) if zaman else time.time()  # feedparser UTC verir
        haberler.append({
            "kaynak": kaynak["ad"],
            "baslik": (e.get("title") or "").strip(),
            "link": link,
            "zaman": min(ts, time.time()),  # ileri tarihli hatalı beslemelere karşı
        })
    return haberler


# ---------------------------------------------------------------------------
def gorulenleri_yukle() -> list[str]:
    try:
        return json.loads(GORULEN_DOSYA.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def gorulenleri_kaydet(liste: list[str]) -> None:
    GORULEN_DOSYA.write_text(json.dumps(liste[-MAX_GORULEN:], ensure_ascii=False),
                             encoding="utf-8")


def telegram_gonder(haber: dict) -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not (token and chat):
        return
    metin = f"<b>[{haber['kaynak']}]</b> {haber['baslik']}\n{haber['link']}"
    try:
        requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat, "text": metin, "parse_mode": "HTML",
                            "disable_web_page_preview": "true"},
                      timeout=ZAMAN_ASIMI)
        time.sleep(1)  # Telegram hız sınırı
    except requests.RequestException as hata:
        print(f"{R['kirmizi']}Telegram hatası: {hata}{R['son']}")


def yazdir(haber: dict) -> None:
    saat = datetime.fromtimestamp(haber["zaman"]).strftime("%d.%m %H:%M")
    print(f"{R['gri']}{saat}{R['son']}  {R['bas']}{R['mavi']}[{haber['kaynak']}]"
          f"{R['son']} {haber['baslik']}\n           {R['gri']}{haber['link']}{R['son']}")


# ---------------------------------------------------------------------------
def tur(gorulen: set, gorulen_sira: list, filtreler: list[str], goster: bool) -> int:
    tum = []
    hatali = []
    with ThreadPoolExecutor(max_workers=8) as havuz:
        isler = {havuz.submit(beslemeyi_oku, k): k for k in KAYNAKLAR}
        for is_ in as_completed(isler):
            k = isler[is_]
            try:
                sonuc = is_.result()
            except Exception:
                sonuc = []
            if not sonuc:
                hatali.append(k["ad"])
            tum.extend(sonuc)

    yeniler = [h for h in tum if h["link"] not in gorulen]
    if filtreler:
        yeniler = [h for h in yeniler
                   if any(f in h["baslik"].lower() for f in filtreler)]
    yeniler.sort(key=lambda h: h["zaman"])

    for h in yeniler:
        gorulen.add(h["link"])
        gorulen_sira.append(h["link"])
        if goster:
            yazdir(h)
            telegram_gonder(h)

    # Filtreye takılmayanları da "görüldü" say ki tekrar taranmasın
    for h in tum:
        if h["link"] not in gorulen:
            gorulen.add(h["link"])
            gorulen_sira.append(h["link"])

    if hatali:
        print(f"{R['kirmizi']}Okunamayan kaynaklar: {', '.join(sorted(hatali))}{R['son']}")
    return len(yeniler) if goster else 0


def main() -> None:
    p = argparse.ArgumentParser(description="Anlık haber botu")
    p.add_argument("--aralik", type=int, default=120, help="Kontrol aralığı (sn)")
    p.add_argument("--filtre", default="", help="Virgülle ayrılmış anahtar kelimeler")
    p.add_argument("--sadece-yeni", action="store_true",
                   help="İlk turda mevcut haberleri gösterme, sadece sonrakileri göster")
    a = p.parse_args()

    filtreler = [f.strip().lower() for f in a.filtre.split(",") if f.strip()]
    gorulen_sira = gorulenleri_yukle()
    gorulen = set(gorulen_sira)

    print(f"{R['yesil']}Haber botu başladı — {len(KAYNAKLAR)} kaynak, "
          f"{a.aralik} sn aralıkla. Çıkmak için Ctrl+C.{R['son']}\n")

    ilk = True
    try:
        while True:
            goster = not (ilk and a.sadece_yeni)
            n = tur(gorulen, gorulen_sira, filtreler, goster)
            gorulenleri_kaydet(gorulen_sira)
            simdi = datetime.now().strftime("%H:%M:%S")
            print(f"{R['gri']}— {simdi}: {n} yeni haber. "
                  f"Sonraki kontrol {a.aralik} sn sonra —{R['son']}\n")
            ilk = False
            time.sleep(a.aralik)
    except KeyboardInterrupt:
        gorulenleri_kaydet(gorulen_sira)
        print("\nKapatıldı.")


if __name__ == "__main__":
    main()
