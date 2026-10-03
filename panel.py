#!/usr/bin/env python3
"""
Haber Paneli - haber_botu.py'deki kaynakları arka planda tarar ve
tarayıcıda canlı güncellenen bir panelde gösterir.

Kurulum (haber_botu.py, panel.py ve index.html aynı klasörde olmalı):
    pip install feedparser requests beautifulsoup4

Çalıştırma:
    python panel.py                 # http://127.0.0.1:8000
    python panel.py --port 9000 --aralik 60
    python panel.py --host 0.0.0.0  # yerel ağdaki diğer cihazlardan erişim
"""

import argparse
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from haber_botu import KAYNAKLAR, beslemeyi_oku

KLASOR = Path(__file__).resolve().parent
MAX_HABER = 2000


class Depo:
    def __init__(self):
        self.kilit = threading.Lock()
        self.haberler: dict[str, dict] = {}
        self.durum = {k["ad"]: {"ad": k["ad"], "site": k["site"], "ok": None, "adet": 0}
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
            self.durum[ad]["adet"] = sum(1 for h in self.haberler.values() if h["kaynak"] == ad)
            if len(self.haberler) > MAX_HABER:
                eskiler = sorted(self.haberler.values(), key=lambda h: h["zaman"])
                for h in eskiler[: len(self.haberler) - MAX_HABER]:
                    del self.haberler[h["link"]]
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
        with ThreadPoolExecutor(max_workers=8) as havuz:
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
            else:
                self._json({"hata": "bulunamadı"}, 404)

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
    print(f"Panel hazır: http://{'127.0.0.1' if a.host == '0.0.0.0' else a.host}:{a.port}")
    print(f"{len(KAYNAKLAR)} kaynak, {a.aralik} sn aralıkla taranıyor. Çıkmak için Ctrl+C.")
    try:
        sunucu.serve_forever()
    except KeyboardInterrupt:
        print("\nKapatıldı.")


if __name__ == "__main__":
    main()
