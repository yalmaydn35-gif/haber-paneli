#!/usr/bin/env python3
"""
Haber Paneli - Muhalif, hükümete yakın ve yurtdışı haber kaynaklarını RSS
üzerinden arka planda tarar ve tarayıcıda canlı güncellenen bir panelde gösterir.

Bu dosya tek başına çalışır (haber_botu.py gerekmez). Aynı klasörde
index.html bulunmalıdır.

Kurulum:
    pip install -r requirements.txt

Çalıştırma:
    python panel.py                 # http://127.0.0.1:8000
    python panel.py --port 9000 --aralik 60
    python panel.py --host 0.0.0.0  # yerel ağdaki diğer cihazlardan erişim

Render:
    Build command: pip install -r requirements.txt
    Start command: python panel.py
    (Port, PORT ortam değişkeninden otomatik alınır.)
"""

import argparse
import calendar
import json
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlparse

import feedparser
import requests
from bs4 import BeautifulSoup

KLASOR = Path(__file__).resolve().parent
MAX_HABER = 6000
ZAMAN_ASIMI = 15
socket.setdefaulttimeout(ZAMAN_ASIMI)  # feedparser'ın takılı kalmasını önler

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) HaberPaneli/2.0 (+kisisel kullanim)"
}


# ---------------------------------------------------------------------------
# KAYNAKLAR — "feed" boş ya da çalışmıyorsa ana sayfadan otomatik keşfedilir.
# ---------------------------------------------------------------------------
def _grup(grup, kategori, liste):
    return [{"ad": ad, "site": site, "feed": feed, "grup": grup, "kategori": kategori}
            for ad, site, feed in liste]


