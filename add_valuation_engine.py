from pathlib import Path

server_path = Path('server.py')
index_path = Path('index.html')
readme_path = Path('README.md')

for path in (server_path, index_path):
    if not path.exists():
        raise SystemExit(f'ERRO: {path} não encontrado.')

server = server_path.read_text(encoding='utf-8')
html = index_path.read_text(encoding='utf-8')

if 'def build_stock_valuation_snapshot' in server or 'id="stockValuationPanel"' in html:
    print('O Valuation Engine já está instalado.')
    raise SystemExit(0)

# ------------------------------------------------------------------
# Backend
# ------------------------------------------------------------------
server = server.replace(
    'FRAMEWORK_ENGINE_VERSION = "0.7"',
    'FRAMEWORK_ENGINE_VERSION = "0.8"',
    1,
)

valuation_backend = r'''

def valuation_rule(
    rule_id: str,
    label: str,
    value,
    unit: str,
    points: int,
    max_points: int,
    status: str,
    interpretation: str,
) -> dict:
    return {
        "id": rule_id,
        "label": label,
        "value": value,
        "unit": unit,
        "points": points,
        "max_points": max_points,
        "status": status,
        "interpretation": interpretation,
        "source": "ThesisOS calculation from Finnhub + SEC EDGAR",
    }


def unavailable_valuation_rule(
    rule_id: str,
    label: str,
    unit: str,
    max_points: int,
    interpretation: str,
) -> dict:
    return valuation_rule(
        rule_id,
        label,
        None,
        unit,
        0,
        max_points,
        "unavailable",
        interpretation,
    )


def classify_valuation_score(score):
    if score is None:
        return {
            "code": "insufficient_data",
            "label": "Dados insuficientes",
        }
    if score >= 80:
        return {
            "code": "low_relative_demand",
            "label": "Exigência relativa baixa",
        }
    if score >= 65:
        return {
            "code": "moderate_relative_demand",
            "label": "Exigência relativa moderada",
        }
    if score >= 45:
        return {
            "code": "high_relative_demand",
            "label": "Exigência relativa elevada",
        }
    return {
        "code": "very_high_relative_demand",
        "label": "Exigência relativa muito elevada",
    }


def discounted_fcf_equity_value(
    annual_fcf: float,
    growth_rate: float,
    discount_rate: float,
    terminal_growth_rate: float,
    years: int = 5,
):
    if annual_fcf <= 0:
        return None
    if discount_rate <= terminal_growth_rate:
        return None

    value = 0.0
    projected_fcf = annual_fcf

    for year in range(1, years + 1):
        projected_fcf *= 1 + growth_rate
        value += projected_fcf / ((1 + discount_rate) ** year)

    terminal_value = (
        projected_fcf
        * (1 + terminal_growth_rate)
        / (discount_rate - terminal_growth_rate)
    )
    value += terminal_value / ((1 + discount_rate) ** years)
    return value


def reverse_dcf_implied_growth(
    annual_fcf,
    market_cap,
    discount_rate=0.09,
    terminal_growth_rate=0.025,
    years=5,
):
    if not isinstance(annual_fcf, (int, float)) or annual_fcf <= 0:
        return None
    if not isinstance(market_cap, (int, float)) or market_cap <= 0:
        return None
    if discount_rate <= terminal_growth_rate:
        return None

    low = -0.50
    high = 0.50
    low_value = discounted_fcf_equity_value(
        annual_fcf,
        low,
        discount_rate,
        terminal_growth_rate,
        years,
    )
    high_value = discounted_fcf_equity_value(
        annual_fcf,
        high,
        discount_rate,
        terminal_growth_rate,
        years,
    )

    if low_value is not None and low_value >= market_cap:
        return -50.0
    if high_value is not None and high_value <= market_cap:
        return 50.0

    for _ in range(80):
        middle = (low + high) / 2
        middle_value = discounted_fcf_equity_value(
            annual_fcf,
            middle,
            discount_rate,
            terminal_growth_rate,
            years,
        )
        if middle_value is None:
            return None
        if middle_value < market_cap:
            low = middle
        else:
            high = middle

    return round(((low + high) / 2) * 100, 2)


def evaluate_fcf_yield(value):
    if value is None:
        return unavailable_valuation_rule(
            "fcf_yield",
            "Free cash flow yield",
            "%",
            30,
            "FCF anual ou capitalização bolsista indisponível.",
        )
    if value >= 7:
        points, status, text = 30, "strong", "FCF yield elevado nos dados disponíveis."
    elif value >= 5:
        points, status, text = 25, "positive", "FCF yield sólido nos dados disponíveis."
    elif value >= 3:
        points, status, text = 18, "neutral", "FCF yield intermédio."
    elif value > 0:
        points, status, text = 8, "watch", "FCF yield reduzido."
    else:
        points, status, text = 0, "warning", "Free cash flow anual não positivo."
    return valuation_rule(
        "fcf_yield", "Free cash flow yield", value, "%",
        points, 30, status, text,
    )


def evaluate_pe(value, annual_net_income):
    if value is None:
        if isinstance(annual_net_income, (int, float)) and annual_net_income <= 0:
            return valuation_rule(
                "price_to_earnings", "P/E calculado", None, "x",
                0, 25, "warning", "Lucro anual não positivo; P/E não é interpretável.",
            )
        return unavailable_valuation_rule(
            "price_to_earnings", "P/E calculado", "x", 25,
            "Lucro anual ou capitalização bolsista indisponível.",
        )
    if value <= 15:
        points, status, text = 25, "strong", "Múltiplo de lucro reduzido, sujeito ao contexto do negócio."
    elif value <= 22:
        points, status, text = 20, "positive", "Múltiplo de lucro moderado."
    elif value <= 30:
        points, status, text = 13, "neutral", "Múltiplo de lucro relevante."
    elif value <= 45:
        points, status, text = 6, "watch", "Múltiplo de lucro exigente."
    else:
        points, status, text = 1, "warning", "Múltiplo de lucro muito exigente."
    return valuation_rule(
        "price_to_earnings", "P/E calculado", value, "x",
        points, 25, status, text,
    )


def evaluate_ps(value):
    if value is None:
        return unavailable_valuation_rule(
            "price_to_sales", "P/S calculado", "x", 15,
            "Receitas anuais ou capitalização bolsista indisponível.",
        )
    if value <= 2:
        points, status, text = 15, "strong", "Preço/receitas reduzido."
    elif value <= 4:
        points, status, text = 12, "positive", "Preço/receitas moderado."
    elif value <= 7:
        points, status, text = 8, "neutral", "Preço/receitas relevante."
    elif value <= 12:
        points, status, text = 4, "watch", "Preço/receitas exigente."
    else:
        points, status, text = 0, "warning", "Preço/receitas muito exigente."
    return valuation_rule(
        "price_to_sales", "P/S calculado", value, "x",
        points, 15, status, text,
    )


def evaluate_pb(value, equity):
    if value is None:
        if isinstance(equity, (int, float)) and equity <= 0:
            return valuation_rule(
                "price_to_book", "P/B calculado", None, "x",
                0, 10, "warning", "Capital próprio não positivo; P/B não é interpretável.",
            )
        return unavailable_valuation_rule(
            "price_to_book", "P/B calculado", "x", 10,
            "Capital próprio ou capitalização bolsista indisponível.",
        )
    if value <= 3:
        points, status, text = 10, "positive", "P/B reduzido a moderado."
    elif value <= 6:
        points, status, text = 7, "neutral", "P/B relevante."
    elif value <= 10:
        points, status, text = 3, "watch", "P/B exigente."
    else:
        points, status, text = 0, "warning", "P/B muito exigente."
    return valuation_rule(
        "price_to_book", "P/B calculado", value, "x",
        points, 10, status, text,
    )


def evaluate_implied_growth(value):
    if value is None:
        return unavailable_valuation_rule(
            "reverse_dcf_growth", "Crescimento implícito no reverse DCF", "%", 20,
            "É necessário FCF anual positivo e capitalização bolsista.",
        )
    if value <= 0:
        points, status, text = 20, "strong", "O preço não exige crescimento positivo no cenário-base."
    elif value <= 5:
        points, status, text = 17, "positive", "O preço implica crescimento moderado do FCF."
    elif value <= 10:
        points, status, text = 12, "neutral", "O preço implica crescimento material do FCF."
    elif value <= 15:
        points, status, text = 6, "watch", "O preço exige crescimento elevado do FCF."
    else:
        points, status, text = 1, "warning", "O preço exige crescimento muito elevado do FCF."
    return valuation_rule(
        "reverse_dcf_growth",
        "Crescimento implícito no reverse DCF",
        value,
        "%",
        points,
        20,
        status,
        text,
    )


def build_stock_valuation_snapshot(asset: dict, fundamentals: dict | None):
    if asset.get("asset_type") != "stock":
        return None

    fundamentals = fundamentals or {}
    duration = fundamentals.get("duration_metrics", {})
    instant = fundamentals.get("instant_metrics", {})
    derived = fundamentals.get("derived_metrics", {})

    market_cap_millions = finite_number(asset.get("market_cap_millions"))
    market_cap = (
        market_cap_millions * 1_000_000
        if market_cap_millions is not None and market_cap_millions > 0
        else None
    )

    annual_revenue = fact_value(
        duration.get("revenue", {}).get("latest_annual")
    )
    annual_net_income = fact_value(
        duration.get("net_income", {}).get("latest_annual")
    )
    equity = fact_value(instant.get("equity"))
    annual_fcf = finite_number(derived.get("annual_free_cash_flow"))

    price = finite_number(asset.get("price"))
    shares_millions = finite_number(asset.get("shares_outstanding_millions"))
    shares = (
        shares_millions * 1_000_000
        if shares_millions is not None and shares_millions > 0
        else None
    )
    if shares is None and market_cap and price and price > 0:
        shares = market_cap / price

    def ratio(numerator, denominator):
        if not isinstance(numerator, (int, float)):
            return None
        if not isinstance(denominator, (int, float)) or denominator == 0:
            return None
        return round(numerator / denominator, 2)

    fcf_yield = (
        round((annual_fcf / market_cap) * 100, 2)
        if isinstance(annual_fcf, (int, float)) and market_cap
        else None
    )
    earnings_yield = (
        round((annual_net_income / market_cap) * 100, 2)
        if isinstance(annual_net_income, (int, float)) and market_cap
        else None
    )
    pe = (
        ratio(market_cap, annual_net_income)
        if isinstance(annual_net_income, (int, float)) and annual_net_income > 0
        else None
    )
    ps = (
        ratio(market_cap, annual_revenue)
        if isinstance(annual_revenue, (int, float)) and annual_revenue > 0
        else None
    )
    pb = (
        ratio(market_cap, equity)
        if isinstance(equity, (int, float)) and equity > 0
        else None
    )

    assumptions = {
        "forecast_years": 5,
        "discount_rate_percentage": 9.0,
        "terminal_growth_percentage": 2.5,
    }
    implied_growth = reverse_dcf_implied_growth(
        annual_fcf,
        market_cap,
        discount_rate=assumptions["discount_rate_percentage"] / 100,
        terminal_growth_rate=assumptions["terminal_growth_percentage"] / 100,
        years=assumptions["forecast_years"],
    )

    rules = [
        evaluate_fcf_yield(fcf_yield),
        evaluate_pe(pe, annual_net_income),
        evaluate_ps(ps),
        evaluate_pb(pb, equity),
        evaluate_implied_growth(implied_growth),
    ]
    available_rules = [rule for rule in rules if rule["status"] != "unavailable"]
    achieved_points = sum(rule["points"] for rule in available_rules)
    available_max_points = sum(rule["max_points"] for rule in available_rules)
    total_max_points = sum(rule["max_points"] for rule in rules)
    score = (
        round((achieved_points / available_max_points) * 100)
        if available_max_points
        else None
    )
    coverage = round((available_max_points / total_max_points) * 100)

    positive_signals = [
        {
            "rule_id": rule["id"],
            "label": rule["label"],
            "value": rule["value"],
            "unit": rule["unit"],
            "interpretation": rule["interpretation"],
        }
        for rule in rules
        if rule["status"] in {"strong", "positive"}
    ]
    warning_signals = [
        {
            "rule_id": rule["id"],
            "label": rule["label"],
            "value": rule["value"],
            "unit": rule["unit"],
            "interpretation": rule["interpretation"],
        }
        for rule in rules
        if rule["status"] in {"watch", "warning"}
    ]

    current_dcf_value = discounted_fcf_equity_value(
        annual_fcf,
        0.05,
        assumptions["discount_rate_percentage"] / 100,
        assumptions["terminal_growth_percentage"] / 100,
        assumptions["forecast_years"],
    ) if isinstance(annual_fcf, (int, float)) and annual_fcf > 0 else None

    indicative_value_per_share = (
        round(current_dcf_value / shares, 2)
        if current_dcf_value is not None and shares
        else None
    )

    return {
        "status": "partial" if score is not None else "insufficient_data",
        "score": score,
        "classification": classify_valuation_score(score),
        "coverage_percentage": coverage,
        "achieved_points": achieved_points,
        "available_max_points": available_max_points,
        "total_max_points": total_max_points,
        "currency": asset.get("currency"),
        "metrics": {
            "market_cap": market_cap,
            "annual_revenue": annual_revenue,
            "annual_net_income": annual_net_income,
            "annual_free_cash_flow": annual_fcf,
            "equity": equity,
            "shares_outstanding": shares,
            "free_cash_flow_yield_percentage": fcf_yield,
            "earnings_yield_percentage": earnings_yield,
            "price_to_earnings": pe,
            "price_to_sales": ps,
            "price_to_book": pb,
            "reverse_dcf_implied_growth_percentage": implied_growth,
            "illustrative_value_per_share_at_5pct_growth": indicative_value_per_share,
        },
        "reverse_dcf": {
            "assumptions": assumptions,
            "implied_growth_percentage": implied_growth,
            "interpretation": (
                "Crescimento anual do FCF necessário para aproximar o valor "
                "presente da capitalização atual, usando pressupostos genéricos."
            ),
        },
        "rules": rules,
        "positive_signals": positive_signals,
        "warning_signals": warning_signals,
        "methodology_note": (
            "Snapshot relativo e setorialmente neutro. Não substitui histórico de "
            "múltiplos, comparáveis, guidance, normalização do FCF ou cenários próprios."
        ),
    }


def enrich_framework_engine_with_valuation(engine: dict, valuation: dict | None):
    if not isinstance(engine, dict) or not valuation:
        return engine

    checklist = engine.get("framework_checklist", {}).get("items", [])
    valuation_item = next(
        (item for item in checklist if item.get("id") == "valuation"),
        None,
    )
    metrics = valuation.get("metrics", {})
    available = []
    if metrics.get("free_cash_flow_yield_percentage") is not None:
        available.append("free cash flow yield")
    if metrics.get("price_to_earnings") is not None:
        available.append("P/E calculado")
    if metrics.get("price_to_sales") is not None:
        available.append("P/S calculado")
    if metrics.get("price_to_book") is not None:
        available.append("P/B calculado")
    if metrics.get("reverse_dcf_implied_growth_percentage") is not None:
        available.append("reverse DCF com crescimento implícito")

    missing = [
        "múltiplos históricos",
        "comparáveis setoriais",
        "normalização do FCF",
        "guidance e estimativas",
        "cenários bear/base/bull específicos",
        "retorno esperado ajustado ao risco",
    ]

    if valuation_item:
        valuation_item["status"] = "partial" if available else "missing"
        valuation_item["available_data"] = available
        valuation_item["missing_data"] = missing

    framework = engine.get("framework_checklist", {})
    if checklist:
        available_sections = sum(
            1 for item in checklist
            if item.get("status") in {"available", "partial"}
        )
        framework["coverage_percentage"] = round(
            (available_sections / len(checklist)) * 100
        )
        framework["confidence"] = (
            "high" if framework["coverage_percentage"] >= 70
            else "moderate" if framework["coverage_percentage"] >= 40
            else "low"
        )

    engine["valuation_snapshot"] = valuation
    engine["next_required_data"] = [
        item
        for item in engine.get("next_required_data", [])
        if item != "valuation, reverse DCF e cenários"
    ]
    for item in missing:
        if item not in engine["next_required_data"]:
            engine["next_required_data"].append(item)

    decision = engine.get("decision", {})
    decision["valuation_context"] = {
        "score": valuation.get("score"),
        "classification": valuation.get("classification"),
        "fcf_yield_percentage": metrics.get("free_cash_flow_yield_percentage"),
        "reverse_dcf_implied_growth_percentage": metrics.get(
            "reverse_dcf_implied_growth_percentage"
        ),
    }
    if decision.get("label") == "Aguardar valuation e revisão qualitativa":
        decision["label"] = "Aguardar comparação histórica, notícias e carteira"
        decision["reason"] = (
            "O valuation quantitativo inicial está disponível, mas ainda faltam "
            "comparáveis, contexto qualitativo, notícias, carteira e plano de entrada."
        )

    engine["scope"] = (
        "Snapshot quantitativo de qualidade, cash flow, dívida, diluição, "
        "alocação de capital, valuation relativo e reverse DCF. "
        "Não representa ainda uma análise integral."
    )
    return engine
'''

