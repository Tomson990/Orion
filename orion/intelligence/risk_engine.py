"""
intelligence/risk_engine.py
Motor de inteligencia de Orion.
Calcula el índice de presión de costos y el score de riesgo global.
"""

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from ingestion.commodities import get_conn, get_latest_prices_simple, COMMODITIES
from ingestion.company_news import get_recent_company_news, get_company_summary, COMPANIES

# Umbrales de cambio para alertas (% semanal)
ALERT_THRESHOLDS = {
    "brent":      {"low": 3, "medium": 7, "high": 12},
    "ttf":        {"low": 5, "medium": 10, "high": 18},
    "henry_hub":  {"low": 5, "medium": 10, "high": 18},
    "copper":     {"low": 3, "medium": 6, "high": 10},
    "bdi":        {"low": 5, "medium": 10, "high": 20},
    "usd_ars":    {"low": 2, "medium": 5, "high": 10},
    "default":    {"low": 3, "medium": 7, "high": 12},
}

# Peso de cada commodity en el índice de presión
COST_WEIGHTS = {
    "brent":      0.25,
    "ttf":        0.20,
    "henry_hub":  0.15,
    "copper":     0.12,
    "bdi":        0.10,
    "usd_ars":    0.10,
    "aluminium":  0.05,
    "steel":      0.03,
}


def get_signal_level(commodity: str, change_pct: float) -> str:
    """Determina nivel de alerta para un commodity."""
    if change_pct is None:
        return "neutral"
    thresholds = ALERT_THRESHOLDS.get(commodity, ALERT_THRESHOLDS["default"])
    abs_change = abs(change_pct)
    if abs_change >= thresholds["high"]:
        return "high"
    elif abs_change >= thresholds["medium"]:
        return "medium"
    elif abs_change >= thresholds["low"]:
        return "low"
    return "neutral"


def calculate_cost_pressure_index(prices: list) -> dict:
    """
    Calcula el índice de presión de costos (0-100).
    Combina cambios ponderados de commodities clave.
    """
    price_dict = {p["commodity"]: p for p in prices}

    weighted_pressure = 0.0
    total_weight = 0.0
    signals = []

    for commodity, weight in COST_WEIGHTS.items():
        if commodity not in price_dict:
            continue

        p = price_dict[commodity]
        change = p.get("change_pct")

        if change is None:
            continue

        # Presión normalizada: cambio positivo = más presión (excepto FX inverso)
        if commodity == "usd_ars":
            pressure = max(0, change) / 10  # devaluación = más presión
        else:
            pressure = change / 10  # normalizado

        weighted_pressure += pressure * weight
        total_weight += weight

        level = get_signal_level(commodity, change)
        if level in ("high", "medium"):
            signals.append({
                "commodity": COMMODITIES.get(commodity, {}).get("name", commodity),
                "change_pct": change,
                "level": level,
                "unit": p.get("unit", "")
            })

    # Índice 0-100
    if total_weight > 0:
        raw = weighted_pressure / total_weight
        index = min(100, max(0, 50 + raw * 50))
    else:
        index = 50

    # Nivel global
    if index >= 70:
        level = "HIGH"
        emoji = "🔴"
    elif index >= 50:
        level = "MEDIUM"
        emoji = "🟡"
    else:
        level = "LOW"
        emoji = "🟢"

    return {
        "index": round(index, 1),
        "level": level,
        "emoji": emoji,
        "signals": sorted(signals, key=lambda x: abs(x["change_pct"]), reverse=True),
        "calculated_at": datetime.utcnow().isoformat()
    }


def analyze_company_risk(company_summary: dict) -> list:
    """
    Analiza riesgo por compañía basado en volumen de noticias recientes.
    Más noticias recientes = más actividad = potencial señal.
    """
    company_risks = []

    for key, data in company_summary.items():
        info = COMPANIES.get(key, {})
        count = data["count"]

        # Score simple basado en volumen
        if count >= 10:
            risk_level = "HIGH"
        elif count >= 5:
            risk_level = "MEDIUM"
        elif count >= 2:
            risk_level = "LOW"
        else:
            continue

        # Noticias recientes
        recent = get_recent_company_news(key)[:3]

        company_risks.append({
            "company": key,
            "name": info.get("name", key),
            "region": info.get("region", "global"),
            "news_count": count,
            "risk_level": risk_level,
            "recent_headlines": [n["title"] for n in recent],
        })

    company_risks.sort(key=lambda x: x["news_count"], reverse=True)
    return company_risks


def generate_daily_intelligence() -> dict:
    """
    Genera el reporte de inteligencia diario completo.
    """
    print("[Orion] Generando inteligencia diaria...")

    # 1. Precios actuales
    prices = get_latest_prices_simple()
    print(f"  {len(prices)} commodities cargados")

    # 2. Índice de presión de costos
    cost_pressure = calculate_cost_pressure_index(prices)
    print(f"  Índice de presión: {cost_pressure['index']} ({cost_pressure['level']})")

    # 3. Análisis de compañías
    company_summary = get_company_summary()
    company_risks = analyze_company_risk(company_summary)
    print(f"  {len(company_risks)} compañías con actividad de riesgo")

    # 4. Top señales
    top_signals = cost_pressure["signals"][:5]

    # Guardar en DB
    conn = get_conn()
    conn.execute("""
        INSERT INTO risk_scores (date, sector, score, level, signals)
        VALUES (?, ?, ?, ?, ?)
    """, (
        datetime.utcnow().strftime("%Y-%m-%d"),
        "energia",
        cost_pressure["index"],
        cost_pressure["level"],
        json.dumps(top_signals)
    ))
    conn.commit()
    conn.close()

    return {
        "date": datetime.utcnow().strftime("%Y-%m-%d"),
        "cost_pressure": cost_pressure,
        "prices": prices,
        "company_risks": company_risks[:10],
        "top_signals": top_signals,
        "generated_at": datetime.utcnow().isoformat()
    }


def format_intelligence_report(intel: dict) -> str:
    """Formatea el reporte para texto (Telegram/briefing)."""
    cp = intel["cost_pressure"]
    date = intel["date"]

    lines = [
        f"⚡ ORION — Energy Intelligence",
        f"📅 {date}",
        f"━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"ÍNDICE DE PRESIÓN DE COSTOS: {cp['emoji']} {cp['index']:.0f}/100 ({cp['level']})",
        f"",
    ]

    if cp["signals"]:
        lines.append("SEÑALES ACTIVAS:")
        for s in cp["signals"][:5]:
            arrow = "↑" if s["change_pct"] > 0 else "↓"
            lines.append(f"  {arrow} {s['commodity']}: {s['change_pct']:+.1f}% [{s['level'].upper()}]")

    lines.append("")
    lines.append("COMPAÑÍAS CON ACTIVIDAD:")
    for c in intel["company_risks"][:5]:
        emoji = "🔴" if c["risk_level"] == "HIGH" else "🟡" if c["risk_level"] == "MEDIUM" else "🟢"
        lines.append(f"  {emoji} {c['name']} — {c['news_count']} noticias recientes")

    return "\n".join(lines)
