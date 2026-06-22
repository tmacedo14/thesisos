import csv
import gzip
import io
import json
import os
import re
import time
import zlib
from datetime import date, datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse
from urllib.request import Request, urlopen


HOST = "0.0.0.0"
PORT = 3000

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"
EODHD_SEARCH_URL = "https://eodhd.com/api/search"
ECB_DATA_URL = (
    "https://data-api.ecb.europa.eu/service/data/"
    "EXR/D.{currency}.EUR.SP00.A"
    "?lastNObservations=1&format=csvdata"
)
SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_COMPANY_FACTS_URL = (
    "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
)

OPENFIGI_CACHE_TTL_SECONDS = 24 * 60 * 60
EODHD_CACHE_TTL_SECONDS = 24 * 60 * 60
SEC_CACHE_TTL_SECONDS = 24 * 60 * 60
ECB_CACHE_TTL_SECONDS = 12 * 60 * 60
OPENFIGI_CACHE = {}
EODHD_CACHE = {}
SEC_CACHE = {}
ECB_CACHE = {}


def fetch_json(request: Request, timeout: int = 20):
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_finnhub(endpoint: str, params: dict) -> dict:
    api_key = os.getenv("FINNHUB_API_KEY")

    if not api_key:
        raise RuntimeError("FINNHUB_API_KEY não está configurada.")

    query_params = {
        **params,
        "token": api_key,
    }

    url = f"{FINNHUB_BASE_URL}/{endpoint}?{urlencode(query_params)}"

    request = Request(
        url,
        headers={
            "User-Agent": "ThesisOS/0.5",
            "Accept": "application/json",
        },
    )

    data = fetch_json(request, timeout=15)

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])

    return data


def fetch_openfigi_mapping(job: dict) -> list:
    api_key = os.getenv("OPENFIGI_API_KEY")

    if not api_key:
        raise RuntimeError("OPENFIGI_API_KEY não está configurada.")

    cache_key = json.dumps(
        job,
        ensure_ascii=False,
        sort_keys=True,
    )

    cached = OPENFIGI_CACHE.get(cache_key)

    if cached:
        age = time.time() - cached["created_at"]

        if age < OPENFIGI_CACHE_TTL_SECONDS:
            return cached["data"]

    request = Request(
        OPENFIGI_MAPPING_URL,
        data=json.dumps([job]).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-OPENFIGI-APIKEY": api_key,
            "User-Agent": "ThesisOS/0.5",
        },
        method="POST",
    )

    response = fetch_json(request, timeout=20)

    if not isinstance(response, list) or not response:
        raise RuntimeError("Resposta inesperada da OpenFIGI.")

    first_result = response[0]

    if first_result.get("error"):
        raise RuntimeError(first_result["error"])

    matches = first_result.get("data", [])

    OPENFIGI_CACHE[cache_key] = {
        "created_at": time.time(),
        "data": matches,
    }

    return matches



def fetch_eodhd_search(query: str) -> list:
    api_token = os.getenv("EODHD_API_TOKEN")

    if not api_token:
        raise RuntimeError("EODHD_API_TOKEN não está configurado.")

    clean_query = query.strip().upper()
    cached = EODHD_CACHE.get(clean_query)

    if cached:
        age = time.time() - cached["created_at"]

        if age < EODHD_CACHE_TTL_SECONDS:
            return cached["data"]

    params = urlencode(
        {
            "api_token": api_token,
            "fmt": "json",
            "limit": 20,
        }
    )

    url = f"{EODHD_SEARCH_URL}/{quote(clean_query)}?{params}"

    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "ThesisOS/0.6",
        },
    )

    response = fetch_json(request, timeout=20)

    if isinstance(response, dict):
        if response.get("error"):
            raise RuntimeError(response["error"])

        if response.get("message"):
            raise RuntimeError(response["message"])

    if not isinstance(response, list):
        raise RuntimeError("Resposta inesperada da EODHD.")

    EODHD_CACHE[clean_query] = {
        "created_at": time.time(),
        "data": response,
    }

    return response


SEC_DURATION_METRICS = {
    "revenue": [
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ],
    "net_income": [
        "NetIncomeLoss",
        "ProfitLoss",
    ],
    "operating_income": [
        "OperatingIncomeLoss",
    ],
    "diluted_shares": [
        "WeightedAverageNumberOfDilutedSharesOutstanding",
    ],
}

SEC_INSTANT_METRICS = {
    "assets": ["Assets"],
    "liabilities": ["Liabilities"],
    "equity": [
        "StockholdersEquity",
        (
            "StockholdersEquityIncludingPortion"
            "AttributableToNoncontrollingInterest"
        ),
    ],
    "cash": [
        "CashAndCashEquivalentsAtCarryingValue",
        (
            "CashCashEquivalentsRestrictedCashAnd"
            "RestrictedCashEquivalents"
        ),
    ],
}


def fetch_sec_json(url: str):
    user_agent = os.getenv("SEC_USER_AGENT")

    if not user_agent:
        raise RuntimeError("SEC_USER_AGENT não está configurado.")

    cached = SEC_CACHE.get(url)

    if cached:
        age = time.time() - cached["created_at"]

        if age < SEC_CACHE_TTL_SECONDS:
            return cached["data"]

    request = Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
    )

    with urlopen(request, timeout=25) as response:
        raw = response.read()
        encoding = (
            response.headers.get("Content-Encoding", "")
            .strip()
            .lower()
        )

        if encoding == "gzip":
            raw = gzip.decompress(raw)

        elif encoding == "deflate":
            try:
                raw = zlib.decompress(raw)
            except zlib.error:
                raw = zlib.decompress(raw, -zlib.MAX_WBITS)

        data = json.loads(raw.decode("utf-8"))

    SEC_CACHE[url] = {
        "created_at": time.time(),
        "data": data,
    }

    return data