anchor = '\ndef resolve_analysis_payload(\n'
if anchor not in server:
    raise SystemExit('ERRO: ponto de inserção backend não encontrado.')
server = server.replace(anchor, valuation_backend + anchor, 1)

technical_to_fx = '''        warnings.append(\n            "A análise técnica está temporariamente indisponível."\n        )\n\n    fx = None\n'''
valuation_block = '''        warnings.append(\n            "A análise técnica está temporariamente indisponível."\n        )\n\n    valuation = None\n    if asset.get("asset_type") == "stock":\n        try:\n            valuation = build_stock_valuation_snapshot(asset, fundamentals)\n            valuation_status = (\n                "ok"\n                if valuation and valuation.get("score") is not None\n                else "unavailable"\n            )\n            sources["valuation"] = analysis_source(\n                "ThesisOS calculation from Finnhub + SEC EDGAR",\n                valuation_status,\n                coverage_percentage=(valuation or {}).get(\n                    "coverage_percentage"\n                ),\n            )\n        except Exception as error:\n            sources["valuation"] = analysis_source(\n                "ThesisOS Valuation Engine",\n                "unavailable",\n                detail=str(error),\n            )\n            warnings.append(\n                "O valuation quantitativo está temporariamente indisponível."\n            )\n\n    fx = None\n'''
if technical_to_fx not in server:
    raise SystemExit('ERRO: bloco técnico/fx não encontrado.')
