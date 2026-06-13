"""
analysis/contradictions.py
Detecta contradicciones narrativas — cuando fuentes distintas reportan
el mismo tema con framing opuesto.

Lógica:
1. Toma clusters semánticos existentes
2. Para cada cluster, analiza el tono de cada artículo
3. Si hay artículos con tono muy distinto dentro del mismo cluster → contradicción
4. Clasifica la contradicción por tipo y severidad
"""

import numpy as np
from collections import defaultdict
from typing import Optional

# Palabras de tono positivo
POSITIVE = {
    # Español
    "acuerdo", "firmó", "logró", "avance", "crecimiento", "récord", "éxito",
    "aprobó", "ganó", "superó", "paz", "cooperación", "apoyo", "recuperación",
    "optimismo", "confianza", "estabilidad", "mejora", "aumento", "positivo",
    # Inglés
    "agreement", "signed", "achieved", "growth", "record", "success",
    "approved", "won", "peace", "cooperation", "recovery", "optimism",
    "confidence", "stability", "improvement", "positive", "gains", "rally"
}

NEGATIVE = {
    # Español
    "crisis", "colapso", "fracasó", "rechazó", "conflicto", "guerra", "caída",
    "devaluación", "default", "quiebra", "protesta", "violencia", "tensión",
    "preocupación", "riesgo", "deterioro", "pérdida", "negativo", "alerta",
    "rompió", "amenaza", "sanción", "condena", "ataque", "denunció",
    # Inglés
    "crisis", "collapse", "failed", "rejected", "conflict", "war", "fall",
    "devaluation", "default", "bankruptcy", "protest", "violence", "tension",
    "concern", "risk", "deterioration", "loss", "negative", "alert",
    "broke", "threat", "sanction", "condemned", "attack", "accused"
}

HEDGE_WORDS = {
    "podría", "podría ser", "posible", "quizás", "tal vez", "según",
    "could", "might", "possible", "perhaps", "according to", "alleged"
}


def get_article_tone(title: str, body: str = "") -> float:
    """
    Calcula tono de un artículo: -1 (muy negativo) a +1 (muy positivo).
    Pondera título más que cuerpo.
    """
    text_title = title.lower()
    text_body = (body or "").lower()[:500]

    # Contar en título (peso 2x)
    pos_title = sum(2 for w in POSITIVE if w in text_title)
    neg_title = sum(2 for w in NEGATIVE if w in text_title)

    # Contar en cuerpo (peso 1x)
    pos_body = sum(1 for w in POSITIVE if w in text_body)
    neg_body = sum(1 for w in NEGATIVE if w in text_body)

    pos = pos_title + pos_body
    neg = neg_title + neg_body

    if pos + neg == 0:
        return 0.0

    return round((pos - neg) / (pos + neg), 3)


def detect_contradictions_in_cluster(cluster: dict, min_tone_gap: float = 0.4) -> Optional[dict]:
    """
    Detecta si hay contradicción dentro de un cluster.
    
    Una contradicción existe cuando:
    - Hay artículos con tono > +0.2 Y artículos con tono < -0.2
    - La diferencia de tono entre los más positivos y más negativos es >= min_tone_gap
    - Las fuentes contradictorias son distintas (no el mismo medio)
    
    Retorna dict con la contradicción o None si no hay.
    """
    articles = cluster.get("articles", [])
    if len(articles) < 2:
        return None

    # Calcular tono de cada artículo
    toned = []
    for a in articles:
        tone = get_article_tone(a.get("title", ""), a.get("body", ""))
        toned.append({**a, "tone": tone})

    # Separar positivos y negativos
    positives = [a for a in toned if a["tone"] > 0.15]
    negatives = [a for a in toned if a["tone"] < -0.15]

    if not positives or not negatives:
        return None

    # Verificar que vienen de fuentes distintas
    pos_sources = {a.get("source", "") for a in positives}
    neg_sources = {a.get("source", "") for a in negatives}

    if pos_sources == neg_sources:
        return None  # Mismo medio contradiciéndose → menos interesante

    # Calcular gap de tono
    max_pos = max(a["tone"] for a in positives)
    min_neg = min(a["tone"] for a in negatives)
    tone_gap = max_pos - min_neg

    if tone_gap < min_tone_gap:
        return None

    # Clasificar tipo de contradicción
    contradiction_type = classify_contradiction(positives, negatives)

    # Severidad
    if tone_gap > 0.8:
        severity = "ALTA"
    elif tone_gap > 0.5:
        severity = "MEDIA"
    else:
        severity = "BAJA"

    return {
        "cluster_title": cluster.get("representative_title", "")[:100],
        "topic": cluster.get("representative_title", "")[:60],
        "type": contradiction_type,
        "severity": severity,
        "tone_gap": round(tone_gap, 3),
        "positive_side": {
            "articles": positives[:3],
            "sources": list(pos_sources)[:4],
            "avg_tone": round(np.mean([a["tone"] for a in positives]), 3)
        },
        "negative_side": {
            "articles": negatives[:3],
            "sources": list(neg_sources)[:4],
            "avg_tone": round(np.mean([a["tone"] for a in negatives]), 3)
        },
        "domains": cluster.get("domains", []),
        "total_articles": len(articles)
    }


