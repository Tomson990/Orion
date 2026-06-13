"""
storage/db.py
Base de datos SQLite para el sistema de inteligencia narrativa.
Persiste artículos y sus embeddings.
"""

import sqlite3
import hashlib
import numpy as np
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "narratives.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Crea las tablas si no existen."""
    conn = get_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS articles (
            id           TEXT PRIMARY KEY,
            url          TEXT NOT NULL,
            title        TEXT,
            body         TEXT,
            source       TEXT,
            domain       TEXT,
            published_at TEXT,
            ingested_at  TEXT,
            embedding    BLOB
        );

        CREATE INDEX IF NOT EXISTS idx_source      ON articles(source);
        CREATE INDEX IF NOT EXISTS idx_domain      ON articles(domain);
        CREATE INDEX IF NOT EXISTS idx_published   ON articles(published_at);

        CREATE TABLE IF NOT EXISTS cluster_history (
            id                  TEXT PRIMARY KEY,
            date                TEXT NOT NULL,
            representative_title TEXT,
            size                INTEGER,
            sources             TEXT,
            domains             TEXT,
            emergence_score     REAL,
            is_new_topic        INTEGER,
            first_seen          TEXT,
            last_seen           TEXT,
            centroid            BLOB
        );

        CREATE INDEX IF NOT EXISTS idx_cluster_date ON cluster_history(date);
    """)
    conn.commit()
    conn.close()
    print(f"[DB] Inicializada en {DB_PATH}")


def article_exists(url: str) -> bool:
    article_id = hashlib.sha256(url.encode()).hexdigest()[:16]
    conn = get_connection()
    row = conn.execute(
        "SELECT 1 FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    conn.close()
    return row is not None


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
    embedding_blob = embedding.astype(np.float32).tobytes()

    conn = get_connection()
    conn.execute("""
        INSERT OR IGNORE INTO articles
            (id, url, title, body, source, domain, published_at, ingested_at, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        article_id,
        url,
        title,
        body[:2000],          # limitamos body para no inflar la DB
        source,
        domain,
        published_at,
        datetime.utcnow().isoformat(),
        embedding_blob
    ))
    conn.commit()
    conn.close()


def load_embeddings(domain: Optional[str] = None, limit: int = 5000):
    """
    Retorna lista de dicts con id, title, source, domain, published_at, embedding (np.ndarray).
    Útil para Fase 2 (clustering).
    """
    conn = get_connection()
    if domain:
        rows = conn.execute(
            "SELECT * FROM articles WHERE domain = ? ORDER BY published_at DESC LIMIT ?",
            (domain, limit)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM articles ORDER BY published_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    conn.close()

    results = []
    for row in rows:
        emb = np.frombuffer(row["embedding"], dtype=np.float32)
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
    """
    Persiste los clusters del día en cluster_history.
    Permite comparación histórica entre corridas.
    """
    conn = get_connection()
    date = datetime.utcnow().strftime("%Y-%m-%d")

    for cluster in clusters:
        cluster_id = hashlib.sha256(
            f"{date}_{cluster['representative_title'][:50]}".encode()
        ).hexdigest()[:16]

        centroid_blob = cluster["centroid"].astype(np.float32).tobytes() if cluster.get("centroid") is not None else None

        conn.execute("""
            INSERT OR IGNORE INTO cluster_history
                (id, date, representative_title, size, sources, domains,
                 emergence_score, is_new_topic, first_seen, last_seen, centroid)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            cluster_id,
            date,
            cluster.get("representative_title", "")[:200],
            cluster.get("size", 0),
            json.dumps(cluster.get("sources", [])),
            json.dumps(cluster.get("domains", [])),
            cluster.get("emergence_score", 0),
            1 if cluster.get("is_new_topic") else 0,
            cluster.get("first_seen", ""),
            cluster.get("last_seen", ""),
            centroid_blob
        ))

    conn.commit()
    conn.close()
    print(f"[DB] {len(clusters)} clusters guardados en historial ({date})")


def load_cluster_history(days: int = 30) -> list:
    """
    Carga el historial de clusters de los últimos N días.
    Retorna lista de dicts con metadata de cada cluster histórico.
    """
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM cluster_history
        WHERE date >= date('now', ?)
        ORDER BY date DESC, emergence_score DESC
    """, (f"-{days} days",)).fetchall()
    conn.close()

    results = []
    for row in rows:
        centroid = None
        if row["centroid"]:
            centroid = np.frombuffer(row["centroid"], dtype=np.float32)
        results.append({
            "id": row["id"],
            "date": row["date"],
            "representative_title": row["representative_title"],
            "size": row["size"],
            "sources": json.loads(row["sources"]),
            "domains": json.loads(row["domains"]),
            "emergence_score": row["emergence_score"],
            "is_new_topic": bool(row["is_new_topic"]),
            "first_seen": row["first_seen"],
            "last_seen": row["last_seen"],
            "centroid": centroid
        })
    return results


def find_similar_historical(centroid: np.ndarray, days: int = 60, threshold: float = 0.7) -> list:
    """
    Dado el centroide de un cluster actual, busca clusters históricos similares.
    Retorna lista de clusters históricos ordenados por similitud.
    """
    history = load_cluster_history(days=days)
    similar = []

    for h in history:
        if h["centroid"] is None:
            continue
        sim = float(np.dot(centroid / np.linalg.norm(centroid),
                           h["centroid"] / np.linalg.norm(h["centroid"])))
        if sim >= threshold:
            similar.append({**h, "similarity": round(sim, 3)})

    similar.sort(key=lambda x: x["similarity"], reverse=True)
    return similar


def cluster_history_stats() -> dict:
    """Estadísticas del historial de clusters."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM cluster_history").fetchone()[0]
    days = conn.execute("SELECT COUNT(DISTINCT date) FROM cluster_history").fetchone()[0]
    top = conn.execute("""
        SELECT representative_title, COUNT(*) as recurrences, AVG(emergence_score) as avg_score
        FROM cluster_history
        GROUP BY representative_title
        ORDER BY recurrences DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    return {
        "total_snapshots": total,
        "days_tracked": days,
        "top_recurring": [{"title": r["representative_title"], "recurrences": r["recurrences"], "avg_score": round(r["avg_score"], 2)} for r in top]
    }


def stats() -> dict:
    """Estadísticas rápidas de la DB."""
    conn = get_connection()
    total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) as n FROM articles GROUP BY source ORDER BY n DESC"
    ).fetchall()
    by_domain = conn.execute(
        "SELECT domain, COUNT(*) as n FROM articles GROUP BY domain ORDER BY n DESC"
    ).fetchall()
    conn.close()
    return {
        "total": total,
        "by_source": {r["source"]: r["n"] for r in by_source},
        "by_domain": {r["domain"]: r["n"] for r in by_domain}
    }