server = server.replace(technical_to_fx, valuation_block, 1)

payload_anchor = '''        "technical": technical,\n        "fx": fx,\n'''
if payload_anchor not in server:
    raise SystemExit('ERRO: payload technical/fx não encontrado.')
server = server.replace(
    payload_anchor,
    '''        "technical": technical,\n        "valuation": valuation,\n        "fx": fx,\n''',
    1,
)

engine_anchor = '''    payload["framework_engine"] = enrich_framework_engine_with_technical(\n        payload["framework_engine"],\n        technical,\n    )\n\n    return payload, 200\n'''
if engine_anchor not in server:
    raise SystemExit('ERRO: enriquecimento do framework não encontrado.')
server = server.replace(
    engine_anchor,
    '''    payload["framework_engine"] = enrich_framework_engine_with_technical(\n        payload["framework_engine"],\n        technical,\n    )\n    payload["framework_engine"] = enrich_framework_engine_with_valuation(\n        payload["framework_engine"],\n        valuation,\n    )\n\n    return payload, 200\n''',
    1,
)

old_radar_score = '''def radar_composite_score(payload: dict):\n    engine = payload.get("framework_engine", {})\n    snapshot = engine.get("quantitative_snapshot", {})\n    technical = payload.get("technical") or {}\n    quality_score = finite_number(snapshot.get("score"))\n    technical_score = finite_number(technical.get("score"))\n    source_coverage = finite_number(\n        payload.get("data_quality", {}).get("completeness_percentage")\n    )\n\n    weighted = []\n\n    if quality_score is not None:\n        weighted.append((quality_score, 0.7))\n    if technical_score is not None:\n        weighted.append((technical_score, 0.2))\n    if source_coverage is not None:\n        weighted.append((source_coverage, 0.1))\n\n    if not weighted:\n        return None\n\n    total_weight = sum(weight for _, weight in weighted)\n    return round(sum(value * weight for value, weight in weighted) / total_weight)\n'''
new_radar_score = '''def radar_composite_score(payload: dict):\n    engine = payload.get("framework_engine", {})\n    snapshot = engine.get("quantitative_snapshot", {})\n    technical = payload.get("technical") or {}\n    valuation = payload.get("valuation") or {}\n    asset_type = payload.get("asset", {}).get("asset_type")\n    quality_score = finite_number(snapshot.get("score"))\n    technical_score = finite_number(technical.get("score"))\n    valuation_score = finite_number(valuation.get("score"))\n    source_coverage = finite_number(\n        payload.get("data_quality", {}).get("completeness_percentage")\n    )\n\n    weighted = []\n    weights = (\n        {"quality": 0.55, "valuation": 0.20, "technical": 0.15, "data": 0.10}\n        if asset_type == "stock"\n        else {"quality": 0.70, "technical": 0.20, "data": 0.10}\n    )\n\n    if quality_score is not None:\n        weighted.append((quality_score, weights["quality"]))\n    if valuation_score is not None and "valuation" in weights:\n        weighted.append((valuation_score, weights["valuation"]))\n    if technical_score is not None:\n        weighted.append((technical_score, weights["technical"]))\n    if source_coverage is not None:\n        weighted.append((source_coverage, weights["data"]))\n\n    if not weighted:\n        return None\n\n    total_weight = sum(weight for _, weight in weighted)\n    return round(sum(value * weight for value, weight in weighted) / total_weight)\n'''
if old_radar_score not in server:
    raise SystemExit('ERRO: radar_composite_score não encontrado.')