def classify_contradiction(positives: list, negatives: list) -> str:
    """Clasifica el tipo de contradicción según las fuentes."""
    pos_sources = {a.get("source", "") for a in positives}
    neg_sources = {a.get("source", "") for a in negatives}

    # Detectar si es geográfico (medios de distintas regiones)
    geo_indicators = {
        "western": {"nyt_world", "wsj_world", "bloomberg", "ft_world", "guardian_world",
                   "bbc_world", "economist", "ft_emerging"},
        "latam": {"infobae", "lanacion", "ambito", "clarin", "perfil", "mercopress",
                 "batimes", "folha_sp", "eltiempo_co"},
        "asia": {"scmp_world", "scmp_economy", "nikkei_asia", "straits_times",
                "channelnewsasia", "economic_times"},
        "mideast": {"aljazeera_english", "middleeast_eye"},
        "russia": {"moscow_times"}
    }

    def get_region(sources):
        for region, indicators in geo_indicators.items():
            if sources & indicators:
                return region
        return "other"

    pos_region = get_region(pos_sources)
    neg_region = get_region(neg_sources)

    if pos_region != neg_region and pos_region != "other" and neg_region != "other":
        return "GEOPOLÍTICO"

    # Detectar si es oficial vs independiente
    official_sources = {"telam", "bcra_comunicados", "indec_noticias", "secretaria_energia"}
    if pos_sources & official_sources or neg_sources & official_sources:
        return "OFICIAL vs INDEPENDIENTE"

    # Detectar si es especializado vs generalista
    specialized = {"energiaynegocios", "ecojournal", "oilprice", "energy_monitor",
                  "freightwaves", "portalportuario"}
    if (pos_sources & specialized) != (neg_sources & specialized):
        return "ESPECIALIZADO vs GENERALISTA"

    return "NARRATIVO"


def detect_all_contradictions(clusters: list, min_tone_gap: float = 0.35) -> list:
    """
    Detecta contradicciones en todos los clusters.
    Retorna lista ordenada por severidad.
    """
    contradictions = []

    for cluster in clusters:
        contradiction = detect_contradictions_in_cluster(cluster, min_tone_gap)
        if contradiction:
            contradictions.append(contradiction)

    # Ordenar: primero ALTA severidad, luego por gap de tono
    severity_order = {"ALTA": 0, "MEDIA": 1, "BAJA": 2}
    contradictions.sort(
        key=lambda x: (severity_order.get(x["severity"], 3), -x["tone_gap"])
    )

    return contradictions


def format_contradiction_for_briefing(contradictions: list) -> str:
    """Formatea las contradicciones para incluir en el briefing."""
    if not contradictions:
        return ""

    lines = ["\n\nTENSIONES NARRATIVAS DETECTADAS\n" + "─" * 40]

    for c in contradictions[:3]:
        lines.append(f"""
⚡ [{c['severity']}] {c['topic']}
   Tipo: {c['type']} | Gap de tono: {c['tone_gap']:.2f}
   Framing positivo: {', '.join(c['positive_side']['sources'][:3])}
   Framing negativo: {', '.join(c['negative_side']['sources'][:3])}""")

    return "\n".join(lines)