def find_sec_company(ticker: str):
    companies = fetch_sec_json(SEC_TICKERS_URL)

    for item in companies.values():
        if str(item.get("ticker", "")).upper() == ticker:
            return {
                "ticker": ticker,
                "name": item.get("title"),
                "cik": str(item.get("cik_str", "")).zfill(10),
            }

    return None


def parse_sec_date(value):
    if not value:
        return None

    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def sec_duration_days(entry):
    start = parse_sec_date(entry.get("start"))
    end = parse_sec_date(entry.get("end"))

    if not start or not end:
        return None

    return (end - start).days + 1


def normalize_sec_fact(concept, fact, entry):
    return {
        "concept": concept,
        "label": fact.get("label"),
        "value": entry.get("val"),
        "unit": entry.get("_unit"),
        "start": entry.get("start"),
        "end": entry.get("end"),
        "duration_days": sec_duration_days(entry),
        "filed": entry.get("filed"),
        "form": entry.get("form"),
        "fiscal_year": entry.get("fy"),
        "fiscal_period": entry.get("fp"),
        "frame": entry.get("frame"),
        "accession": entry.get("accn"),
    }


def sec_concept_entries(company_facts, concepts):
    us_gaap = (
        company_facts
        .get("facts", {})
        .get("us-gaap", {})
    )

    collected = []

    for concept in concepts:
        fact = us_gaap.get(concept)

        if not fact:
            continue

        for unit_name, entries in fact.get("units", {}).items():
            if unit_name not in {"USD", "shares", "USD/shares"}:
                continue

            for original in entries:
                if original.get("form") not in {"10-K", "10-Q"}:
                    continue

                if original.get("val") is None:
                    continue

                entry = dict(original)
                entry["_unit"] = unit_name

                collected.append(
                    {
                        "concept": concept,
                        "fact": fact,
                        "entry": entry,
                    }
                )

        if collected:
            break

    return collected


def unique_sec_entries(items):
    seen = set()
    result = []

    for item in items:
        entry = item["entry"]

        key = (
            entry.get("val"),
            entry.get("_unit"),
            entry.get("start"),
            entry.get("end"),
            entry.get("form"),
        )

        if key in seen:
            continue

        seen.add(key)
        result.append(item)

    return result


def select_sec_latest_instant(company_facts, concepts):
    items = sec_concept_entries(company_facts, concepts)

    valid = [
        item
        for item in items
        if item["entry"].get("end")
        and not item["entry"].get("start")
    ]

    valid = unique_sec_entries(valid)

    if not valid:
        return None

    valid.sort(
        key=lambda item: (
            item["entry"].get("end", ""),
            item["entry"].get("filed", ""),
        ),
        reverse=True,
    )

    selected = valid[0]

    return normalize_sec_fact(
        selected["concept"],
        selected["fact"],
        selected["entry"],
    )


def select_sec_latest_duration(company_facts, concepts, mode):
    items = sec_concept_entries(company_facts, concepts)
    valid = []

    for item in items:
        days = sec_duration_days(item["entry"])

        if days is None:
            continue

        if mode == "quarter" and 70 <= days <= 120:
            valid.append(item)

        elif mode == "ytd" and 121 <= days <= 300:
            valid.append(item)

        elif mode == "annual" and 300 <= days <= 430:
            valid.append(item)

    valid = unique_sec_entries(valid)

    if not valid:
        return None

    valid.sort(
        key=lambda item: (
            item["entry"].get("end", ""),
            item["entry"].get("filed", ""),
        ),
        reverse=True,
    )

    selected = valid[0]

    return normalize_sec_fact(
        selected["concept"],
        selected["fact"],
        selected["entry"],
    )


def select_sec_prior_comparable(
    company_facts,
    concepts,
    current_fact,
):
    if not current_fact:
        return None

    current_end = parse_sec_date(current_fact.get("end"))
    current_days = current_fact.get("duration_days")

    if not current_end or current_days is None:
        return None

    candidates = []

    for item in sec_concept_entries(company_facts, concepts):
        entry = item["entry"]
        end = parse_sec_date(entry.get("end"))
        days = sec_duration_days(entry)

        if not end or days is None:
            continue

        day_gap = (current_end - end).days

        if not 330 <= day_gap <= 400:
            continue

        if abs(days - current_days) > 15:
            continue

        candidates.append(item)

    candidates = unique_sec_entries(candidates)

    if not candidates:
        return None

    candidates.sort(
        key=lambda item: (
            item["entry"].get("end", ""),
            item["entry"].get("filed", ""),
        ),
        reverse=True,
    )

    selected = candidates[0]

    return normalize_sec_fact(
        selected["concept"],
        selected["fact"],
        selected["entry"],
    )


def sec_percentage_change(current_fact, prior_fact):
    if not current_fact or not prior_fact:
        return None

    current = current_fact.get("value")
    prior = prior_fact.get("value")

    if not isinstance(current, (int, float)):
        return None

    if not isinstance(prior, (int, float)) or prior == 0:
        return None

    return round(((current / prior) - 1) * 100, 2)