server = server.replace(old_radar_score, new_radar_score, 1)

# Add valuation reasons and fields to radar output.
server = server.replace(
    '''    technical = payload.get("technical") or {}\n    indicators = technical.get("indicators", {})\n''',
    '''    technical = payload.get("technical") or {}\n    valuation = payload.get("valuation") or {}\n    valuation_metrics = valuation.get("metrics", {})\n    indicators = technical.get("indicators", {})\n''',
    1,
)
server = server.replace(
    '''        snapshot.get("positive_signals", []),\n        technical.get("positive_signals", []),\n''',
    '''        snapshot.get("positive_signals", []),\n        valuation.get("positive_signals", []),\n        technical.get("positive_signals", []),\n''',
    1,
)
server = server.replace(
    '''        snapshot.get("warning_signals", []),\n        technical.get("warning_signals", []),\n''',
    '''        snapshot.get("warning_signals", []),\n        valuation.get("warning_signals", []),\n        technical.get("warning_signals", []),\n''',
    1,
)
server = server.replace(
    '''        "technical_score": technical.get("score"),\n        "technical_classification": technical.get("classification"),\n''',
    '''        "valuation_score": valuation.get("score"),\n        "valuation_classification": valuation.get("classification"),\n        "free_cash_flow_yield_percentage": valuation_metrics.get(\n            "free_cash_flow_yield_percentage"\n        ),\n        "price_to_earnings": valuation_metrics.get("price_to_earnings"),\n        "reverse_dcf_implied_growth_percentage": valuation_metrics.get(\n            "reverse_dcf_implied_growth_percentage"\n        ),\n        "technical_score": technical.get("score"),\n        "technical_classification": technical.get("classification"),\n''',
    1,
)
server = server.replace(
    '''        "methodology": {\n            "quality_weight": 0.7,\n            "technical_weight": 0.2,\n            "data_quality_weight": 0.1,\n            "decision_limit": (\n                "O ranking identifica candidatas para análise. "\n                "Não substitui valuation, notícias, carteira ou plano de entrada."\n            ),\n        },\n''',
    '''        "methodology": {\n            "stock_weights": {\n                "quality": 0.55,\n                "valuation": 0.20,\n                "technical": 0.15,\n                "data_quality": 0.10,\n            },\n            "etf_weights": {\n                "structure": 0.70,\n                "technical": 0.20,\n                "data_quality": 0.10,\n            },\n            "decision_limit": (\n                "O ranking identifica candidatas para análise. "\n                "Não substitui notícias, contexto qualitativo, carteira ou plano de entrada."\n            ),\n        },\n''',
    1,
)

