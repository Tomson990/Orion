"""
storage/supabase_sync.py
Sincroniza artículos, clusters y briefings a Supabase.
La PWA consulta Supabase directamente — sin modelo, sin espera.
"""

import os
import json
import httpx
from datetime import datetime
from typing import Optional

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")  # service_role key


def get_headers() -> dict:
    return {
        "apikey": SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=ignore-duplicates"
    }


def sync_articles(articles: list) -> int:
    """
    Sube artículos nuevos a Supabase.
    Solo sube metadata — no embeddings (demasiado pesados para Supabase).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        print("[Supabase] Faltan credenciales — saltando sync")
        return 0

    rows = []
    for a in articles:
        rows.append({
            "id": a["id"],
            "url": a["url"],
            "title": a["title"],
            "source": a["source"],
            "domain": a["domain"],
            "published_at": a.get("published_at"),
        })

    if not rows:
        return 0

    # Supabase acepta hasta 1000 rows por request
    uploaded = 0
    for i in range(0, len(rows), 500):
        batch = rows[i:i+500]
        try:
            r = httpx.post(
                f"{SUPABASE_URL}/rest/v1/ni_articles",
                headers=get_headers(),
                json=batch,
                timeout=30
            )
            if r.status_code in (200, 201):
                uploaded += len(batch)
            else:
                print(f"[Supabase] Error articles: {r.status_code} {r.text[:100]}")
        except Exception as e:
            print(f"[Supabase] Error: {e}")

    print(f"[Supabase] {uploaded} artículos sincronizados")
    return uploaded


def sync_clusters(clusters: list) -> int:
    """Sube clusters del día a Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return 0

    date = datetime.utcnow().strftime("%Y-%m-%d")
    rows = []

    for c in clusters:
        import hashlib
        cluster_id = hashlib.sha256(
            f"{date}_{c['representative_title'][:50]}".encode()
        ).hexdigest()[:16]

        rows.append({
            "id": cluster_id,
            "date": date,
            "representative_title": c.get("representative_title", "")[:200],
            "size": c.get("size", 0),
            "sources": c.get("sources", []),
            "domains": c.get("domains", []),
            "emergence_score": c.get("emergence_score", 0),
            "is_new_topic": c.get("is_new_topic", False),
            "article_titles": [a["title"][:100] for a in c.get("articles", [])[:8]],
            "article_urls": [a["url"] for a in c.get("articles", [])[:8]],
        })

    if not rows:
        return 0

    try:
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/ni_clusters",
            headers=get_headers(),
            json=rows,
            timeout=30
        )
        if r.status_code in (200, 201):
            print(f"[Supabase] {len(rows)} clusters sincronizados")
            return len(rows)
        else:
            print(f"[Supabase] Error clusters: {r.status_code} {r.text[:100]}")
            return 0
    except Exception as e:
        print(f"[Supabase] Error: {e}")
        return 0


def sync_briefing(content: str) -> bool:
    """Sube el briefing del día a Supabase."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return False

    date = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        # Upsert por fecha
        r = httpx.post(
            f"{SUPABASE_URL}/rest/v1/ni_briefings",
            headers={**get_headers(), "Prefer": "resolution=merge-duplicates"},
            json={"date": date, "content": content},
            timeout=30
        )
        if r.status_code in (200, 201):
            print(f"[Supabase] Briefing sincronizado ({date})")
            return True
        else:
            print(f"[Supabase] Error briefing: {r.status_code} {r.text[:100]}")
            return False
    except Exception as e:
        print(f"[Supabase] Error: {e}")
        return False


def get_latest_briefing() -> Optional[str]:
    """Obtiene el briefing más reciente."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return None
    try:
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/ni_briefings?order=date.desc&limit=1",
            headers=get_headers(),
            timeout=10
        )
        data = r.json()
        if data:
            return data[0].get("content")
    except Exception:
        pass
    return None


def get_latest_clusters(limit: int = 20) -> list:
    """Obtiene los clusters más recientes."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/ni_clusters?order=date.desc,emergence_score.desc&limit={limit}",
            headers=get_headers(),
            timeout=10
        )
        return r.json()
    except Exception:
        return []


def search_articles(query: str, domain: Optional[str] = None, limit: int = 10) -> list:
    """Búsqueda por texto en títulos (sin embeddings)."""
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        url = f"{SUPABASE_URL}/rest/v1/ni_articles?title=ilike.*{query}*&order=published_at.desc&limit={limit}"
        if domain:
            url += f"&domain=eq.{domain}"
        r = httpx.get(url, headers=get_headers(), timeout=10)
        return r.json()
    except Exception:
        return []


def get_cluster_history(days: int = 60) -> list:
    """
    Obtiene historial de clusters de los últimos N días desde Supabase.
    Usado para contexto histórico en el briefing.
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/ni_clusters"
            f"?date=gte.{since}"
            f"&order=date.desc,emergence_score.desc"
            f"&limit=200",
            headers=get_headers(),
            timeout=15
        )
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[Supabase] Error obteniendo historial: {e}")
        return []


def find_similar_in_history(title: str, days: int = 60, limit: int = 3) -> list:
    """
    Busca clusters históricos similares a un título dado.
    Usa búsqueda por keywords en lugar de embeddings (más simple para Supabase).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        return []
    try:
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")

        # Extraer keywords del título (palabras de más de 4 letras)
        words = [w for w in title.lower().split() if len(w) > 4][:3]
        if not words:
            return []

        # Buscar por la primera keyword significativa
        keyword = words[0]
        r = httpx.get(
            f"{SUPABASE_URL}/rest/v1/ni_clusters"
            f"?representative_title=ilike.*{keyword}*"
            f"&date=gte.{since}"
            f"&order=date.desc"
            f"&limit={limit}",
            headers=get_headers(),
            timeout=10
        )
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f"[Supabase] Error buscando historial: {e}")
        return []