KAYNAKLAR = [
    # ============================ MUHALİF ============================
    *_grup("Muhalif", "Kemalist / ulusalcı", [
        ("Sözcü",          "https://www.sozcu.com.tr",           "https://www.sozcu.com.tr/feeds-son-dakika"),
        ("Cumhuriyet",     "https://www.cumhuriyet.com.tr",      "https://www.cumhuriyet.com.tr/rss/son_dakika.xml"),
        ("Halk TV",        "https://halktv.com.tr",              "https://halktv.com.tr/service/rss.php"),
        ("Oda TV",         "https://www.odatv.com",              ""),
        ("Yeniçağ",        "https://www.yenicaggazetesi.com.tr", ""),
        ("Korkusuz",       "https://www.korkusuz.com.tr",        ""),
        ("Nefes",          "https://www.nefes.com.tr",           ""),
        ("Muhalif",        "https://www.muhalif.com.tr",         ""),
        ("Gerçek Gündem",  "https://www.gercekgundem.com",       ""),
        ("ANKA",           "https://ankahaber.net",              ""),
    ]),
    *_grup("Muhalif", "Sol / sosyalist", [
        ("BirGün",         "https://www.birgun.net",             "https://www.birgun.net/rss/home"),
        ("Evrensel",       "https://www.evrensel.net",           "https://www.evrensel.net/rss/haber.xml"),
        ("Sendika.org",    "https://sendika.org",                "https://sendika.org/feed/"),
        ("soL Haber",      "https://haber.sol.org.tr",           ""),
        ("İleri Haber",    "https://ilerihaber.org",             ""),
        ("Ekmek ve Gül",   "https://ekmekvegul.net",             ""),
        ("Artı Gerçek",    "https://artigercek.com",             ""),
        ("Gazete Pencere", "https://www.gazetepencere.com",      ""),
    ]),
    *_grup("Muhalif", "Liberal / bağımsız", [
        ("T24",            "https://t24.com.tr",                 "https://t24.com.tr/rss"),
        ("Medyascope",     "https://medyascope.tv",              "https://medyascope.tv/feed/"),
        ("Diken",          "https://www.diken.com.tr",           "https://www.diken.com.tr/feed/"),
        ("Bianet",         "https://bianet.org",                 ""),
        ("Gazete Oksijen", "https://gazeteoksijen.com",          ""),
        ("Kısa Dalga",     "https://kisadalga.net",              ""),
        ("Yetkin Report",  "https://yetkinreport.com",           "https://yetkinreport.com/feed/"),
        ("Journo",         "https://journo.com.tr",              ""),
        ("dokuz8haber",    "https://www.dokuz8haber.net",        ""),
        ("Gazete Kadıköy", "https://www.gazetekadikoy.com.tr",   ""),
        ("Karar",          "https://www.karar.com",              ""),
        ("Serbestiyet",    "https://serbestiyet.com",            "https://serbestiyet.com/feed/"),
        ("PolitikYol",     "https://www.politikyol.com",         ""),
    ]),
    *_grup("Muhalif", "Kürt basını", [
        ("Mezopotamya Ajansı", "https://mezopotamyaajansi.com",  ""),
        ("JinNews",        "https://jinnews.net",                ""),
        ("Yeni Yaşam",     "https://yeniyasamgazetesi.com",      ""),
    ]),
    *_grup("Muhalif", "Türkçe servisler", [
        ("BBC Türkçe",     "https://www.bbc.com/turkce",         "https://feeds.bbci.co.uk/turkce/rss.xml"),
        ("DW Türkçe",      "https://www.dw.com/tr",              "https://rss.dw.com/rdf/rss-tur-all"),
        ("Euronews Türkçe", "https://tr.euronews.com",           "https://tr.euronews.com/rss"),
        ("Independent Türkçe", "https://www.indyturk.com",       ""),
        ("Sputnik Türkçe", "https://anlatilaninotesi.com.tr",    "https://anlatilaninotesi.com.tr/export/rss2/archive/index.xml"),
    ]),

    # ========================= HÜKÜMETE YAKIN =========================
    *_grup("Hükümete yakın", "AKP medyası", [
        ("Sabah",          "https://www.sabah.com.tr",           "https://www.sabah.com.tr/rss/anasayfa.xml"),
        ("A Haber",        "https://www.ahaber.com.tr",          "https://www.ahaber.com.tr/rss/anasayfa.xml"),
        ("Star",           "https://www.star.com.tr",            ""),
        ("Yeni Şafak",     "https://www.yenisafak.com",          ""),
        ("TVNET",          "https://www.tvnet.com.tr",           ""),
        ("Kanal 7",        "https://www.kanal7.com",             ""),
        ("Haber7",         "https://www.haber7.com",             ""),
        ("Yeni Akit",      "https://www.yeniakit.com.tr",        ""),
        ("Diriliş Postası", "https://www.dirilispostasi.com",    ""),
        ("Türkiye Gazetesi", "https://www.turkiyegazetesi.com.tr", ""),
        ("TGRT Haber",     "https://www.tgrthaber.com",          ""),
    ]),
    *_grup("Hükümete yakın", "Ana akım", [
        ("Hürriyet",       "https://www.hurriyet.com.tr",        "https://www.hurriyet.com.tr/rss/anasayfa"),
        ("Milliyet",       "https://www.milliyet.com.tr",        ""),
        ("CNN Türk",       "https://www.cnnturk.com",            "https://www.cnnturk.com/feed/rss/all/news"),
        ("Habertürk",      "https://www.haberturk.com",          "https://www.haberturk.com/rss"),
        ("NTV",            "https://www.ntv.com.tr",             "https://www.ntv.com.tr/son-dakika.rss"),
        ("Posta",          "https://www.posta.com.tr",           ""),
    ]),
    *_grup("Hükümete yakın", "Cumhur İttifakı", [
        ("Türkgün",        "https://www.turkgun.com",            ""),
        ("Ortadoğu",       "https://www.ortadogugazetesi.com.tr", ""),
        ("Aydınlık",       "https://www.aydinlik.com.tr",        ""),
    ]),
    *_grup("Hükümete yakın", "Devlet yayın kuruluşları", [
        ("TRT Haber",      "https://www.trthaber.com",           "https://www.trthaber.com/sondakika.rss"),
        ("Anadolu Ajansı", "https://www.aa.com.tr/tr",           "https://www.aa.com.tr/tr/rss/default?cat=guncel"),
    ]),

    # ============================ YURTDIŞI ============================
    *_grup("Yurtdışı", "Yurtdışı", [
        ("Reuters",        "https://www.reuters.com",            ""),
        ("AP",             "https://apnews.com",                 ""),
        ("BBC News",       "https://www.bbc.com/news",           "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("The Guardian",   "https://www.theguardian.com/international", "https://www.theguardian.com/world/rss"),
        ("New York Times", "https://www.nytimes.com",            "https://rss.nytimes.com/services/xml/rss/nyt/World.xml"),
        ("Financial Times", "https://www.ft.com",                "https://www.ft.com/world?format=rss"),
        ("Bloomberg",      "https://www.bloomberg.com",          "https://feeds.bloomberg.com/politics/news.rss"),
        ("The Economist",  "https://www.economist.com",          "https://www.economist.com/international/rss.xml"),
        ("Al Jazeera",     "https://www.aljazeera.com",          "https://www.aljazeera.com/xml/rss/all.xml"),
    ]),
]


# ---------------------------------------------------------------------------
# RSS okuma
# ---------------------------------------------------------------------------
def feed_kesfet(site: str) -> str | None:
    """Ana sayfadaki RSS/Atom link etiketini bulur, yoksa yaygın yolları dener."""
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
# Bellek içi depo
# ---------------------------------------------------------------------------
class Depo:
    def __init__(self):
        self.kilit = threading.Lock()
        self.haberler: dict[str, dict] = {}
        self.durum = {k["ad"]: {"ad": k["ad"], "site": k["site"], "grup": k["grup"],
                                "kategori": k["kategori"], "ok": None, "adet": 0}
                      for k in KAYNAKLAR}
        self.son_tarama: float | None = None
        self.taraniyor = False

    def ekle(self, ad: str, liste: list[dict]) -> int:
        simdi = time.time()
        yeni = 0
        with self.kilit:
            self.durum[ad]["ok"] = bool(liste)
            for h in liste:
                if h["link"] in self.haberler:
                    continue
                self.haberler[h["link"]] = {**h, "eklenme": simdi}
                yeni += 1
            if len(self.haberler) > MAX_HABER:
                eskiler = sorted(self.haberler.values(), key=lambda h: h["zaman"])
                for h in eskiler[: len(self.haberler) - MAX_HABER]:
                    del self.haberler[h["link"]]
            self.durum[ad]["adet"] = sum(1 for h in self.haberler.values() if h["kaynak"] == ad)
        return yeni

    def anlik(self, since: float) -> dict:
        with self.kilit:
            liste = [h for h in self.haberler.values() if h["eklenme"] > since]
            return {
                "simdi": time.time(),
                "son_tarama": self.son_tarama,
                "taraniyor": self.taraniyor,
                "kaynaklar": list(self.durum.values()),
                "haberler": sorted(liste, key=lambda h: h["zaman"], reverse=True),
            }


def tara(depo: Depo) -> None:
    depo.taraniyor = True
    try:
        with ThreadPoolExecutor(max_workers=16) as havuz:
            isler = {havuz.submit(beslemeyi_oku, k): k for k in KAYNAKLAR}
            for is_ in as_completed(isler):
                try:
                    sonuc = is_.result()
                except Exception:
                    sonuc = []
                depo.ekle(isler[is_]["ad"], sonuc)
        depo.son_tarama = time.time()
    finally:
        depo.taraniyor = False


def tarayici_dongusu(depo: Depo, aralik: int, tetik: threading.Event) -> None:
    while True:
        tara(depo)
        tetik.wait(aralik)   # "Şimdi tara" butonu bekleyişi erken bitirir
        tetik.clear()


# ---------------------------------------------------------------------------
# HTTP sunucu
# ---------------------------------------------------------------------------
def handler_olustur(depo: Depo, tetik: threading.Event, aralik: int):
    class Isleyici(BaseHTTPRequestHandler):
        def _json(self, veri, kod=200):
            govde = json.dumps(veri, ensure_ascii=False).encode("utf-8")
            self.send_response(kod)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(govde)))
            self.end_headers()
            self.wfile.write(govde)

        def do_GET(self):
            url = urlparse(self.path)
            if url.path in ("/", "/index.html"):
                govde = (KLASOR / "index.html").read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(govde)))
                self.end_headers()
                self.wfile.write(govde)
            elif url.path == "/api/haberler":
                try:
                    since = float(parse_qs(url.query).get("since", ["0"])[0])
                except ValueError:
                    since = 0.0
                veri = depo.anlik(since)
                veri["aralik"] = aralik
                self._json(veri)
            elif url.path == "/saglik":
                self._json({"ok": True})
            else:
                self._json({"hata": "bulunamadı"}, 404)

        def do_HEAD(self):
            self.send_response(200)
            self.end_headers()

        def do_POST(self):
            if urlparse(self.path).path == "/api/tara":
                tetik.set()
                self._json({"ok": True})
            else:
                self._json({"hata": "bulunamadı"}, 404)

        def log_message(self, *args):
            pass  # konsolu istek kayıtlarıyla doldurma

    return Isleyici


def main() -> None:
    # Render gibi platformlar portu PORT ortam değişkeniyle verir
    ortam_port = os.getenv("PORT")
    p = argparse.ArgumentParser(description="Haber paneli")
    p.add_argument("--host", default="0.0.0.0" if ortam_port else "127.0.0.1")
    p.add_argument("--port", type=int, default=int(ortam_port or 8000))
    p.add_argument("--aralik", type=int, default=90, help="Tarama aralığı (sn)")
    a = p.parse_args()

    depo = Depo()
    tetik = threading.Event()
    threading.Thread(target=tarayici_dongusu, args=(depo, a.aralik, tetik), daemon=True).start()

    sunucu = ThreadingHTTPServer((a.host, a.port), handler_olustur(depo, tetik, a.aralik))
    print(f"Panel hazır: http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}", flush=True)
    print(f"{len(KAYNAKLAR)} kaynak, {a.aralik} sn aralıkla taranıyor.", flush=True)
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatıldı.")


if __name__ == "__main__":
    main()