# Optional valuation endpoint.
route_anchor = '''        if parsed_url.path.startswith("/api/technical/"):\n            self.handle_technical_request(parsed_url)\n            return\n\n        if parsed_url.path.startswith("/api/fx/"):\n'''
if route_anchor not in server:
    raise SystemExit('ERRO: rotas técnicas não encontradas.')
server = server.replace(
    route_anchor,
    '''        if parsed_url.path.startswith("/api/technical/"):\n            self.handle_technical_request(parsed_url)\n            return\n\n        if parsed_url.path.startswith("/api/valuation/"):\n            self.handle_valuation_request(parsed_url)\n            return\n\n        if parsed_url.path.startswith("/api/fx/"):\n''',
    1,
)
handler_anchor = '''    def handle_analysis_request(self, parsed_url) -> None:\n'''
valuation_handler = '''    def handle_valuation_request(self, parsed_url) -> None:\n        identifier = unquote(\n            parsed_url.path.removeprefix("/api/valuation/")\n        ).strip().upper()\n\n        if not identifier:\n            self.send_json({"error": "Identificador inválido."}, status=400)\n            return\n\n        try:\n            payload, status = resolve_analysis_payload(identifier)\n            if status != 200:\n                self.send_json(payload, status=status)\n                return\n            self.send_json(payload.get("valuation") or {})\n        except Exception as error:\n            self.send_json(\n                {\n                    "error": "Erro ao calcular valuation.",\n                    "detail": str(error),\n                },\n                status=500,\n            )\n\n'''
if handler_anchor not in server:
    raise SystemExit('ERRO: handler analysis não encontrado.')
server = server.replace(handler_anchor, valuation_handler + handler_anchor, 1)

# ------------------------------------------------------------------
# Frontend CSS
# ------------------------------------------------------------------
html = html.replace(
    '#analysis.stock-mode #realMarketDataCard ~ *:not(#stockFundamentalsPanel):not(#stockCashFlowPanel){display:none!important}',
    '#analysis.stock-mode #realMarketDataCard ~ *:not(#stockFundamentalsPanel):not(#stockCashFlowPanel):not(#stockValuationPanel){display:none!important}',
)

css = r'''
    /* ThesisOS stock valuation engine */
    #stockValuationPanel{display:none;margin-bottom:16px}
    #analysis.stock-mode #stockValuationPanel{display:block}
    #analysis.etf-mode #stockValuationPanel,
    #analysis.analysis-neutral #stockValuationPanel{display:none!important}
    .valuation-top{display:flex;justify-content:space-between;gap:18px;align-items:flex-start}
    .valuation-score{font-size:30px;font-weight:900;letter-spacing:-.04em}
    .valuation-metric-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:10px;margin-top:14px}
    .valuation-metric{padding:13px;border:1px solid #263a59;border-radius:13px;background:#111d30;min-height:94px}
    .valuation-metric span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.05em}
    .valuation-metric strong{display:block;font-size:18px;margin:7px 0 3px}
    .valuation-metric small{color:var(--muted);line-height:1.35}
    .valuation-layout{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(320px,.85fr);gap:14px;margin-top:14px}
    .valuation-rule-list{display:grid;gap:8px;margin-top:10px}
    .valuation-rule{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;padding:10px 11px;border:1px solid #263a59;border-radius:11px;background:#101b2d}
    .valuation-rule strong{display:block;font-size:12px}.valuation-rule small{display:block;color:var(--muted);margin-top:4px;line-height:1.35}
    .valuation-points{font-weight:900;white-space:nowrap}
    .dcf-lab{padding:14px;border:1px solid #304968;border-radius:14px;background:linear-gradient(180deg,#14243b,#101c2f)}
    .dcf-input-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:9px;margin:12px 0}
    .dcf-result-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:12px}
    .dcf-result{padding:11px;border-radius:11px;background:#0f1b2d;border:1px solid #22344f}
    .dcf-result span{display:block;color:var(--muted);font-size:10px;text-transform:uppercase}.dcf-result strong{display:block;margin-top:5px;font-size:17px}
    @media(max-width:1100px){.valuation-metric-grid{grid-template-columns:repeat(3,1fr)}.valuation-layout{grid-template-columns:1fr}}
    @media(max-width:700px){.valuation-metric-grid,.dcf-input-grid,.dcf-result-grid{grid-template-columns:1fr}}
'''
style_anchor = '</style>'
if style_anchor not in html:
    raise SystemExit('ERRO: fecho de style não encontrado.')
html = html.replace(style_anchor, css + '\n  </style>', 1)

# Source card.
source_anchor = '''          <div class="analysis-source-item" id="analysisSourceFundamentals" data-status="waiting">\n'''
source_card = '''          <div class="analysis-source-item" id="analysisSourceValuation" data-status="waiting" style="display:none">\n            <div class="analysis-source-item-head">\n              <strong>Valuation</strong>\n              <span class="analysis-source-status">A aguardar</span>\n            </div>\n            <small>Cálculo ThesisOS · Finnhub + SEC</small>\n          </div>\n'''
if source_anchor not in html:
    raise SystemExit('ERRO: source fundamentals não encontrado.')
html = html.replace(source_anchor, source_card + source_anchor, 1)

