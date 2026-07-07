"""
analysis/briefing.py
Fase 3 — Briefing diario con contexto histórico desde Supabase.
Integrado con ORION para incluir precios de commodities energéticos.
"""

import os
import sys
import httpx
from datetime import datetime
from analysis.clustering import detect_emerging, cluster_articles
from storage.db import load_embeddings

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")


def get_prices_for_prompt() -> dict:
    """Convierte precios de ORION al formato que espera build_prompt()."""
    try:
        from ingestion.commodities import get_latest_prices_simple
        rows = get_latest_prices_simple()
        prices = {}
        for r in rows:
            if r["price"] and r["category"] in ("energia", "fletes"):
                prices[r["commodity"]] = {
                    "price": r["price"],
                    "unit": r["unit"],
                    "change_pct": r["change_pct"] or 0.0,
                    "name": r["commodity"].replace("_", " ").title()
                }
        return prices
    except Exception as e:
        print(f"[Briefing] Precios no disponibles: {e}")
        return {}


def get_historical_context(clusters: list) -> str:
    """
    Busca antecedentes históricos en Supabase para cada cluster actual.
    Si no hay Supabase, cae a la búsqueda local.
    """
    context_lines = []
    supabase_ok = bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_KEY"))

    for i, c in enumerate(clusters[:6], 1):
        title = c.get("representative_title", "")
        found = False

        if supabase_ok:
            try:
                from storage.supabase_sync import find_similar_in_history
                similar = find_similar_in_history(title, days=60, limit=3)
                if similar:
                    hoy = datetime.utcnow().strftime("%Y-%m-%d")
                    prev_list = [s for s in similar if s.get("date") != hoy]
                    if prev_list:
                        prev = prev_list[0]
                        recurrencias = len(prev_list)
                        context_lines.append(
                            f"Narrativa #{i}: reapareció. Última vez el {prev['date']} "
                            f"({prev.get('size', 0)} artículos, score {prev.get('emergence_score', 0):.2f}). "
                            f"Recurrencias en 60 días: {recurrencias}. "
                            f"Título previo: '{prev.get('representative_title', '')[:60]}'"
                        )
                        found = True
            except Exception as e:
                print(f"[Briefing] Error buscando historial Supabase: {e}")

        if not found:
            try:
                from storage.db import find_similar_historical
                centroid = c.get("centroid")
                if centroid is not None:
                    similar_local = find_similar_historical(centroid, days=60, threshold=0.72)
                    hoy = datetime.utcnow().strftime("%Y-%m-%d")
                    prev_list = [s for s in similar_local if s.get("date") != hoy]
                    if prev_list:
                        prev = prev_list[0]
                        context_lines.append(
                            f"Narrativa #{i}: reapareció el {prev['date']} "
                            f"({prev.get('size', 0)} artículos). "
                            f"Título previo: '{prev.get('representative_title', '')[:60]}'"
                        )
                        found = True
            except Exception:
                pass

        if not found:
            context_lines.append(f"Narrativa #{i}: primera vez detectada en los últimos 60 días.")

    return "\n".join(context_lines)


