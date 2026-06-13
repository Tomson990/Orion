"""
analysis/clustering.py
Fase 2 — Clustering temporal y detección de narrativas emergentes.
Agrupa artículos por similitud semántica y detecta clusters que crecen.
"""

import numpy as np
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Optional

try:
    import hdbscan
    HDBSCAN_AVAILABLE = True
except ImportError:
    HDBSCAN_AVAILABLE = False

from sklearn.cluster import AgglomerativeClustering
from sklearn.metrics.pairwise import cosine_similarity


def cluster_articles(articles: list, min_cluster_size: int = 3) -> list:
    """
    Agrupa artículos por similitud semántica.
    Retorna lista de clusters, cada uno con sus artículos y metadata.
    """
    if len(articles) < min_cluster_size:
        return []

    # Matrix de embeddings
    embeddings = np.array([a["embedding"] for a in articles])

    if HDBSCAN_AVAILABLE:
        # HDBSCAN — mejor para clusters de densidad variable
        clusterer = hdbscan.HDBSCAN(
            min_cluster_size=min_cluster_size,
            min_samples=2,
            metric="euclidean",
            cluster_selection_epsilon=0.3
        )
        labels = clusterer.fit_predict(embeddings)
    else:
        # Fallback: Agglomerative Clustering
        n_clusters = max(2, len(articles) // 8)
        clusterer = AgglomerativeClustering(
            n_clusters=n_clusters,
            metric="cosine",
            linkage="average"
        )
        labels = clusterer.fit_predict(embeddings)

    # Agrupar artículos por label
    clusters_raw = defaultdict(list)
    for i, label in enumerate(labels):
        if label == -1:
            continue  # HDBSCAN marca ruido como -1
        clusters_raw[label].append(articles[i])

    # Construir clusters con metadata
    clusters = []
    for label, members in clusters_raw.items():
        if len(members) < min_cluster_size:
            continue

        # Centroide del cluster
        centroid = np.mean([m["embedding"] for m in members], axis=0)

        # Fuentes únicas
        sources = list(set(m["source"] for m in members))
        domains = list(set(m["domain"] for m in members))

        # Ordenar por fecha
        members_sorted = sorted(
            members,
            key=lambda x: x["published_at"] or "",
            reverse=True
        )

        # Título representativo — el más cercano al centroide
        sims = cosine_similarity([centroid], [m["embedding"] for m in members])[0]
        rep_idx = int(np.argmax(sims))
        representative_title = members[rep_idx]["title"]

        clusters.append({
            "id": label,
            "size": len(members),
            "sources": sources,
            "domains": domains,
            "representative_title": representative_title,
            "articles": members_sorted,
            "centroid": centroid,
            "first_seen": min(m["published_at"] or "" for m in members),
            "last_seen": max(m["published_at"] or "" for m in members),
        })

    # Ordenar por tamaño descendente
    clusters.sort(key=lambda x: x["size"], reverse=True)
    return clusters


def detect_emerging(
    articles: list,
    window_hours: int = 24,
    lookback_hours: int = 72,
    growth_threshold: float = 0.4
) -> list:
    """
    Detecta narrativas emergentes: clusters que crecieron en las últimas `window_hours`
    respecto al período anterior.

    Retorna clusters ordenados por score de emergencia.
    """
    now = datetime.utcnow()
    cutoff_recent = now - timedelta(hours=window_hours)
    cutoff_old = now - timedelta(hours=lookback_hours)

    # Separar artículos recientes vs anteriores
    recent = []
    older = []
    for a in articles:
        if not a["published_at"]:
            continue
        try:
            pub = datetime.fromisoformat(a["published_at"].replace("Z", ""))
        except Exception:
            continue
        if pub >= cutoff_recent:
            recent.append(a)
        elif pub >= cutoff_old:
            older.append(a)

    if len(recent) < 3:
        return []

    # Cluster solo los recientes
    recent_clusters = cluster_articles(recent, min_cluster_size=2)

    # Para cada cluster reciente, buscar artículos similares en el período anterior
    emerging = []
    for cluster in recent_clusters:
        centroid = cluster["centroid"]

        # Similitud de artículos anteriores con este cluster
        older_sims = []
        for a in older:
            sim = float(np.dot(centroid, a["embedding"]))
            older_sims.append(sim)

        # Artículos anteriores relacionados (sim > 0.4)
        older_related = sum(1 for s in older_sims if s > 0.4)
        recent_count = cluster["size"]

        # Score de emergencia: cuánto creció relativo al pasado
        if older_related == 0:
            emergence_score = 1.0  # Tema completamente nuevo
        else:
            emergence_score = recent_count / (recent_count + older_related)

        # Cross-domain bonus: si cruza dominios es más interesante
        domain_bonus = 0.1 * (len(cluster["domains"]) - 1)
        source_bonus = 0.05 * (len(cluster["sources"]) - 1)

        final_score = min(1.0, emergence_score + domain_bonus + source_bonus)

        emerging.append({
            **cluster,
            "emergence_score": round(final_score, 3),
            "older_related": older_related,
            "is_new_topic": older_related == 0,
        })

    # Ordenar por score de emergencia
    emerging.sort(key=lambda x: x["emergence_score"], reverse=True)
    return emerging


def format_cluster_report(clusters: list, max_clusters: int = 10) -> str:
    """Formatea clusters para output en terminal."""
    if not clusters:
        return "No se detectaron clusters con los parámetros actuales."

    lines = []
    lines.append(f"\n{'='*60}")
    lines.append(f"  {len(clusters)} NARRATIVAS DETECTADAS")
    lines.append(f"{'='*60}\n")

    for i, c in enumerate(clusters[:max_clusters], 1):
        score = c.get("emergence_score", 0)
        is_new = c.get("is_new_topic", False)
        tag = " 🆕 TEMA NUEVO" if is_new else f" 📈 score: {score:.2f}"

        lines.append(f"{'─'*60}")
        lines.append(f"#{i} [{c['size']} artículos]{tag}")
        lines.append(f"   {c['representative_title'][:75]}")
        lines.append(f"   Fuentes: {', '.join(c['sources'][:5])}")
        lines.append(f"   Dominios: {', '.join(c['domains'])}")
        lines.append(f"   Desde: {c['first_seen'][:10]} → {c['last_seen'][:10]}")
        lines.append(f"   Artículos:")
        for a in c["articles"][:4]:
            lines.append(f"     · {a['title'][:65]}")
        if c["size"] > 4:
            lines.append(f"     · ... y {c['size']-4} más")
        lines.append("")

    return "\n".join(lines)