valuation_html = r'''
      <section class="card" id="stockValuationPanel">
        <div class="valuation-top">
          <div>
            <h2 style="margin:0 0 6px">Valuation Engine</h2>
            <p class="sub" id="valuationScope">Múltiplos calculados, FCF yield e reverse DCF.</p>
          </div>
          <div style="text-align:right">
            <div><strong class="valuation-score" id="valuationScore">—</strong><span class="sub">/100</span></div>
            <span class="pill" id="valuationClassification">A aguardar</span>
          </div>
        </div>

        <div class="valuation-metric-grid">
          <div class="valuation-metric"><span>Capitalização</span><strong id="valuationMarketCap">—</strong><small id="valuationCoverage">Cobertura indisponível</small></div>
          <div class="valuation-metric"><span>FCF yield</span><strong id="valuationFcfYield">—</strong><small>FCF anual / capitalização</small></div>
          <div class="valuation-metric"><span>P/E calculado</span><strong id="valuationPe">—</strong><small>Capitalização / lucro anual</small></div>
          <div class="valuation-metric"><span>P/S calculado</span><strong id="valuationPs">—</strong><small>Capitalização / receitas anuais</small></div>
          <div class="valuation-metric"><span>P/B calculado</span><strong id="valuationPb">—</strong><small>Capitalização / capital próprio</small></div>
          <div class="valuation-metric"><span>Crescimento implícito</span><strong id="valuationImpliedGrowth">—</strong><small>Reverse DCF a 5 anos</small></div>
        </div>

        <div class="valuation-layout">
          <div>
            <div class="section-head" style="margin:0 0 8px"><div><h3>Regras transparentes</h3><p class="sub" style="margin:4px 0 0">Leitura genérica; setores e ciclos exigem comparação própria.</p></div></div>
            <div class="valuation-rule-list" id="valuationRules"></div>
          </div>
          <div class="dcf-lab">
            <h3>DCF ilustrativo</h3>
            <p class="sub" style="margin:5px 0 0">Altera os pressupostos para testar cenários. O cálculo usa o FCF anual reportado e não normalizado.</p>
            <div class="dcf-input-grid">
              <label>Crescimento FCF anual (%)<input id="dcfGrowth" type="number" step="0.5" value="5"></label>
              <label>Taxa de desconto (%)<input id="dcfDiscount" type="number" step="0.5" value="9"></label>
              <label>Crescimento terminal (%)<input id="dcfTerminal" type="number" step="0.1" value="2.5"></label>
            </div>
            <button class="primary" id="dcfCalculateBtn" type="button">Calcular cenário</button>
            <div class="dcf-result-grid">
              <div class="dcf-result"><span>Valor indicativo / ação</span><strong id="dcfValuePerShare">—</strong></div>
              <div class="dcf-result"><span>Upside / downside</span><strong id="dcfUpside">—</strong></div>
            </div>
            <div class="logic-note" id="dcfMethodology">O reverse DCF automático mostra o crescimento necessário para justificar o preço atual. Este simulador testa um cenário escolhido pelo utilizador.</div>
          </div>
        </div>

        <div class="framework-engine-note" id="valuationMethodology">Valuation relativo inicial. Ainda faltam histórico, comparáveis, guidance, normalização do FCF e cenários específicos.</div>
      </section>
'''
valuation_html_anchor = '      <section class="card" id="etfAnalysisPanel" style="margin-bottom:16px">\n'
if valuation_html_anchor not in html:
    raise SystemExit('ERRO: painel ETF não encontrado.')
html = html.replace(valuation_html_anchor, valuation_html + '\n' + valuation_html_anchor, 1)

# Quality sources reset/render.
html = html.replace(
    '''      "#analysisSourceTechnical",\n      "#analysisSourceFundamentals",\n''',
    '''      "#analysisSourceTechnical",\n      "#analysisSourceValuation",\n      "#analysisSourceFundamentals",\n''',
    1,
)
html = html.replace(
    '''    updateAnalysisSourceCard(\n      "#analysisSourceTechnical",\n      sources.technical\n    );\n    updateAnalysisSourceCard(\n      "#analysisSourceFundamentals",\n      sources.fundamentals\n    );\n''',
    '''    updateAnalysisSourceCard(\n      "#analysisSourceTechnical",\n      sources.technical\n    );\n    const valuationSourceCard=$("#analysisSourceValuation");\n    if(valuationSourceCard){\n      const isStock=payload.asset?.asset_type==="stock";\n      valuationSourceCard.style.display=isStock ? "" : "none";\n      if(isStock){\n        updateAnalysisSourceCard(\n          "#analysisSourceValuation",\n          sources.valuation\n        );\n      }\n    }\n    updateAnalysisSourceCard(\n      "#analysisSourceFundamentals",\n      sources.fundamentals\n    );\n''',
    1,
)

