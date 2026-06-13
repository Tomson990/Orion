"""
main.py
Orion — Energy Intelligence Platform
Orquestador principal del pipeline de datos e inteligencia.
Uso:
    python main.py              # pipeline completo
    python main.py --prices     # solo precios
    python main.py --news       # solo noticias
    python main.py --intel      # solo reporte de inteligencia
    python main.py --report     # mostrar reporte actual
    python main.py --briefing   # briefing narrativo + precios
"""
import argparse
import sys
from ingestion.commodities import fetch_all_commodities, get_latest_prices_simple, init_db
from ingestion.company_news import fetch_company_news, get_company_summary
from intelligence.risk_engine import generate_daily_intelligence, format_intelligence_report


def run_full_pipeline():
    print("\n◈ ORION — Energy Intelligence Platform")
    print("=" * 45)

    print("\n[1/4] Descargando precios de commodities...")
    prices = fetch_all_commodities()
    ok = len([p for p in prices if p.get("price")])
    print(f"  ✓ {ok}/{len(prices)} commodities actualizados")

    print("\n[2/4] Monitoreando noticias de compañías...")
    company_news = fetch_company_news()
    active = sum(1 for v in company_news.values() if v)
    print(f"  ✓ {active} compañías con noticias nuevas")

    print("\n[3/4] Generando inteligencia...")
    intel = generate_daily_intelligence()
    report = format_intelligence_report(intel)
    print("\n" + "=" * 45)
    print(report)
    print("=" * 45)

    print("\n[4/4] Generando briefing narrativo...")
    run_briefing()

    return intel


def run_prices():
    print("\n◈ ORION — Precios de Commodities")
    prices = fetch_all_commodities()
    print(f"\n{'Commodity':<25} {'Precio':<12} {'Cambio':<10} {'Nivel'}")
    print("─" * 60)
    for p in prices:
        if p.get("price"):
            change = f"{p['change_pct']:+.1f}%" if p.get("change_pct") else "N/A"
            level = "↑" if (p.get("change_pct") or 0) > 0 else "↓"
            print(f"{p['name']:<25} {p['price']:<12} {change:<10} {level}")


def run_news():
    print("\n◈ ORION — Noticias de Compañías")
    company_news = fetch_company_news()
    for company, articles in company_news.items():
        if articles:
            from ingestion.company_news import COMPANIES
            name = COMPANIES.get(company, {}).get("name", company)
            print(f"\n{name} ({len(articles)} noticias nuevas):")
            for a in articles[:3]:
                print(f"  · {a['title'][:80]}")


def run_report():
    print("\n◈ ORION — Reporte de Inteligencia")
    intel = generate_daily_intelligence()
    print(format_intelligence_report(intel))


def run_briefing():
    print("\n◈ ORION — Energy Briefing")
    print("=" * 45)

    print("[1/2] Verificando precios...")
    prices = get_latest_prices_simple()
    if not prices:
        print("  Sin precios en DB — descargando...")
        fetch_all_commodities()

    print("[2/2] Generando briefing narrativo...")
    import sys
    sys.path.insert(0, os.environ.get("NARRATIVE_INTEL_PATH", "/Users/tomasdelfino/Desktop/Narrative Intel/narrative_intelligence"))
    from analysis.briefing import generate_briefing, save_briefing
    briefing = generate_briefing()

    print("\n" + "=" * 45)
    print(briefing)
    print("=" * 45)

    path = save_briefing(briefing)
    print(f"\n✓ Guardado en {path}")

    return briefing


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Orion — Energy Intelligence Platform")
    parser.add_argument("--prices",   action="store_true", help="Solo precios de commodities")
    parser.add_argument("--news",     action="store_true", help="Solo noticias de compañías")
    parser.add_argument("--intel",    action="store_true", help="Solo reporte de inteligencia")
    parser.add_argument("--report",   action="store_true", help="Mostrar reporte actual")
    parser.add_argument("--briefing", action="store_true", help="Briefing narrativo + precios")
    args = parser.parse_args()

    if args.prices:
        run_prices()
    elif args.news:
        run_news()
    elif args.intel or args.report:
        run_report()
    elif args.briefing:
        run_briefing()
    else:
        run_full_pipeline()
