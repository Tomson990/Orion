"""
analysis/actor_graph.py
Extrae actores (personas, organizaciones, lugares) de los artículos
y construye un grafo de co-ocurrencia con tono.
"""

import spacy
import numpy as np
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
import json
import sqlite3

# Cargar modelo multilingüe
_nlp = None

def get_nlp():
    global _nlp
    if _nlp is None:
        try:
            _nlp = spacy.load("es_core_news_sm")
        except OSError:
            try:
                _nlp = spacy.load("en_core_web_sm")
            except OSError:
                raise RuntimeError("Instalá un modelo spacy: python -m spacy download es_core_news_sm")
    return _nlp


# Actores a ignorar (demasiado genéricos)
STOPACTORS = {
    "reuters", "bloomberg", "afp", "ap", "efe", "dpa",
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "lunes", "martes", "miércoles", "jueves", "viernes",
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
}

# Palabras positivas y negativas para tono simple
POSITIVE_WORDS = {"acuerdo", "cooperación", "apoyo", "alianza", "paz", "crecimiento",
                  "agreement", "cooperation", "support", "alliance", "peace", "growth",
                  "aprobó", "firmó", "celebró", "logró", "ganó"}
NEGATIVE_WORDS = {"conflicto", "guerra", "sanción", "acusación", "crisis", "colapso",
                  "conflict", "war", "sanction", "accusation", "crisis", "collapse",
                  "atacó", "rechazó", "denunció", "condenó", "perdió"}


def extract_actors(text: str, max_actors: int = 8) -> list:
    """Extrae entidades nombradas del texto."""
    nlp = get_nlp()
    doc = nlp(text[:2000])  # limitar para velocidad

    actors = []
    seen = set()

    for ent in doc.ents:
        if ent.label_ in ("PER", "ORG", "GPE", "LOC", "PERSON", "ORG", "NORP"):
            name = ent.text.strip()
            name_lower = name.lower()

            # Filtros
            if len(name) < 3:
                continue
            if name_lower in STOPACTORS:
                continue
            if any(c.isdigit() for c in name):
                continue
            if name_lower in seen:
                continue

            seen.add(name_lower)
            actors.append({
                "name": name,
                "type": ent.label_,
                "start": ent.start_char,
                "end": ent.end_char
            })

            if len(actors) >= max_actors:
                break

    return actors


def get_tone(text: str) -> float:
    """Tono simple: positivo (+1), negativo (-1), neutral (0)."""
    text_lower = text.lower()
    pos = sum(1 for w in POSITIVE_WORDS if w in text_lower)
    neg = sum(1 for w in NEGATIVE_WORDS if w in text_lower)
    if pos + neg == 0:
        return 0.0
    return (pos - neg) / (pos + neg)


def build_actor_graph(articles: list, window_hours: int = 48) -> dict:
    """
    Construye el grafo de actores a partir de artículos recientes.
    
    Returns:
        dict con 'nodes' y 'edges'
    """
    now = datetime.utcnow()
    cutoff = now - timedelta(hours=window_hours)

    # Filtrar artículos recientes
    recent = []
    for a in articles:
        if not a.get("published_at"):
            continue
        try:
            pub = datetime.fromisoformat(a["published_at"].replace("Z", ""))
            if pub >= cutoff:
                recent.append(a)
        except Exception:
            continue

    if not recent:
        # Usar todos si no hay recientes
        recent = articles[:200]

    print(f"[ActorGraph] Procesando {len(recent)} artículos...")

    # Contar actores y co-ocurrencias
    actor_count = defaultdict(int)
    actor_sources = defaultdict(set)
    actor_domains = defaultdict(set)
    co_occurrence = defaultdict(lambda: {"count": 0, "tone_sum": 0.0})

    for article in recent:
        text = f"{article.get('title', '')} {article.get('body', '')}"
        actors = extract_actors(text)
        tone = get_tone(text)
        source = article.get("source", "")
        domain = article.get("domain", "")

        for actor in actors:
            name = actor["name"]
            actor_count[name] += 1
            actor_sources[name].add(source)
            actor_domains[name].add(domain)

        # Co-ocurrencias entre pares de actores en el mismo artículo
        for i, a1 in enumerate(actors):
            for a2 in actors[i+1:]:
                pair = tuple(sorted([a1["name"], a2["name"]]))
                co_occurrence[pair]["count"] += 1
                co_occurrence[pair]["tone_sum"] += tone

    # Filtrar actores con al menos 2 menciones
    top_actors = {name: count for name, count in actor_count.items() if count >= 2}

    if not top_actors:
        # Bajar umbral si hay pocos
        top_actors = dict(sorted(actor_count.items(), key=lambda x: x[1], reverse=True)[:20])

    # Construir nodos
    max_count = max(top_actors.values(), default=1)
    nodes = []
    for name, count in sorted(top_actors.items(), key=lambda x: x[1], reverse=True)[:40]:
        nodes.append({
            "id": name,
            "label": name,
            "count": count,
            "size": 8 + (count / max_count) * 20,
            "sources": list(actor_sources[name])[:5],
            "domains": list(actor_domains[name]),
        })

    node_names = {n["id"] for n in nodes}

    # Construir aristas
    edges = []
    for (a1, a2), data in co_occurrence.items():
        if a1 not in node_names or a2 not in node_names:
            continue
        if data["count"] < 1:
            continue

        tone_avg = data["tone_sum"] / data["count"] if data["count"] > 0 else 0
        edges.append({
            "source": a1,
            "target": a2,
            "count": data["count"],
            "tone": round(tone_avg, 2),
            "width": min(1 + data["count"] * 0.5, 6)
        })

    # Ordenar por peso
    edges.sort(key=lambda x: x["count"], reverse=True)

    print(f"[ActorGraph] {len(nodes)} actores, {len(edges)} conexiones")

    return {
        "nodes": nodes,
        "edges": edges[:100],  # limitar edges para visualización
        "generated_at": datetime.utcnow().isoformat(),
        "articles_processed": len(recent),
        "window_hours": window_hours
    }