valuation_js = r'''
  // ---------------------------------------------------------------
  // Stock Valuation Engine
  // ---------------------------------------------------------------
  function valuationNumber(value,digits=2){
    const number=Number(value);
    return Number.isFinite(number)?formatNumber(number,digits):"—";
  }
  function valuationMoney(value,currency="USD"){
    const number=Number(value);
    if(!Number.isFinite(number))return "—";
    if(Math.abs(number)>=1e12)return `${currency} ${formatNumber(number/1e12,2)} T`;
    if(Math.abs(number)>=1e9)return `${currency} ${formatNumber(number/1e9,2)} B`;
    if(Math.abs(number)>=1e6)return `${currency} ${formatNumber(number/1e6,2)} M`;
    return `${currency} ${formatNumber(number,2)}`;
  }
  function resetStockValuation(state="idle"){
    window.currentValuationSnapshot=null;
    ["#valuationScore","#valuationMarketCap","#valuationFcfYield","#valuationPe","#valuationPs","#valuationPb","#valuationImpliedGrowth","#dcfValuePerShare","#dcfUpside"].forEach(selector=>{const el=$(selector);if(el)el.textContent=state==="loading"?"…":"—";});
    if($("#valuationClassification"))$("#valuationClassification").textContent=state==="loading"?"A calcular":"A aguardar";
    if($("#valuationCoverage"))$("#valuationCoverage").textContent="Cobertura indisponível";
    if($("#valuationRules"))$("#valuationRules").innerHTML='<div class="sub">Pesquisa uma ação para calcular o valuation.</div>';
  }
  function valuationStatusClass(status){
    if(["strong","positive"].includes(status))return "pill";
    if(status==="warning")return "pill bad";
    return "pill warn";
  }
  function renderStockValuation(valuation,asset={}){
    if(asset.asset_type!=="stock"){
      resetStockValuation();
      return;
    }
    window.currentValuationSnapshot=valuation||null;
    if(!valuation){resetStockValuation();return;}
    const metrics=valuation.metrics||{},currency=valuation.currency||asset.currency||"USD";
    $("#valuationScore").textContent=Number.isFinite(Number(valuation.score))?formatNumber(valuation.score,0):"—";
    $("#valuationClassification").textContent=valuation.classification?.label||"Dados insuficientes";
    $("#valuationMarketCap").textContent=valuationMoney(metrics.market_cap,currency);
    $("#valuationCoverage").textContent=`Cobertura ${valuationNumber(valuation.coverage_percentage,0)}%`;
    $("#valuationFcfYield").textContent=Number.isFinite(Number(metrics.free_cash_flow_yield_percentage))?`${valuationNumber(metrics.free_cash_flow_yield_percentage)}%`:"—";
    $("#valuationPe").textContent=Number.isFinite(Number(metrics.price_to_earnings))?`${valuationNumber(metrics.price_to_earnings)}x`:"—";
    $("#valuationPs").textContent=Number.isFinite(Number(metrics.price_to_sales))?`${valuationNumber(metrics.price_to_sales)}x`:"—";
    $("#valuationPb").textContent=Number.isFinite(Number(metrics.price_to_book))?`${valuationNumber(metrics.price_to_book)}x`:"—";
    $("#valuationImpliedGrowth").textContent=Number.isFinite(Number(metrics.reverse_dcf_implied_growth_percentage))?`${valuationNumber(metrics.reverse_dcf_implied_growth_percentage)}%`:"—";
    $("#valuationRules").innerHTML=(valuation.rules||[]).map(rule=>`<div class="valuation-rule"><div><strong>${escapeHtml(rule.label)}</strong><small>${escapeHtml(rule.interpretation||"")}</small><span class="${valuationStatusClass(rule.status)}" style="display:inline-flex;margin-top:7px">${rule.value===null||rule.value===undefined?"Indisponível":escapeHtml(`${rule.value}${rule.unit||""}`)}</span></div><div class="valuation-points">${rule.status==="unavailable"?"—":`${rule.points}/${rule.max_points}`}</div></div>`).join("")||'<div class="sub">Sem regras disponíveis.</div>';
    $("#valuationMethodology").textContent=valuation.methodology_note||"Valuation quantitativo parcial.";
    calculateDcfScenario();
  }
  function calculateDcfScenario(){
    const valuation=window.currentValuationSnapshot,asset=window.currentAnalysisPayload?.asset||{};
    const metrics=valuation?.metrics||{};
    const fcf=Number(metrics.annual_free_cash_flow),shares=Number(metrics.shares_outstanding),price=Number(asset.price);
    const growth=Number($("#dcfGrowth")?.value)/100,discount=Number($("#dcfDiscount")?.value)/100,terminal=Number($("#dcfTerminal")?.value)/100;
    if(!Number.isFinite(fcf)||fcf<=0||!Number.isFinite(shares)||shares<=0||!Number.isFinite(discount)||!Number.isFinite(terminal)||discount<=terminal){
      $("#dcfValuePerShare").textContent="Indisponível";$("#dcfUpside").textContent="—";return;
    }
    let projected=fcf,value=0;
    for(let year=1;year<=5;year++){projected*=1+growth;value+=projected/Math.pow(1+discount,year);}
    value+=(projected*(1+terminal)/(discount-terminal))/Math.pow(1+discount,5);
    const perShare=value/shares,currency=valuation.currency||asset.currency||"USD";
    $("#dcfValuePerShare").textContent=`${currency} ${formatNumber(perShare,2)}`;
    $("#dcfUpside").textContent=Number.isFinite(price)&&price>0?`${perShare/price-1>=0?"+":""}${formatNumber((perShare/price-1)*100,1)}%`:"—";
  }
  $("#dcfCalculateBtn")?.addEventListener("click",calculateDcfScenario);
'''
js_anchor = '  // ---------------------------------------------------------------\n  // Technical Engine, Watchlist, live Radar, Comparison and Export\n'
if js_anchor not in html:
    raise SystemExit('ERRO: âncora JS Technical Engine não encontrada.')
html = html.replace(js_anchor, valuation_js + '\n' + js_anchor, 1)

# Render and reset hooks.
html = html.replace(
    '''    renderFrameworkEngine(payload.framework_engine);\n    renderTechnicalAnalysis(payload.technical);\n    syncAnalysisActionButtons();\n''',
    '''    renderFrameworkEngine(payload.framework_engine);\n    renderTechnicalAnalysis(payload.technical);\n    renderStockValuation(payload.valuation,asset);\n    syncAnalysisActionButtons();\n''',
    1,
)
html = html.replace(
    '''    resetFrameworkEngine("loading");\n    resetTechnicalAnalysis("loading");\n    resetEtfOfficialProfile("loading");\n''',
    '''    resetFrameworkEngine("loading");\n    resetTechnicalAnalysis("loading");\n    resetStockValuation("loading");\n    resetEtfOfficialProfile("loading");\n''',
    1,
)