def safe_ratio(numerator, denominator):
    if not isinstance(numerator, (int, float)):
        return None

    if not isinstance(denominator, (int, float)) or denominator == 0:
        return None

    return round((numerator / denominator) * 100, 2)


def get_sec_fundamentals(ticker: str):
    company = find_sec_company(ticker)

    if not company:
        return None

    company_facts = fetch_sec_json(
        SEC_COMPANY_FACTS_URL.format(cik=company["cik"])
    )

    duration_metrics = {}

    for metric_name, concepts in SEC_DURATION_METRICS.items():
        quarter = select_sec_latest_duration(
            company_facts,
            concepts,
            "quarter",
        )

        prior_quarter = select_sec_prior_comparable(
            company_facts,
            concepts,
            quarter,
        )

        duration_metrics[metric_name] = {
            "latest_quarter": quarter,
            "prior_year_quarter": prior_quarter,
            "quarter_yoy_percentage": sec_percentage_change(
                quarter,
                prior_quarter,
            ),
            "latest_ytd": select_sec_latest_duration(
                company_facts,
                concepts,
                "ytd",
            ),
            "latest_annual": select_sec_latest_duration(
                company_facts,
                concepts,
                "annual",
            ),
        }

    instant_metrics = {
        metric_name: select_sec_latest_instant(
            company_facts,
            concepts,
        )
        for metric_name, concepts in SEC_INSTANT_METRICS.items()
    }

    revenue_quarter = (
        duration_metrics
        .get("revenue", {})
        .get("latest_quarter")
        or {}
    ).get("value")

    operating_income_quarter = (
        duration_metrics
        .get("operating_income", {})
        .get("latest_quarter")
        or {}
    ).get("value")

    net_income_quarter = (
        duration_metrics
        .get("net_income", {})
        .get("latest_quarter")
        or {}
    ).get("value")

    assets = (instant_metrics.get("assets") or {}).get("value")
    liabilities = (
        instant_metrics.get("liabilities") or {}
    ).get("value")

    latest_period = None

    for metric in (
        duration_metrics.get("revenue", {}).get("latest_quarter"),
        instant_metrics.get("assets"),
    ):
        if metric and metric.get("end"):
            latest_period = metric["end"]
            break

    return {
        "ticker": company["ticker"],
        "name": company["name"],
        "cik": company["cik"],
        "entity_name": company_facts.get("entityName"),
        "latest_period": latest_period,
        "duration_metrics": duration_metrics,
        "instant_metrics": instant_metrics,
        "derived_metrics": {
            "operating_margin_quarter_percentage": safe_ratio(
                operating_income_quarter,
                revenue_quarter,
            ),
            "net_margin_quarter_percentage": safe_ratio(
                net_income_quarter,
                revenue_quarter,
            ),
            "liabilities_to_assets_percentage": safe_ratio(
                liabilities,
                assets,
            ),
        },
        "source": "SEC EDGAR Company Facts",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def decode_http_body(response) -> bytes:
    raw = response.read()
    encoding = (
        response.headers.get("Content-Encoding", "")
        .strip()
        .lower()
    )

    if encoding == "gzip":
        return gzip.decompress(raw)

    if encoding == "deflate":
        try:
            return zlib.decompress(raw)
        except zlib.error:
            return zlib.decompress(raw, -zlib.MAX_WBITS)

    return raw


def fetch_ecb_reference_rate(currency: str) -> dict:
    clean_currency = currency.strip().upper()

    if clean_currency == "EUR":
        return {
            "currency": "EUR",
            "eur_reference_rate": 1.0,
            "date": None,
        }

    if not re.fullmatch(r"[A-Z]{3}", clean_currency):
        raise ValueError("Código de moeda inválido.")

    cached = ECB_CACHE.get(clean_currency)

    if cached:
        age = time.time() - cached["created_at"]

        if age < ECB_CACHE_TTL_SECONDS:
            return cached["data"]

    url = ECB_DATA_URL.format(currency=clean_currency)

    request = Request(
        url,
        headers={
            "Accept": "text/csv",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "ThesisOS/0.8",
        },
    )

    with urlopen(request, timeout=25) as response:
        body = decode_http_body(response).decode("utf-8-sig")

    reader = csv.DictReader(io.StringIO(body))
    selected = None

    for row in reader:
        row_currency = (
            row.get("CURRENCY")
            or row.get("currency")
        )

        value = (
            row.get("OBS_VALUE")
            or row.get("obs_value")
        )

        period = (
            row.get("TIME_PERIOD")
            or row.get("time_period")
        )

        if not row_currency or not value:
            continue

        if row_currency.upper() != clean_currency:
            continue

        selected = {
            "currency": clean_currency,
            "eur_reference_rate": float(value),
            "date": period,
        }

    if not selected:
        raise ValueError(
            f"Não foi encontrada uma taxa ECB para {clean_currency}."
        )

    ECB_CACHE[clean_currency] = {
        "created_at": time.time(),
        "data": selected,
    }

    return selected


def convert_currency(
    amount: float,
    from_currency: str,
    to_currency: str,
) -> dict:
    source = from_currency.strip().upper()
    target = to_currency.strip().upper()

    if not re.fullmatch(r"[A-Z]{3}", source):
        raise ValueError("Moeda de origem inválida.")

    if not re.fullmatch(r"[A-Z]{3}", target):
        raise ValueError("Moeda de destino inválida.")

    source_rate = fetch_ecb_reference_rate(source)
    target_rate = fetch_ecb_reference_rate(target)

    source_reference = source_rate["eur_reference_rate"]
    target_reference = target_rate["eur_reference_rate"]

    conversion_rate = target_reference / source_reference
    converted_amount = amount * conversion_rate

    rate_date = (
        source_rate.get("date")
        or target_rate.get("date")
    )

    return {
        "from_currency": source,
        "to_currency": target,
        "amount": amount,
        "conversion_rate": conversion_rate,
        "converted_amount": converted_amount,
        "rate_date": rate_date,
        "source_reference_rate": source_reference,
        "target_reference_rate": target_reference,
        "source": "ECB Data Portal",
        "rate_type": "official_reference_rate",
        "note": (
            "Taxa de referência oficial. "
            "Não representa necessariamente o câmbio executável "
            "por uma corretora ou banco."
        ),
    }

def classify_figi_asset(item: dict) -> str:
    security_type = str(item.get("securityType") or "").lower()
    security_type_2 = str(item.get("securityType2") or "").lower()

    if security_type == "etp":
        return "etf"

    if "common stock" in security_type:
        return "stock"

    if "common stock" in security_type_2:
        return "stock"

    if "mutual fund" in security_type_2:
        return "fund"

    if "index" in security_type:
        return "index"

    return "other"


def normalize_figi_match(item: dict) -> dict:
    return {
        "figi": item.get("figi"),
        "composite_figi": item.get("compositeFIGI"),
        "share_class_figi": item.get("shareClassFIGI"),
        "ticker": item.get("ticker"),
        "name": item.get("name"),
        "exchange_code": item.get("exchCode"),
        "market_sector": item.get("marketSector"),
        "security_type": item.get("securityType"),
        "security_type_2": item.get("securityType2"),
        "asset_type": classify_figi_asset(item),
    }


def rank_figi_match(item: dict, query: str) -> tuple:
    ticker = str(item.get("ticker") or "").upper()
    exchange = str(item.get("exchCode") or "").upper()
    asset_type = classify_figi_asset(item)

    exact_ticker = 1 if ticker == query.upper() else 0
    supported_type = 1 if asset_type in {"stock", "etf"} else 0
    preferred_exchange = 1 if exchange in {
        "US",
        "UN",
        "UW",
        "UR",
        "GR",
        "GF",
        "GD",
        "LN",
        "NA",
        "IM",
    } else 0

    return (
        exact_ticker,
        supported_type,
        preferred_exchange,
    )


def deduplicate_figi_matches(matches: list) -> list:
    seen = set()
    unique = []

    for item in matches:
        key = (
            item.get("figi"),
            item.get("ticker"),
            item.get("exchCode"),
        )

        if key in seen:
            continue

        seen.add(key)
        unique.append(item)

    return unique



def classify_eodhd_asset(item: dict) -> str:
    asset_type = str(item.get("Type") or "").lower()

    if asset_type == "etf":
        return "etf"

    if asset_type in {"common stock", "stock"}:
        return "stock"

    if asset_type in {"fund", "mutual fund"}:
        return "fund"

    if asset_type == "index":
        return "index"

    return "other"


def normalize_eodhd_match(item: dict) -> dict:
    return {
        "figi": None,
        "composite_figi": None,
        "share_class_figi": None,
        "ticker": item.get("Code"),
        "name": item.get("Name"),
        "exchange_code": item.get("Exchange"),
        "market_sector": "Equity",
        "security_type": item.get("Type"),
        "security_type_2": (
            "Mutual Fund"
            if str(item.get("Type") or "").upper() == "ETF"
            else item.get("Type")
        ),
        "asset_type": classify_eodhd_asset(item),
        "country": item.get("Country"),
        "currency": item.get("Currency"),
        "isin": item.get("ISIN"),
        "previous_close": item.get("previousClose"),
        "previous_close_date": item.get("previousCloseDate"),
        "quote_provider": "EODHD",
        "quote_type": "previous_close",
    }


def rank_eodhd_match(item: dict) -> tuple:
    exchange = str(item.get("Exchange") or "").upper()
    preferred_order = {
        "XETRA": 5,
        "LSE": 4,
        "F": 3,
        "MI": 2,
        "AS": 1,
    }

    return (
        preferred_order.get(exchange, 0),
        1 if str(item.get("Type") or "").upper() == "ETF" else 0,
    )

def search_openfigi(query: str) -> dict:
    clean_query = query.strip().upper()

    is_isin = bool(
        re.fullmatch(
            r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
            clean_query,
        )
    )

    if is_isin:
        job = {
            "idType": "ID_ISIN",
            "idValue": clean_query,
        }
        query_type = "isin"
    else:
        job = {
            "idType": "TICKER",
            "idValue": clean_query,
        }
        query_type = "ticker"

    matches = fetch_openfigi_mapping(job)
    matches = deduplicate_figi_matches(matches)

    matches.sort(
        key=lambda item: rank_figi_match(item, clean_query),
        reverse=True,
    )

    normalized = [
        normalize_figi_match(item)
        for item in matches[:20]
    ]

    return {
        "query": clean_query,
        "query_type": query_type,
        "total_results": len(matches),
        "returned_results": len(normalized),
        "requires_selection": len(matches) > 1,
        "results": normalized,
        "source": "OpenFIGI",
    }



def search_assets(query: str) -> dict:
    clean_query = query.strip().upper()
    figi_result = search_openfigi(clean_query)

    is_isin = bool(
        re.fullmatch(
            r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
            clean_query,
        )
    )

    if not is_isin:
        return figi_result

    eodhd_matches = fetch_eodhd_search(clean_query)

    exact_matches = [
        item
        for item in eodhd_matches
        if str(item.get("ISIN") or "").upper() == clean_query
    ]

    exact_matches.sort(
        key=rank_eodhd_match,
        reverse=True,
    )

    normalized = [
        normalize_eodhd_match(item)
        for item in exact_matches[:20]
    ]

    if not normalized:
        return figi_result

    return {
        "query": clean_query,
        "query_type": "isin",
        "total_results": len(exact_matches),
        "returned_results": len(normalized),
        "requires_selection": len(normalized) > 1,
        "results": normalized,
        "source": "OpenFIGI + EODHD",
        "openfigi_total_results": figi_result["total_results"],
    }

def identify_us_symbol(symbol: str) -> dict | None:
    matches = fetch_openfigi_mapping(
        {
            "idType": "TICKER",
            "idValue": symbol,
            "exchCode": "US",
        }
    )

    if not matches:
        return None

    matches.sort(
        key=lambda item: rank_figi_match(item, symbol),
        reverse=True,
    )

    return normalize_figi_match(matches[0])


def build_us_asset_snapshot(symbol: str) -> dict | None:
    clean_symbol = symbol.strip().upper()

    profile = fetch_finnhub(
        "stock/profile2",
        {"symbol": clean_symbol},
    )

    quote_data = fetch_finnhub(
        "quote",
        {"symbol": clean_symbol},
    )

    figi_asset = None

    try:
        figi_asset = identify_us_symbol(clean_symbol)
    except Exception:
        # A cotação pode continuar disponível mesmo que a
        # identificação OpenFIGI esteja temporariamente indisponível.
        figi_asset = None

    has_profile = bool(profile.get("name"))
    has_quote = quote_data.get("c") not in (None, 0)

    if not has_profile and not has_quote and not figi_asset:
        return None

    quote_timestamp = quote_data.get("t")
    updated_at = None

    if quote_timestamp:
        updated_at = datetime.fromtimestamp(
            quote_timestamp,
            tz=timezone.utc,
        ).isoformat()

    asset_type = (
        figi_asset.get("asset_type")
        if figi_asset
        else "stock"
    )

    exchange = (
        profile.get("exchange")
        or (
            figi_asset.get("exchange_code")
            if figi_asset
            else None
        )
    )

    exchange_code = (
        figi_asset.get("exchange_code")
        if figi_asset
        else None
    )

    is_us_listing = str(exchange_code or "").upper() in {
        "US",
        "UN",
        "UW",
        "UR",
    }

    return {
        "symbol": profile.get("ticker") or clean_symbol,
        "name": (
            profile.get("name")
            or (
                figi_asset.get("name")
                if figi_asset
                else None
            )
            or clean_symbol
        ),
        "asset_type": asset_type,

        "price": quote_data.get("c"),
        "change": quote_data.get("d"),
        "change_percentage": quote_data.get("dp"),

        "open": quote_data.get("o"),
        "high": quote_data.get("h"),
        "low": quote_data.get("l"),
        "previous_close": quote_data.get("pc"),
        "quote_type": "intraday_or_latest",
        "quote_date": (
            updated_at[:10]
            if updated_at
            else None
        ),

        "currency": (
            profile.get("currency")
            or ("USD" if is_us_listing else None)
        ),
        "exchange": exchange,
        "exchange_code": exchange_code,
        "country": (
            profile.get("country")
            or ("US" if is_us_listing else None)
        ),

        "sector": None,
        "industry": (
            profile.get("finnhubIndustry")
            if asset_type == "stock"
            else None
        ),

        "figi": (
            figi_asset.get("figi")
            if figi_asset
            else None
        ),
        "security_type": (
            figi_asset.get("security_type")
            if figi_asset
            else None
        ),
        "security_type_2": (
            figi_asset.get("security_type_2")
            if figi_asset
            else None
        ),

        "isin": None,
        "ipo_date": profile.get("ipo"),
        "market_cap_millions": profile.get(
            "marketCapitalization"
        ),
        "shares_outstanding_millions": profile.get(
            "shareOutstanding"
        ),

        "website": profile.get("weburl"),
        "logo": profile.get("logo"),

        "updated_at": updated_at,
        "source": "Finnhub + OpenFIGI",
        "market_provider": "Finnhub",
        "identification_provider": (
            "OpenFIGI"
            if figi_asset
            else "Finnhub"
        ),
    }


def select_analysis_listing(
    search_result: dict,
    exchange: str | None,
    ticker: str | None,
) -> dict | None:
    results = search_result.get("results", [])

    if not results:
        return None

    clean_exchange = (
        exchange.strip().upper()
        if exchange
        else None
    )

    clean_ticker = (
        ticker.strip().upper()
        if ticker
        else None
    )

    candidates = results

    if clean_exchange:
        candidates = [
            item
            for item in candidates
            if str(
                item.get("exchange_code") or ""
            ).upper() == clean_exchange
        ]

    if clean_ticker:
        candidates = [
            item
            for item in candidates
            if str(
                item.get("ticker") or ""
            ).upper() == clean_ticker
        ]

    if not candidates:
        return None

    if len(results) > 1 and not clean_exchange and not clean_ticker:
        return None

    return candidates[0]


def build_eodhd_asset_snapshot(
    identifier: str,
    exchange: str | None,
    ticker: str | None,
) -> tuple[dict | None, dict]:
    search_result = search_assets(identifier)

    selected = select_analysis_listing(
        search_result,
        exchange,
        ticker,
    )

    if not selected:
        return None, search_result

    price = selected.get("previous_close")
    quote_date = selected.get("previous_close_date")

    return {
        "symbol": selected.get("ticker"),
        "name": selected.get("name"),
        "asset_type": selected.get("asset_type"),

        "price": price,
        "change": None,
        "change_percentage": None,

        "open": None,
        "high": None,
        "low": None,
        "previous_close": price,
        "quote_type": "previous_close",
        "quote_date": quote_date,

        "currency": selected.get("currency"),
        "exchange": selected.get("exchange_code"),
        "exchange_code": selected.get("exchange_code"),
        "country": selected.get("country"),

        "sector": None,
        "industry": None,

        "figi": selected.get("figi"),
        "security_type": selected.get("security_type"),
        "security_type_2": selected.get("security_type_2"),

        "isin": (
            selected.get("isin")
            or identifier.strip().upper()
        ),
        "ipo_date": None,
        "market_cap_millions": None,
        "shares_outstanding_millions": None,

        "website": None,
        "logo": None,

        "updated_at": quote_date,
        "source": "OpenFIGI + EODHD",
        "market_provider": "EODHD",
        "identification_provider": "OpenFIGI + EODHD",
    }, search_result


def analysis_source(
    provider: str,
    status: str,
    **details,
) -> dict:
    return {
        "provider": provider,
        "status": status,
        **details,
    }


def build_analysis_payload(
    identifier: str,
    base_currency: str = "EUR",
    exchange: str | None = None,
    ticker: str | None = None,
) -> tuple[dict, int]:
    clean_identifier = identifier.strip().upper()
    clean_base_currency = base_currency.strip().upper()

    if not re.fullmatch(r"[A-Z]{3}", clean_base_currency):
        raise ValueError("Moeda-base inválida.")

    is_isin = bool(
        re.fullmatch(
            r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
            clean_identifier,
        )
    )

    warnings = []
    sources = {}

    if is_isin:
        asset, search_result = build_eodhd_asset_snapshot(
            clean_identifier,
            exchange,
            ticker,
        )

        if not asset:
            return {
                "error": (
                    "A pesquisa devolveu várias listagens. "
                    "Escolhe uma bolsa ou um ticker."
                ),
                "query": clean_identifier,
                "requires_selection": True,
                "selection_options": search_result.get(
                    "results",
                    [],
                ),
                "source": search_result.get("source"),
            }, 409

        sources["identification"] = analysis_source(
            asset["identification_provider"],
            "ok",
            isin=asset.get("isin"),
            exchange=asset.get("exchange"),
        )

        sources["market"] = analysis_source(
            "EODHD",
            (
                "ok"
                if asset.get("price") is not None
                else "unavailable"
            ),
            quote_type=asset.get("quote_type"),
            quote_date=asset.get("quote_date"),
        )

    else:
        asset = build_us_asset_snapshot(clean_identifier)

        if not asset:
            return {
                "error": "Ativo não encontrado.",
                "query": clean_identifier,
            }, 404

        sources["identification"] = analysis_source(
            asset["identification_provider"],
            "ok",
            figi=asset.get("figi"),
            exchange=asset.get("exchange"),
        )

        sources["market"] = analysis_source(
            "Finnhub",
            (
                "ok"
                if asset.get("price") is not None
                else "unavailable"
            ),
            quote_type=asset.get("quote_type"),
            quote_date=asset.get("quote_date"),
        )

    fundamentals = None

    if asset.get("asset_type") == "stock":
        try:
            fundamentals = get_sec_fundamentals(
                asset["symbol"]
            )

            if fundamentals:
                sources["fundamentals"] = analysis_source(
                    "SEC EDGAR",
                    "ok",
                    latest_period=(
                        fundamentals.get("latest_period")
                    ),
                )
            else:
                sources["fundamentals"] = analysis_source(
                    "SEC EDGAR",
                    "unavailable",
                )
                warnings.append(
                    "A SEC não devolveu fundamentais "
                    "para este ticker."
                )

        except Exception as error:
            sources["fundamentals"] = analysis_source(
                "SEC EDGAR",
                "unavailable",
                detail=str(error),
            )
            warnings.append(
                "Os fundamentais oficiais estão "
                "temporariamente indisponíveis."
            )

    else:
        sources["fundamentals"] = analysis_source(
            "SEC EDGAR",
            "not_applicable",
            reason="O ativo não é uma ação.",
        )

    fx = None
    price = asset.get("price")
    currency = asset.get("currency")

    if (
        isinstance(price, (int, float))
        and currency
    ):
        try:
            fx = convert_currency(
                float(price),
                currency,
                clean_base_currency,
            )

            sources["fx"] = analysis_source(
                "ECB Data Portal",
                "ok",
                rate_date=fx.get("rate_date"),
                base_currency=clean_base_currency,
            )

        except Exception as error:
            sources["fx"] = analysis_source(
                "ECB Data Portal",
                "unavailable",
                detail=str(error),
            )
            warnings.append(
                "A conversão cambial está "
                "temporariamente indisponível."
            )

    else:
        sources["fx"] = analysis_source(
            "ECB Data Portal",
            "unavailable",
            reason=(
                "Preço ou moeda do ativo indisponível."
            ),
        )

    required_source_keys = [
        "identification",
        "market",
        "fx",
    ]

    if asset.get("asset_type") == "stock":
        required_source_keys.append("fundamentals")

    available_count = sum(
        1
        for key in required_source_keys
        if sources.get(key, {}).get("status") == "ok"
    )

    completeness_percentage = round(
        (
            available_count
            / len(required_source_keys)
        ) * 100
    )

    payload = {
        "query": clean_identifier,
        "generated_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "base_currency": clean_base_currency,
        "asset": asset,
        "market": {
            "price": asset.get("price"),
            "currency": asset.get("currency"),
            "change": asset.get("change"),
            "change_percentage": (
                asset.get("change_percentage")
            ),
            "open": asset.get("open"),
            "high": asset.get("high"),
            "low": asset.get("low"),
            "previous_close": (
                asset.get("previous_close")
            ),
            "quote_type": asset.get("quote_type"),
            "quote_date": asset.get("quote_date"),
            "provider": asset.get("market_provider"),
        },
        "fundamentals": fundamentals,
        "fx": fx,
        "sources": sources,
        "data_quality": {
            "completeness_percentage": (
                completeness_percentage
            ),
            "available_sources": available_count,
            "required_sources": len(
                required_source_keys
            ),
            "ready_for_rule_engine": (
                completeness_percentage == 100
            ),
        },
        "warnings": warnings,
    }

    return payload, 200


class ThesisOSHandler(SimpleHTTPRequestHandler):
    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

        self.send_response(status)
        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8",
        )
        self.send_header(
            "Content-Length",
            str(len(body)),
        )
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/api/health":
            self.send_json(
                {
                    "status": "ok",
                    "service": "ThesisOS API",
                    "providers": [
                        "Finnhub",
                        "OpenFIGI",
                        "EODHD",
                        "SEC EDGAR",
                        "ECB Data Portal",
                    ],
                }
            )
            return

        if parsed_url.path.startswith("/api/analysis/"):
            self.handle_analysis_request(parsed_url)
            return

        if parsed_url.path.startswith("/api/fx/"):
            self.handle_fx_request(parsed_url)
            return

        if parsed_url.path.startswith("/api/search/"):
            self.handle_search_request(parsed_url.path)
            return

        if parsed_url.path.startswith("/api/fundamentals/"):
            self.handle_fundamentals_request(parsed_url.path)
            return

        if parsed_url.path.startswith("/api/asset/"):
            self.handle_asset_request(parsed_url.path)
            return

        super().do_GET()

    def handle_analysis_request(self, parsed_url) -> None:
        identifier = unquote(
            parsed_url.path.removeprefix("/api/analysis/")
        ).strip()

        if not identifier or len(identifier) > 64:
            self.send_json(
                {"error": "Identificador inválido."},
                status=400,
            )
            return

        query = parse_qs(parsed_url.query)

        base_currency = query.get(
            "base_currency",
            ["EUR"],
        )[0]

        exchange = query.get(
            "exchange",
            [None],
        )[0]

        ticker = query.get(
            "ticker",
            [None],
        )[0]

        try:
            payload, status = build_analysis_payload(
                identifier,
                base_currency=base_currency,
                exchange=exchange,
                ticker=ticker,
            )

            self.send_json(
                payload,
                status=status,
            )

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )

            self.send_json(
                {
                    "error": "Um fornecedor recusou o pedido.",
                    "status": error.code,
                    "detail": detail[:500],
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": (
                        "Não foi possível contactar "
                        "um dos fornecedores."
                    ),
                },
                status=502,
            )

        except ValueError as error:
            self.send_json(
                {
                    "error": str(error),
                },
                status=400,
            )

        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao agregar a análise.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_fx_request(self, parsed_url) -> None:
        route = parsed_url.path.removeprefix("/api/fx/")
        parts = [
            unquote(part).strip().upper()
            for part in route.split("/")
            if part.strip()
        ]

        if len(parts) != 2:
            self.send_json(
                {
                    "error": (
                        "Usa o formato "
                        "/api/fx/MOEDA_ORIGEM/MOEDA_DESTINO."
                    )
                },
                status=400,
            )
            return

        from_currency, to_currency = parts
        query = parse_qs(parsed_url.query)

        raw_amount = query.get("amount", ["1"])[0]

        try:
            amount = float(raw_amount)

        except (TypeError, ValueError):
            self.send_json(
                {"error": "O montante é inválido."},
                status=400,
            )
            return

        if amount < 0 or amount > 1_000_000_000_000:
            self.send_json(
                {"error": "O montante está fora do intervalo permitido."},
                status=400,
            )
            return

        try:
            result = convert_currency(
                amount,
                from_currency,
                to_currency,
            )

            self.send_json(result)

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )

            self.send_json(
                {
                    "error": "O BCE recusou o pedido.",
                    "status": error.code,
                    "detail": detail[:500],
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": "Não foi possível contactar o BCE.",
                },
                status=502,
            )

        except ValueError as error:
            self.send_json(
                {
                    "error": str(error),
                },
                status=404,
            )

        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao converter a moeda.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_search_request(self, path: str) -> None:
        query = unquote(
            path.removeprefix("/api/search/")
        ).strip()

        if not query or len(query) > 64:
            self.send_json(
                {"error": "Pesquisa inválida."},
                status=400,
            )
            return

        try:
            result = search_assets(query)

            if not result["results"]:
                self.send_json(
                    {
                        "error": "Nenhum ativo encontrado.",
                        "query": query,
                    },
                    status=404,
                )
                return

            self.send_json(result)

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )

            self.send_json(
                {
                    "error": "A OpenFIGI recusou o pedido.",
                    "status": error.code,
                    "detail": detail,
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": "Não foi possível contactar a OpenFIGI.",
                },
                status=502,
            )

        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao pesquisar o ativo.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_fundamentals_request(self, path: str) -> None:
        ticker = (
            unquote(path.removeprefix("/api/fundamentals/"))
            .strip()
            .upper()
        )

        if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", ticker):
            self.send_json(
                {"error": "Ticker inválido."},
                status=400,
            )
            return

        try:
            fundamentals = get_sec_fundamentals(ticker)

            if not fundamentals:
                self.send_json(
                    {
                        "error": (
                            "A SEC não encontrou fundamentais "
                            "para este ticker."
                        ),
                        "ticker": ticker,
                    },
                    status=404,
                )
                return

            self.send_json(fundamentals)

        except HTTPError as error:
            detail = error.read().decode(
                "utf-8",
                errors="replace",
            )

            self.send_json(
                {
                    "error": "A SEC recusou o pedido.",
                    "status": error.code,
                    "detail": detail[:500],
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": "Não foi possível contactar a SEC.",
                },
                status=502,
            )

        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao consultar fundamentais.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_asset_request(self, path: str) -> None:
        symbol = (
            unquote(path.removeprefix("/api/asset/"))
            .strip()
            .upper()
        )

        if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol):
            self.send_json(
                {"error": "Ticker inválido."},
                status=400,
            )
            return

        try:
            profile = fetch_finnhub(
                "stock/profile2",
                {"symbol": symbol},
            )

            quote_data = fetch_finnhub(
                "quote",
                {"symbol": symbol},
            )

            figi_asset = identify_us_symbol(symbol)

            has_profile = bool(profile.get("name"))
            has_quote = quote_data.get("c") not in (None, 0)

            if not has_profile and not has_quote and not figi_asset:
                self.send_json(
                    {
                        "error": "Ativo não encontrado.",
                        "symbol": symbol,
                    },
                    status=404,
                )
                return

            quote_timestamp = quote_data.get("t")

            updated_at = None

            if quote_timestamp:
                updated_at = datetime.fromtimestamp(
                    quote_timestamp,
                    tz=timezone.utc,
                ).isoformat()

            asset_type = (
                figi_asset.get("asset_type")
                if figi_asset
                else "stock"
            )

            asset = {
                "symbol": profile.get("ticker") or symbol,
                "name": (
                    profile.get("name")
                    or (
                        figi_asset.get("name")
                        if figi_asset
                        else None
                    )
                    or symbol
                ),
                "asset_type": asset_type,

                "price": quote_data.get("c"),
                "change": quote_data.get("d"),
                "change_percentage": quote_data.get("dp"),

                "open": quote_data.get("o"),
                "high": quote_data.get("h"),
                "low": quote_data.get("l"),
                "previous_close": quote_data.get("pc"),

                "currency": profile.get("currency"),
                "exchange": (
                    profile.get("exchange")
                    or (
                        figi_asset.get("exchange_code")
                        if figi_asset
                        else None
                    )
                ),
                "country": profile.get("country"),

                "sector": None,
                "industry": (
                    profile.get("finnhubIndustry")
                    if asset_type == "stock"
                    else None
                ),

                "figi": (
                    figi_asset.get("figi")
                    if figi_asset
                    else None
                ),
                "security_type": (
                    figi_asset.get("security_type")
                    if figi_asset
                    else None
                ),
                "security_type_2": (
                    figi_asset.get("security_type_2")
                    if figi_asset
                    else None
                ),

                "ipo_date": profile.get("ipo"),
                "market_cap_millions": profile.get(
                    "marketCapitalization"
                ),
                "shares_outstanding_millions": profile.get(
                    "shareOutstanding"
                ),

                "website": profile.get("weburl"),
                "logo": profile.get("logo"),

                "updated_at": updated_at,
                "source": "Finnhub + OpenFIGI",
                "metadata_source": "OpenFIGI",
            }

            self.send_json(asset)

        except HTTPError as error:
            self.send_json(
                {
                    "error": "Um fornecedor recusou o pedido.",
                    "status": error.code,
                    "detail": (
                        "Verifica as API keys ou os limites "
                        "das contas."
                    ),
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": "Não foi possível contactar os fornecedores.",
                },
                status=502,
            )

        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao consultar o ativo.",
                    "detail": str(error),
                },
                status=500,
            )


server = ThreadingHTTPServer(
    (HOST, PORT),
    ThesisOSHandler,
)

print(f"ThesisOS disponível na porta {PORT}")

try:
    server.serve_forever()
except KeyboardInterrupt:
    print("\nServidor encerrado.")
    server.server_close()
