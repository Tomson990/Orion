"""
ingestion/company_news.py
Monitorea noticias de compañías energéticas globales via RSS y búsqueda.
Sin API keys requeridas.
"""

import feedparser
import sqlite3
import hashlib
import re
from datetime import datetime, timedelta
from pathlib import Path
from ingestion.commodities import get_conn, init_db

# Compañías a monitorear — globales y regionales
COMPANIES = {
    # Majors
    "exxonmobil":   {"name": "ExxonMobil",      "keywords": ["exxon", "exxonmobil"],          "region": "global"},
    "shell":        {"name": "Shell",            "keywords": ["shell plc", "shell energy"],    "region": "global"},
    "bp":           {"name": "BP",               "keywords": ["bp plc", "british petroleum"],  "region": "global"},
    "totalenergies":{"name": "TotalEnergies",    "keywords": ["totalenergies", "total se"],    "region": "global"},
    "chevron":      {"name": "Chevron",          "keywords": ["chevron corporation"],          "region": "global"},
    # NOCs
    "aramco":       {"name": "Saudi Aramco",     "keywords": ["aramco", "saudi aramco"],       "region": "middleeast"},
    "petrobras":    {"name": "Petrobras",        "keywords": ["petrobras"],                    "region": "latam"},
    "pemex":        {"name": "Pemex",            "keywords": ["pemex", "petróleos mexicanos"], "region": "latam"},
    # LNG & Midstream
    "qatar_energy": {"name": "Qatar Energy",     "keywords": ["qatar energy", "qatarenergy"],  "region": "middleeast"},
    "cheniere":     {"name": "Cheniere Energy",  "keywords": ["cheniere"],                     "region": "global"},
    "glencore":     {"name": "Glencore",         "keywords": ["glencore"],                     "region": "global"},
    # Renovables
    "orsted":       {"name": "Orsted",           "keywords": ["orsted", "ørsted"],             "region": "global"},
    "iberdrola":    {"name": "Iberdrola",        "keywords": ["iberdrola"],                    "region": "global"},
    "enel":         {"name": "Enel",             "keywords": ["enel group", "enel spa"],       "region": "global"},
    # Regionales Argentina
    "ypf":          {"name": "YPF",              "keywords": ["ypf", "yacimientos petrolíferos"],"region": "argentina"},
    "pampa":        {"name": "Pampa Energía",    "keywords": ["pampa energía", "pampa energia"],"region": "argentina"},
    "tgs":          {"name": "TGS",              "keywords": ["transportadora de gas del sur", "tgs"],"region": "argentina"},
    "pae":          {"name": "Pan American Energy","keywords": ["pan american energy", "pae"], "region": "argentina"},
    "tecpetrol":    {"name": "Tecpetrol",        "keywords": ["tecpetrol"],                    "region": "argentina"},
}

# Feeds RSS especializados en energía
ENERGY_FEEDS = [
    "https://oilprice.com/rss/main",
    "https://www.energymonitor.ai/feed/",
    "https://energiaynegocios.com.ar/feed/",
    "https://ecojournal.com.ar/feed/",
    "https://www.ft.com/energy?format=rss",
    "https://www.rechargenews.com/rss",
    "https://feeds.reuters.com/reuters/businessNews",
    "https://feeds.bloomberg.com/markets/news.rss",
]


def clean_html(text: str) -> str:
    return re.sub(r"<[^>]+>", " ", text or "").strip()


def article_matches_company(title: str, summary: str, keywords: list) -> bool:
    text = f"{title} {summary}".lower()
    return any(kw.lower() in text for kw in keywords)


def fetch_company_news() -> dict:
    """
    Descarga noticias de todos los feeds y filtra por compañía.
    Retorna dict: {company_key: [articles]}
    """
    init_db()
    conn = get_conn()

    company_articles = {key: [] for key in COMPANIES}
    total_new = 0

    print("[Orion] Descargando noticias de compañías...")

    for feed_url in ENERGY_FEEDS:
        try:
            feed = feedparser.parse(feed_url)
            source = feed.feed.get("title", feed_url[:30])

            for entry in feed.entries[:30]:
                title   = clean_html(getattr(entry, "title", ""))
                summary = clean_html(getattr(entry, "summary", ""))
                url     = getattr(entry, "link", "")
                pub     = getattr(entry, "published", "")

                if not title or not url:
                    continue

                # Verificar contra cada compañía
                for key, info in COMPANIES.items():
                    if article_matches_company(title, summary, info["keywords"]):
                        # Deduplicar por hash de URL
                        url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]

                        existing = conn.execute(
                            "SELECT 1 FROM company_news WHERE url = ?", (url,)
                        ).fetchone()

                        if not existing:
                            conn.execute("""
                                INSERT INTO company_news (company, title, url, source, published)
                                VALUES (?, ?, ?, ?, ?)
                            """, (key, title[:300], url, source, pub))
                            company_articles[key].append({
                                "title": title,
                                "url": url,
                                "source": source,
                                "published": pub
                            })
                            total_new += 1

        except Exception as e:
            print(f"  [!] {feed_url[:50]}: {e}")

    conn.commit()
    conn.close()

    companies_with_news = sum(1 for v in company_articles.values() if v)
    print(f"[Orion] {total_new} noticias nuevas · {companies_with_news} compañías con actividad")
    return company_articles


def get_recent_company_news(company: str = None, hours: int = 48) -> list:
    """Retorna noticias recientes de una compañía o todas."""
    conn = get_conn()
    if company:
        rows = conn.execute("""
            SELECT * FROM company_news
            WHERE company = ?
            ORDER BY fetched_at DESC LIMIT 20
        """, (company,)).fetchall()
    else:
        rows = conn.execute("""
            SELECT * FROM company_news
            ORDER BY fetched_at DESC LIMIT 100
        """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_company_summary() -> dict:
    """Resumen de actividad por compañía."""
    conn = get_conn()
    rows = conn.execute("""
        SELECT company, COUNT(*) as n, MAX(fetched_at) as last_seen
        FROM company_news
        GROUP BY company
        ORDER BY n DESC
    """).fetchall()
    conn.close()
    return {r["company"]: {"count": r["n"], "last_seen": r["last_seen"]} for r in rows}


if __name__ == "__main__":
    fetch_company_news()
    summary = get_company_summary()
    print("\nActividad por compañía:")
    for company, data in summary.items():
        name = COMPANIES.get(company, {}).get("name", company)
        print(f"  {name:<25} {data['count']} noticias")