# Radar methodology text and cards.
html = html.replace(
    'Radar score = 70% qualidade estrutural/fundamental + 20% Technical Engine + 10% qualidade dos dados. Valuation, notícias e carteira continuam obrigatórios antes de uma decisão.',
    'Ações: 55% qualidade + 20% valuation + 15% técnica + 10% qualidade dos dados. ETFs: 70% estrutura + 20% técnica + 10% dados. Notícias, carteira e plano de entrada continuam obrigatórios.',
    1,
)
html = html.replace(
    '.radar-live-card .ticker-logo{font-size:12px}.radar-score-layout{display:grid;grid-template-columns:1fr 1fr 1fr;gap:7px;margin:13px 0}',
    '.radar-live-card .ticker-logo{font-size:12px}.radar-score-layout{display:grid;grid-template-columns:repeat(4,1fr);gap:7px;margin:13px 0}',
    1,
)
old_radar_fragment = '''<div class="radar-score-layout"><div class="radar-score-box"><span>Qualidade</span><strong>${Number.isFinite(Number(item.quality_score))?formatNumber(item.quality_score,0):"—"}</strong></div><div class="radar-score-box"><span>Técnica</span><strong>${Number.isFinite(Number(item.technical_score))?formatNumber(item.technical_score,0):"—"}</strong></div><div class="radar-score-box"><span>Pullback</span><strong>${Number.isFinite(Number(item.pullback_percentage))?formatNumber(item.pullback_percentage)+"%":"—"}</strong></div></div>'''
new_radar_fragment = '''<div class="radar-score-layout"><div class="radar-score-box"><span>Qualidade</span><strong>${Number.isFinite(Number(item.quality_score))?formatNumber(item.quality_score,0):"—"}</strong></div><div class="radar-score-box"><span>Valuation</span><strong>${Number.isFinite(Number(item.valuation_score))?formatNumber(item.valuation_score,0):"—"}</strong></div><div class="radar-score-box"><span>Técnica</span><strong>${Number.isFinite(Number(item.technical_score))?formatNumber(item.technical_score,0):"—"}</strong></div><div class="radar-score-box"><span>Pullback</span><strong>${Number.isFinite(Number(item.pullback_percentage))?formatNumber(item.pullback_percentage)+"%":"—"}</strong></div></div>'''
if old_radar_fragment not in html:
    raise SystemExit('ERRO: fragmento visual do Radar não encontrado.')
html = html.replace(old_radar_fragment, new_radar_fragment, 1)

# Comparison rows.
compare_anchor = '''    add("Framework score",lf.score,rf.score,value=>Number.isFinite(Number(value))?`${formatNumber(value,0)}/100`:"—");\n    add("Technical score",lt.score,rt.score,value=>Number.isFinite(Number(value))?`${formatNumber(value,0)}/100`:"—");\n'''
compare_replacement = '''    add("Framework score",lf.score,rf.score,value=>Number.isFinite(Number(value))?`${formatNumber(value,0)}/100`:"—");\n    add("Valuation score",left.valuation?.score,right.valuation?.score,value=>Number.isFinite(Number(value))?`${formatNumber(value,0)}/100`:"—");\n    add("Technical score",lt.score,rt.score,value=>Number.isFinite(Number(value))?`${formatNumber(value,0)}/100`:"—");\n'''
if compare_anchor not in html:
    raise SystemExit('ERRO: comparação de scores não encontrada.')
html = html.replace(compare_anchor, compare_replacement, 1)
stock_compare_anchor = '''      add("Receitas YoY",ldur.revenue?.quarter_yoy_percentage,rdur.revenue?.quarter_yoy_percentage,pct);add("Margem operacional",ld.operating_margin_quarter_percentage,rd.operating_margin_quarter_percentage,pct);add("Margem FCF",ld.free_cash_flow_margin_percentage,rd.free_cash_flow_margin_percentage,pct);add("Conversão lucro/caixa",ld.operating_cash_flow_to_net_income_percentage,rd.operating_cash_flow_to_net_income_percentage,pct);add("Dívida líquida / FCF",ld.net_debt_to_annual_free_cash_flow,rd.net_debt_to_annual_free_cash_flow,multiple);add("SBC / receitas",ld.stock_based_compensation_to_revenue_percentage,rd.stock_based_compensation_to_revenue_percentage,pct);\n'''
stock_compare_replacement = '''      add("Receitas YoY",ldur.revenue?.quarter_yoy_percentage,rdur.revenue?.quarter_yoy_percentage,pct);add("Margem operacional",ld.operating_margin_quarter_percentage,rd.operating_margin_quarter_percentage,pct);add("Margem FCF",ld.free_cash_flow_margin_percentage,rd.free_cash_flow_margin_percentage,pct);add("FCF yield",left.valuation?.metrics?.free_cash_flow_yield_percentage,right.valuation?.metrics?.free_cash_flow_yield_percentage,pct);add("P/E calculado",left.valuation?.metrics?.price_to_earnings,right.valuation?.metrics?.price_to_earnings,multiple);add("Crescimento implícito reverse DCF",left.valuation?.metrics?.reverse_dcf_implied_growth_percentage,right.valuation?.metrics?.reverse_dcf_implied_growth_percentage,pct);add("Conversão lucro/caixa",ld.operating_cash_flow_to_net_income_percentage,rd.operating_cash_flow_to_net_income_percentage,pct);add("Dívida líquida / FCF",ld.net_debt_to_annual_free_cash_flow,rd.net_debt_to_annual_free_cash_flow,multiple);add("SBC / receitas",ld.stock_based_compensation_to_revenue_percentage,rd.stock_based_compensation_to_revenue_percentage,pct);\n'''
if stock_compare_anchor not in html:
    raise SystemExit('ERRO: métricas stock comparison não encontradas.')
html = html.replace(stock_compare_anchor, stock_compare_replacement, 1)

# README.
readme = readme_path.read_text(encoding='utf-8') if readme_path.exists() else '# ThesisOS\n'
if '## Valuation Engine' not in readme:
    readme += '''\n\n## Valuation Engine\n\nPara ações com dados SEC e capitalização Finnhub, o ThesisOS calcula automaticamente:\n\n- free cash flow yield;\n- P/E, P/S e P/B derivados;\n- crescimento de FCF implícito num reverse DCF;\n- score de exigência relativa com cobertura explícita;\n- simulador DCF com pressupostos editáveis.\n\nO modelo é deliberadamente parcial e não substitui múltiplos históricos, comparáveis setoriais, guidance, normalização do FCF, notícias ou contexto da carteira. O Opportunity Radar usa o valuation como uma camada adicional, sem converter o ranking numa recomendação automática.\n'''

server_path.write_text(server, encoding='utf-8')
index_path.write_text(html, encoding='utf-8')
readme_path.write_text(readme, encoding='utf-8')

print('SUCESSO: Valuation Engine, reverse DCF e integração no Radar instalados.')
print('Foram atualizados server.py, index.html e README.md.')
