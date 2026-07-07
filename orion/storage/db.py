"""
storage/db.py
Persistencia de artículos y embeddings en Supabase (Postgres + pgvector).
"""

import os
import hashlib
import numpy as np
from datetime import datetime
from typing import Optional
from supabase import create_client

_client = None

def get_client():
    global _client
    if _client is None:
        _client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    return _client


def init_db():
    """Las tablas ya se crearon a mano en el SQL Editor de Supabase. No-op."""
    print("[DB] Usando Supabase — tablas ya inicializadas manualmente.")


def article_exists(url: str) -> bool:
    article_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    res = get_client().table("articles").select("id").eq("id", article_id).execute()
    return len(res.data) > 0


def save_article(
    url: str,
    title: str,
    body: str,
    source: str,
    domain: str,
    published_at: Optional[str],
    embedding: np.ndarray
):
    article_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    get_client().table("articles").upsert({
        "id": article_id,
        "url": url,
        "title": title,
        "body": body[:2000],
        "source": source,
        "domain": domain,
        "published_at": published_at,
        "ingested_at": datetime.utcnow().isoformat(),
        "embedding": embedding.astype(np.float32).tolist()
    }).execute()


def load_embeddings(domain: Optional[str] = None, limit: int = 5000):
    """
    Retorna lista de dicts con id, title, source, domain, published_at, embedding (np.ndarray).
    """
    query = get_client().table("articles").select("*")
    if domain:
        query = query.eq("domain", domain)
    res = query.order("published_at", desc=True).limit(limit).execute()

    results = []
    for row in res.data:
        raw = row["embedding"]
        if isinstance(raw, str):
            raw = raw.strip("[]").split(",")
        emb = np.array(raw, dtype=np.float32)
        results.append({
            "id": row["id"],
            "title": row["title"],
            "source": row["source"],
            "domain": row["domain"],
            "published_at": row["published_at"],
            "url": row["url"],
            "embedding": emb
        })
    return results


def save_cluster_snapshot(clusters: list):
    date = datetime.utcnow().strftime("%Y-%m-%d")
    rows = []
    for cluster in clusters:
        cluster_id = hashlib.sha256(
            f"{date}_{cluster['representative_title'][:50]}".encode()
        ).hexdigest()[:16]
        centroid = cluster.get("centroid")
        rows.append({
            "id": cluster_id,
            "date": date,
            "representative_title": cluster.get("representative_title", "")[:200],
            "size": cluster.get("size", 0),
            "sources": cluster.get("sources", []),
            "domains": cluster.get("domains", []),
            "emergence_score": cluster.get("emergence_score", 0),
            "is_new_topic": bool(cluster.get("is_new_topic")),
            "first_seen": cluster.get("first_seen", ""),
            "last_seen": cluster.get("last_seen", ""),
            "centroid": centroid.astype(np.float32).tolist() if centroid is not None else None
        })
    if rows:
        get_client().table("cluster_history").upsert(rows).execute()
    print(f"[DB] {len(rows)} clusters guardados en historial ({date})")


def load_cluster_history(days: int = 30) -> list:
    res = get_client().table("cluster_history") \
        .select("*") \
        .gte("date", f"now() - interval '{days} days'") \
        .order("date", desc=True) \
        .order("emergence_score", desc=True) \
        .execute()

    results = []
    for row in res.data:
        centroid = np.array(row["centroid"], dtype=np.float32) if row.get("centroid") else None
        results.append({
            "id": row["id"],
            "date": row["date"],
            "representative_title": row["representative_title"],
            "size": row["size"],
            "sources": row["sources"],
            "domains": row["domains"],
            "emergence_score": row["emergence_score"],
            "is_new_topic": row["is_new_topic"],
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "centroid": centroid
        })
    return results


def find_similar_historical(centroid: np.ndarray, days: int = 60, threshold: float = 0.7) -> list:
    """
    Usa el operador <=> de pgvector (distancia coseno) vía RPC en vez de calcular en Python.
    Requiere la función SQL 'match_clusters'.
    """
    res = get_client().rpc("match_clusters", {
        "query_embedding": centroid.astype(np.float32).tolist(),
        "match_threshold": threshold,
        "days_back": days
    }).execute()
    return [{**row, "similarity": round(row["similarity"], 3)} for row in res.data]


def cluster_history_stats() -> dict:
    res = get_client().table("cluster_history").select("representative_title, emergence_score, date").execute()
    rows = res.data
    total = len(rows)
    days = len(set(r["date"] for r in rows))

    from collections import defaultdict
    grouped = defaultdict(list)
    for r in rows:
        grouped[r["representative_title"]].append(r["emergence_score"])

    top = sorted(
        [{"title": k, "recurrences": len(v), "avg_score": round(sum(v)/len(v), 2)} for k, v in grouped.items()],
        key=lambda x: x["recurrences"], reverse=True
    )[:10]

    return {"total_snapshots": total, "days_tracked": days, "top_recurring": top}


def stats() -> dict:
    res = get_client().table("articles").select("source, domain").execute()
    rows = res.data
    from collections import Counter
    by_source = Counter(r["source"] for r in rows)
    by_domain = Counter(r["domain"] for r in rows)
    return {
        "total": len(rows),
        "by_source": dict(by_source),
        "by_domain": dict(by_domain)
    }


def log_alert(fecha, tipo_senal, entidad, valor_indice, nivel, precio_referencia, detalle):
    """Log de alertas para construir track record (Fase 1)."""
    get_client().table("alerts_log").insert({
        "fecha": fecha,
        "tipo_senal": tipo_senal,
        "entidad": entidad,
        "valor_indice": valor_indice,
        "nivel": nivel,
        "precio_referencia": precio_referencia,
        "detalle": detalle
    }).execute()
