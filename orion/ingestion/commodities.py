"""
ingestion/commodities.py
Descarga precios de commodities energéticos y metales críticos.
Fuente: Yahoo Finance (gratuito, sin API key)
"""

import json
import sqlite3
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

DATA_PATH = Path(__file__).parent.parent / "data"
DATA_PATH.mkdir(exist_ok=True)
DB_PATH = DATA_PATH / "orion.db"

# Commodities a monitorear
COMMODITIES = {
    # Energía
    "brent":     {"ticker": "BZ=F",  "name": "Brent Crude",        "unit": "USD/bbl",  "category": "energia"},
    "wti":       {"ticker": "CL=F",  "name": "WTI Crude",          "unit": "USD/bbl",  "category": "energia"},
    "henry_hub": {"ticker": "NG=F",  "name": "Henry Hub Gas",      "unit": "USD/MMBtu","category": "energia"},
    "ttf":       {"ticker": "TTF=F", "name": "TTF Gas Europa",     "unit": "EUR/MWh",  "category": "energia"},
    "carbon":    {"ticker": "CER=F", "name": "Carbon ETS",         "unit": "EUR/ton",  "category": "energia"},
    # Metales críticos
    "copper":    {"ticker": "HG=F",  "name": "Cobre",              "unit": "USD/lb",   "category": "metales"},
    "aluminium": {"ticker": "ALI=F", "name": "Aluminio",           "unit": "USD/ton",  "category": "metales"},
    "nickel":    {"ticker": "NI=F",  "name": "Níquel",             "unit": "USD/ton",  "category": "metales"},
    "steel":     {"ticker": "HRC=F", "name": "Acero HRC",          "unit": "USD/ton",  "category": "metales"},
    "lithium":   {"ticker": "LTHM",  "name": "Lithium (proxy)",    "unit": "USD",      "category": "metales"},
    # Fletes
    "bdi":       {"ticker": "BDI",   "name": "Baltic Dry Index",   "unit": "points",   "category": "fletes"},
    # FX relevantes
    "usd_ars":   {"ticker": "ARS=X", "name": "USD/ARS",            "unit": "ARS",      "category": "fx"},
    "usd_brl":   {"ticker": "BRL=X", "name": "USD/BRL",            "unit": "BRL",      "category": "fx"},
    "usd_eur":   {"ticker": "EURUSD=X","name": "EUR/USD",          "unit": "USD",      "category": "fx"},
}


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS commodity_prices (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            commodity   TEXT NOT NULL,
            ticker      TEXT NOT NULL,
            price       REAL,
            prev_price  REAL,
            change_pct  REAL,
            category    TEXT,
            unit        TEXT,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_commodity ON commodity_prices(commodity);
        CREATE INDEX IF NOT EXISTS idx_fetched   ON commodity_prices(fetched_at DESC);

        CREATE TABLE IF NOT EXISTS company_news (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            company     TEXT NOT NULL,
            title       TEXT,
            url         TEXT,
            source      TEXT,
            published   TEXT,
            fetched_at  TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_company ON company_news(company);

        CREATE TABLE IF NOT EXISTS risk_scores (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            date        TEXT NOT NULL,
            sector      TEXT,
            score       REAL,
            level       TEXT,
            signals     TEXT,
            created_at  TEXT DEFAULT (datetime('now'))
        );
    """)
    conn.commit()
    conn.close()
    print(f"[Orion] DB inicializada en {DB_PATH}")


def fetch_yahoo_price(ticker: str) -> dict:
    """Descarga precio actual de Yahoo Finance sin API key."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?interval=1d&range=5d"
    headers = {"User-Agent": "Mozilla/5.0"}

    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())

        result = data["chart"]["result"][0]
        meta = result["meta"]
        closes = result["indicators"]["quote"][0].get("close", [])

        # Filtrar Nones
        closes = [c for c in closes if c is not None]

        if len(closes) < 2:
            return {"price": closes[-1] if closes else None, "prev": None, "change_pct": None}

        price = closes[-1]
        prev  = closes[-2]
        change_pct = ((price - prev) / prev * 100) if prev else None

        return {
            "price": round(price, 4),
            "prev": round(prev, 4),
            "change_pct": round(change_pct, 2) if change_pct else None
        }
    except Exception as e:
        print(f"  [!] {ticker}: {e}")
        return {"price": None, "prev": None, "change_pct": None}


def fetch_all_commodities() -> list:
    """Descarga todos los precios y los guarda en DB."""
    init_db()
    conn = get_conn()
    results = []

    print("[Orion] Descargando precios de commodities...")

    for key, info in COMMODITIES.items():
        print(f"  {info['name']}...", end=" ")
        data = fetch_yahoo_price(info["ticker"])

        if data["price"]:
            conn.execute("""
                INSERT INTO commodity_prices (commodity, ticker, price, prev_price, change_pct, category, unit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (key, info["ticker"], data["price"], data["prev"], data["change_pct"],
                  info["category"], info["unit"]))
            print(f"{data['price']} {info['unit']} ({data['change_pct']:+.1f}%)" if data['change_pct'] else f"{data['price']}")
        else:
            print("N/A")

        results.append({
            "key": key,
            "name": info["name"],
            "category": info["category"],
            "unit": info["unit"],
            **data
        })

    conn.commit()
    conn.close()
    return results


def get_latest_prices() -> list:
    """Retorna los precios más recientes de cada commodity."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT DISTINCT commodity, ticker, price, prev_price, change_pct, category, unit, fetched_at
        FROM commodity_prices
        WHERE fetched_at = (SELECT MAX(fetched_at) FROM commodity_prices WHERE commodity = c.commodity)
        FROM commodity_prices c
        ORDER BY category, commodity
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_latest_prices_simple() -> list:
    """Versión simple de get_latest_prices."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT commodity, ticker, price, prev_price, change_pct, category, unit, MAX(fetched_at) as fetched_at
        FROM commodity_prices
        GROUP BY commodity
        ORDER BY category, commodity
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    results = fetch_all_commodities()
    print(f"\n[Orion] {len([r for r in results if r['price']])} precios descargados")