def build_prompt(clusters: list, prices: dict = None) -> str:
    """Construye el prompt para Claude con clusters energéticos y precios."""
    if not clusters:
        return ""

    hoy = datetime.now().strftime("%d de %B de %Y")
    historical_context = get_historical_context(clusters)

    # Bloque de precios
    prices_text = ""
    if prices:
        prices_text = "PRECIOS CLAVE HOY:\n"
        for commodity, data in prices.items():
            cambio = data.get("change_pct", 0)
            signo = "▲" if cambio > 0 else "▼" if cambio < 0 else "—"
            prices_text += f"  {data.get('name', commodity)}: {data['price']} {data['unit']} {signo} {abs(cambio):.1f}%\n"

    clusters_text = ""
    for i, c in enumerate(clusters[:6], 1):
        articulos = "\n".join(
            f"  - [{a['source']}] {a['title'][:80]}"
            for a in c["articles"][:5]
        )
        clusters_text += f"""
NARRATIVA #{i}
Artículos: {c['size']} | Fuentes: {', '.join(c['sources'][:4])}
Score emergencia: {c.get('emergence_score', 0):.2f} | Tema nuevo: {'Sí' if c.get('is_new_topic') else 'No'}
Título representativo: {c['representative_title'][:80]}
Artículos principales:
{articulos}
---"""

    from analysis.contradictions import detect_all_contradictions, format_contradiction_for_briefing
    contradictions = detect_all_contradictions(clusters[:8])
    contradiction_text = format_contradiction_for_briefing(contradictions)

    return f"""Sos un analista de inteligencia energética global.
Fecha de hoy: {hoy}

{prices_text}

Se detectaron las siguientes narrativas emergentes en medios especializados de energía global en las últimas 24-48 horas:

{clusters_text}

CONTEXTO HISTÓRICO (últimos 60 días):
{historical_context}

{contradiction_text}

Generá un briefing de inteligencia energética con este formato exacto:

ORION ENERGY INTELLIGENCE — {hoy}
================================

PULSO DE MERCADO
[2-3 oraciones conectando los movimientos de precio del día con el contexto narrativo]

[Para cada narrativa relevante:]

## [NÚMERO]. [TÍTULO CONCISO] — [NIVEL: ALTA/MEDIA/BAJA]

**Qué está pasando:** [2-3 oraciones]
**Impacto en precios:** [Cómo se relaciona con los movimientos del día, si aplica]
**Historial:** [Si reapareció o es nueva]
**Tensión narrativa:** [Contradicciones entre fuentes si las hay]
**Señal vs ruido:** [1 oración]

---

TENSIONES ACTIVAS
[Contradicciones detectadas entre fuentes]

---

SÍNTESIS EJECUTIVA
[3-4 oraciones sobre el estado del mercado energético global hoy]

Sé directo, específico y accionable."""


def generate_briefing(window_hours: int = 48, domain: str = None) -> str:
    """Genera el briefing completo usando Claude API."""

    if not ANTHROPIC_API_KEY:
        return "[ERROR] Falta ANTHROPIC_API_KEY."

    print("[Briefing] Cargando artículos...")
    articles = load_embeddings(domain=domain, limit=3000)

    if not articles:
        return "[ERROR] No hay artículos en la base."

    print(f"[Briefing] {len(articles)} artículos. Detectando narrativas emergentes...")
    emerging = detect_emerging(articles, window_hours=window_hours)

    if not emerging:
        print("[Briefing] Usando clustering general...")
        emerging = cluster_articles(articles, min_cluster_size=3)[:8]
        for c in emerging:
            c["emergence_score"] = 0.5
            c["is_new_topic"] = False

    if not emerging:
        return "[INFO] No hay suficientes datos."

    print("[Briefing] Cargando precios de ORION...")
    prices = get_prices_for_prompt()
    if prices:
        print(f"[Briefing] {len(prices)} commodities cargados")
    else:
        print("[Briefing] Sin precios — continuando sin ellos")

    print(f"[Briefing] {len(emerging)} narrativas. Consultando Claude API...")
    prompt = build_prompt(emerging, prices=prices)

    try:
        response = httpx.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-5",
                "max_tokens": 2000,
                "messages": [{"role": "user", "content": prompt}]
            },
            timeout=60.0
        )
        response.raise_for_status()
        data = response.json()
        return data["content"][0]["text"]

    except httpx.HTTPStatusError as e:
        return f"[ERROR API] {e.response.status_code}: {e.response.text}"
    except Exception as e:
        return f"[ERROR] {e}"


def save_briefing(text: str):
    """Guarda el briefing en archivo con fecha."""
    os.makedirs("briefings", exist_ok=True)
    fecha = datetime.now().strftime("%Y-%m-%d")
    path = f"briefings/briefing_{fecha}.txt"
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[Briefing] Guardado en {path}")
    return path
