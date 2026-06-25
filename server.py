import csv
import gzip
import io
import json
import math
import os
import re
import time
import zlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import (
    parse_qs,
    quote,
    unquote,
    urlencode,
    urljoin,
    urlparse,
)
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
SEC_SUBMISSIONS_URL = (
    "https://data.sec.gov/submissions/CIK{cik}.json"
)
SEC_ARCHIVES_URL = (
    "https://www.sec.gov/Archives/edgar/data/"
    "{cik}/{accession}/{primary_document}"
)

OPENFIGI_CACHE_TTL_SECONDS = 24 * 60 * 60
EODHD_CACHE_TTL_SECONDS = 24 * 60 * 60
SEC_CACHE_TTL_SECONDS = 24 * 60 * 60
ECB_CACHE_TTL_SECONDS = 12 * 60 * 60
ETF_PROFILE_CACHE_TTL_SECONDS = 24 * 60 * 60
TECHNICAL_CACHE_TTL_SECONDS = 30 * 60
RADAR_CACHE_TTL_SECONDS = 30 * 60
RADAR_DISCOVERY_CACHE_TTL_SECONDS = 30 * 60
EVIDENCE_CACHE_TTL_SECONDS = 30 * 60
SEC_SUBMISSIONS_CACHE_TTL_SECONDS = 60 * 60
OPENFIGI_CACHE = {}
EODHD_CACHE = {}
SEC_CACHE = {}
ECB_CACHE = {}
ETF_PROFILE_CACHE = {}
TECHNICAL_CACHE = {}
RADAR_CACHE = {}
RADAR_DISCOVERY_CACHE = {}

LOGO_CACHE_TTL_SECONDS = 24 * 60 * 60
LOGO_CACHE = {}
EVIDENCE_CACHE = {}
SEC_SUBMISSIONS_CACHE = {}

SYNC_MAX_REQUEST_BYTES = 2_500_000
SYNC_MAX_NAMESPACE_BYTES = 750_000
SYNC_ALLOWED_NAMESPACES = {
    "watchlist",
    "portfolio_transactions",
    "portfolio_quotes",
    "decision_journal",
    "monitoring_alerts",
    "investor_policy",
    "portfolio_construction",
    "last_radar",
    "last_evidence",
}


def validate_cloud_state(raw_state):
    if not isinstance(raw_state, dict):
        raise ValueError("O estado de sincronização deve ser um objeto JSON.")
    cleaned = {}
    for namespace, payload in raw_state.items():
        if namespace not in SYNC_ALLOWED_NAMESPACES:
            continue
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > SYNC_MAX_NAMESPACE_BYTES:
            raise ValueError(
                f"O namespace {namespace} excede o limite de sincronização."
            )
        cleaned[namespace] = payload
    if not cleaned:
        raise ValueError("Não foram recebidos namespaces válidos.")
    return cleaned


# ---------------------------------------------------------------------------
# Supabase Auth + per-user Cloud Sync
# ---------------------------------------------------------------------------
AUTH_USER_STATE_TABLE = "thesisos_user_state"


def auth_publishable_key():
    return (
        os.getenv("SUPABASE_PUBLISHABLE_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    ).strip()


def auth_configuration():
    url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
    publishable_key = auth_publishable_key()
    missing = []
    if not url:
        missing.append("SUPABASE_URL")
    if not publishable_key:
        missing.append("SUPABASE_PUBLISHABLE_KEY ou SUPABASE_ANON_KEY")
    return {
        "configured": not missing,
        "url": url,
        "publishable_key": publishable_key,
        "missing": missing,
        "provider": "Supabase Auth",
        "table": AUTH_USER_STATE_TABLE,
    }


def bearer_token_from_headers(headers):
    value = str(headers.get("Authorization") or "").strip()
    if not value.lower().startswith("bearer "):
        return None
    token = value[7:].strip()
    return token or None


def supabase_auth_user(access_token):
    config = auth_configuration()
    if not config["configured"]:
        raise RuntimeError(
            "Supabase Auth não configurado: " + ", ".join(config["missing"])
        )
    if not access_token:
        raise PermissionError("Token de autenticação em falta.")
    request = Request(
        f'{config["url"]}/auth/v1/user',
        headers={
            "apikey": config["publishable_key"],
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
            "User-Agent": "ThesisOS/1.1 SupabaseAuth",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code in {401, 403}:
            raise PermissionError("Sessão Supabase inválida ou expirada.") from error
        raise RuntimeError(
            f"Supabase Auth respondeu HTTP {error.code}: {detail[:500]}"
        ) from error


def supabase_user_state_request(
    access_token,
    method="GET",
    query="",
    payload=None,
    prefer=None,
):
    config = auth_configuration()
    if not config["configured"]:
        raise RuntimeError(
            "Supabase Auth não configurado: " + ", ".join(config["missing"])
        )
    url = f'{config["url"]}/rest/v1/{AUTH_USER_STATE_TABLE}'
    if query:
        url += "?" + query
    headers = {
        "apikey": config["publishable_key"],
        "Authorization": f"Bearer {access_token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "ThesisOS/1.1 UserCloudSync",
    }
    if prefer:
        headers["Prefer"] = prefer
    body = None if payload is None else json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(url, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=25) as response:
            raw = response.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        if error.code in {401, 403}:
            raise PermissionError(
                "A sessão não tem autorização para aceder aos dados."
            ) from error
        raise RuntimeError(
            f"Supabase respondeu HTTP {error.code}: {detail[:500]}"
        ) from error


def fetch_user_cloud_state(user_id, access_token):
    query = urlencode({
        "select": "namespace,payload,version,updated_at",
        "user_id": f"eq.{user_id}",
        "order": "namespace.asc",
    })
    rows = supabase_user_state_request(
        access_token,
        "GET",
        query=query,
    ) or []
    state = {}
    metadata = {}
    for row in rows:
        namespace = row.get("namespace")
        if namespace not in SYNC_ALLOWED_NAMESPACES:
            continue
        state[namespace] = row.get("payload")
        metadata[namespace] = {
            "version": row.get("version"),
            "updated_at": row.get("updated_at"),
        }
    return {"state": state, "metadata": metadata, "row_count": len(state)}


def upsert_user_cloud_state(user_id, access_token, raw_state):
    state = validate_cloud_state(raw_state)
    version = int(time.time() * 1000)
    delete_namespaces = sorted(
        namespace
        for namespace, payload in state.items()
        if payload is None
    )
    upsert_state = {
        namespace: payload
        for namespace, payload in state.items()
        if payload is not None
    }

    if delete_namespaces:
        namespace_filter = "in.(" + ",".join(delete_namespaces) + ")"
        delete_query = urlencode({
            "user_id": f"eq.{user_id}",
            "namespace": namespace_filter,
        })
        supabase_user_state_request(
            access_token,
            "DELETE",
            query=delete_query,
            prefer="return=minimal",
        )

    rows = [
        {
            "user_id": user_id,
            "namespace": namespace,
            "payload": payload,
            "version": version,
        }
        for namespace, payload in upsert_state.items()
    ]
    result = []
    if rows:
        query = urlencode({"on_conflict": "user_id,namespace"})
        result = supabase_user_state_request(
            access_token,
            "POST",
            query=query,
            payload=rows,
            prefer="resolution=merge-duplicates,return=representation",
        ) or []

    return {
        "saved_namespaces": sorted(state),
        "saved_count": len(state),
        "upserted_count": len(upsert_state),
        "deleted_count": len(delete_namespaces),
        "version": version,
        "rows": result,
    }


RADAR_UNIVERSES = {
    "core_us": [
        "MSFT", "AAPL", "GOOGL", "AMZN",
        "MCD", "KO", "ADP", "ZTS",
    ],
    "quality_us": [
        "MSFT", "AAPL", "GOOGL", "MCD",
        "KO", "ADP", "ZTS", "PWR",
    ],
    "growth_us": [
        "NVDA", "AMD", "AMZN", "META",
        "PWR", "SMCI", "IREN", "KRMN",
    ],
}


RADAR_DISCOVERY_UNIVERSES = {
    "discover_quality_us": {
        "label": "US-listed Quality Discovery",
        "asset_type": "EQUITY",
        "sources": [
            "quality_nonfinancial",
            "quality_financial",
            "value_quality",
        ],
        "fallback": RADAR_UNIVERSES["quality_us"],
    },
    "discover_growth_us": {
        "label": "US-listed Profitable Growth Discovery",
        "asset_type": "EQUITY",
        "sources": [
            "profitable_growth",
            "quality_growth",
            "technology_growth",
        ],
        "fallback": RADAR_UNIVERSES["growth_us"],
    },
    "discover_market_us": {
        "label": "US-listed Multi-Factor Discovery",
        "asset_type": "EQUITY",
        "sources": [
            "quality_nonfinancial",
            "quality_financial",
            "profitable_growth",
            "quality_growth",
            "value_quality",
        ],
        "fallback": list(dict.fromkeys(
            RADAR_UNIVERSES["core_us"]
            + RADAR_UNIVERSES["growth_us"]
        )),
    },
    "discover_etf_us": {
        "label": "US ETF Discovery",
        "asset_type": "ETF",
        "sources": [
            "top_etfs_us",
            "top_performing_etfs",
        ],
        "fallback": [
            "VOO", "VTI", "SPY", "QQQ",
            "SCHD", "VXUS", "IWM", "BND",
        ],
    },
}

YAHOO_EXCHANGE_SUFFIX = {
    "XETRA": ".DE",
    "F": ".F",
    "FRANKFURT": ".F",
    "LSE": ".L",
    "L": ".L",
    "MIL": ".MI",
    "MI": ".MI",
    "AS": ".AS",
    "AMS": ".AS",
    "PA": ".PA",
    "SW": ".SW",
    "SIX": ".SW",
    "BR": ".BR",
    "MC": ".MC",
    "LS": ".LS",
}

ETF_OFFICIAL_PROFILE_REGISTRY = {
    "IE00BK5BQT80": {
        "issuer": "Vanguard",
        "source_url": (
            "https://www.vanguard.co.uk/professional/product/etf/"
            "equity/9679/ftse-all-world-ucits-etf-usd-accumulating"
        ),
    },
}


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


SEC_CASHFLOW_METRICS = {
    "operating_cash_flow": [
        "NetCashProvidedByUsedInOperatingActivities",
    ],
    "capital_expenditure": [
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "PaymentsForAdditionsToPropertyPlantAndEquipment",
        "PaymentsToAcquireProductiveAssets",
    ],
    "share_repurchases": [
        "PaymentsForRepurchaseOfCommonStock",
        "PaymentsForRepurchaseOfCommonAndPreferredStock",
    ],
    "dividends_paid": [
        "PaymentsOfDividendsCommonStock",
        "PaymentsOfDividends",
        "PaymentsOfOrdinaryDividends",
    ],
    "stock_based_compensation": [
        "ShareBasedCompensation",
        (
            "ShareBasedCompensationArrangementByShareBasedPayment"
            "AwardEquityInstrumentsOtherThanOptionsGrantsInPeriodTotal"
        ),
    ],
}

SEC_DEBT_METRICS = {
    "short_term_borrowings": [
        "ShortTermBorrowings",
        "CommercialPaper",
        "ShortTermDebtCurrent",
    ],
    "current_long_term_debt": [
        "LongTermDebtCurrent",
        "LongTermDebtAndFinanceLeaseObligationsCurrent",
    ],
    "noncurrent_long_term_debt": [
        "LongTermDebtNoncurrent",
        "LongTermDebtAndFinanceLeaseObligationsNoncurrent",
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

    for concept_priority, concept in enumerate(concepts):
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
                entry["_concept_priority"] = concept_priority

                collected.append(
                    {
                        "concept": concept,
                        "fact": fact,
                        "entry": entry,
                    }
                )

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
            -item["entry"].get("_concept_priority", 999),
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

        elif mode == "interim" and 70 <= days <= 300:
            valid.append(item)

        elif mode == "annual" and 300 <= days <= 430:
            valid.append(item)

    valid = unique_sec_entries(valid)

    if not valid:
        return None

    valid.sort(
        key=lambda item: (
            item["entry"].get("end", ""),
            sec_duration_days(item["entry"]) or 0,
            item["entry"].get("filed", ""),
            -item["entry"].get("_concept_priority", 999),
        ),
        reverse=True,
    )

    selected = valid[0]

    return normalize_sec_fact(
        selected["concept"],
        selected["fact"],
        selected["entry"],
    )


def select_sec_matching_duration(
    company_facts,
    concepts,
    target_fact,
):
    if not target_fact:
        return None

    target_end = target_fact.get("end")
    target_days = target_fact.get("duration_days")

    if not target_end or target_days is None:
        return None

    valid = []

    for item in sec_concept_entries(company_facts, concepts):
        entry = item["entry"]
        days = sec_duration_days(entry)

        if entry.get("end") != target_end:
            continue

        if days is None or abs(days - target_days) > 15:
            continue

        valid.append(item)

    valid = unique_sec_entries(valid)

    if not valid:
        return None

    valid.sort(
        key=lambda item: (
            item["entry"].get("filed", ""),
            -item["entry"].get("_concept_priority", 999),
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
            -item["entry"].get("_concept_priority", 999),
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


def safe_multiple(numerator, denominator):
    if not isinstance(numerator, (int, float)):
        return None

    if not isinstance(denominator, (int, float)) or denominator == 0:
        return None

    return round(numerator / denominator, 2)


def safe_sum(*values):
    numeric = [
        value
        for value in values
        if isinstance(value, (int, float))
    ]

    if not numeric:
        return None

    return sum(numeric)


def sec_fact_value(fact):
    if not isinstance(fact, dict):
        return None

    value = fact.get("value")

    if isinstance(value, (int, float)):
        return value

    return None


def sec_fact_age_days(fact, reference_end):
    if not fact or not reference_end:
        return None

    fact_end = parse_sec_date(fact.get("end"))
    reference = parse_sec_date(reference_end)

    if not fact_end or not reference:
        return None

    return (reference - fact_end).days


def discard_stale_sec_fact(
    fact,
    reference_end,
    max_age_days=550,
):
    age = sec_fact_age_days(fact, reference_end)

    if age is None:
        return fact

    if age > max_age_days:
        return None

    return fact


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

    operating_cash_flow = select_sec_latest_duration(
        company_facts,
        SEC_CASHFLOW_METRICS["operating_cash_flow"],
        "interim",
    )

    cash_flow_and_allocation = {
        "operating_cash_flow": operating_cash_flow,
        "capital_expenditure": select_sec_matching_duration(
            company_facts,
            SEC_CASHFLOW_METRICS["capital_expenditure"],
            operating_cash_flow,
        ),
        "revenue": select_sec_matching_duration(
            company_facts,
            SEC_DURATION_METRICS["revenue"],
            operating_cash_flow,
        ),
        "net_income": select_sec_matching_duration(
            company_facts,
            SEC_DURATION_METRICS["net_income"],
            operating_cash_flow,
        ),
        "share_repurchases": select_sec_matching_duration(
            company_facts,
            SEC_CASHFLOW_METRICS["share_repurchases"],
            operating_cash_flow,
        ),
        "dividends_paid": select_sec_matching_duration(
            company_facts,
            SEC_CASHFLOW_METRICS["dividends_paid"],
            operating_cash_flow,
        ),
        "stock_based_compensation": select_sec_matching_duration(
            company_facts,
            SEC_CASHFLOW_METRICS["stock_based_compensation"],
            operating_cash_flow,
        ),
    }

    cash_flow_reference_end = (
        operating_cash_flow.get("end")
        if operating_cash_flow
        else latest_period
    )

    annual_cash_flow = {
        metric_name: discard_stale_sec_fact(
            select_sec_latest_duration(
                company_facts,
                concepts,
                "annual",
            ),
            cash_flow_reference_end,
        )
        for metric_name, concepts in SEC_CASHFLOW_METRICS.items()
        if metric_name in {
            "operating_cash_flow",
            "capital_expenditure",
            "share_repurchases",
            "dividends_paid",
        }
    }

    debt_components = {
        metric_name: select_sec_latest_instant(
            company_facts,
            concepts,
        )
        for metric_name, concepts in SEC_DEBT_METRICS.items()
    }

    ocf_value = sec_fact_value(
        cash_flow_and_allocation["operating_cash_flow"]
    )

    capex_raw = sec_fact_value(
        cash_flow_and_allocation["capital_expenditure"]
    )

    capex_value = (
        abs(capex_raw)
        if capex_raw is not None
        else None
    )

    cash_flow_revenue = sec_fact_value(
        cash_flow_and_allocation["revenue"]
    )

    cash_flow_net_income = sec_fact_value(
        cash_flow_and_allocation["net_income"]
    )

    buybacks_value = sec_fact_value(
        cash_flow_and_allocation["share_repurchases"]
    )

    dividends_value = sec_fact_value(
        cash_flow_and_allocation["dividends_paid"]
    )

    sbc_value = sec_fact_value(
        cash_flow_and_allocation["stock_based_compensation"]
    )

    free_cash_flow = (
        ocf_value - capex_value
        if ocf_value is not None and capex_value is not None
        else None
    )

    short_term_debt = sec_fact_value(
        debt_components["short_term_borrowings"]
    )

    current_long_term_debt = sec_fact_value(
        debt_components["current_long_term_debt"]
    )

    noncurrent_long_term_debt = sec_fact_value(
        debt_components["noncurrent_long_term_debt"]
    )

    total_reported_debt = safe_sum(
        short_term_debt,
        current_long_term_debt,
        noncurrent_long_term_debt,
    )

    current_cash = sec_fact_value(instant_metrics.get("cash"))

    net_debt = (
        total_reported_debt - current_cash
        if (
            total_reported_debt is not None
            and current_cash is not None
        )
        else None
    )

    capital_returns = safe_sum(
        buybacks_value,
        dividends_value,
    )

    capital_returns_complete = (
        buybacks_value is not None
        and dividends_value is not None
    )

    annual_ocf = sec_fact_value(
        annual_cash_flow.get("operating_cash_flow")
    )

    annual_capex_raw = sec_fact_value(
        annual_cash_flow.get("capital_expenditure")
    )

    annual_capex = (
        abs(annual_capex_raw)
        if annual_capex_raw is not None
        else None
    )

    annual_free_cash_flow = (
        annual_ocf - annual_capex
        if annual_ocf is not None and annual_capex is not None
        else None
    )

    cash_flow_derived = {
        "free_cash_flow": free_cash_flow,
        "free_cash_flow_margin_percentage": safe_ratio(
            free_cash_flow,
            cash_flow_revenue,
        ),
        "operating_cash_flow_to_net_income_percentage": safe_ratio(
            ocf_value,
            cash_flow_net_income,
        ),
        "capital_expenditure_to_operating_cash_flow_percentage": (
            safe_ratio(capex_value, ocf_value)
        ),
        "stock_based_compensation_to_revenue_percentage": safe_ratio(
            sbc_value,
            cash_flow_revenue,
        ),
        "total_reported_debt": total_reported_debt,
        "net_debt": net_debt,
        "annual_free_cash_flow": annual_free_cash_flow,
        "net_debt_to_annual_free_cash_flow": safe_multiple(
            net_debt,
            annual_free_cash_flow,
        ),
        "capital_returns": capital_returns,
        "capital_returns_components_complete": (
            capital_returns_complete
        ),
        "capital_returns_to_free_cash_flow_percentage": safe_ratio(
            capital_returns,
            free_cash_flow,
        ),
    }

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
            **cash_flow_derived,
        },
        "cash_flow_and_allocation": cash_flow_and_allocation,
        "annual_cash_flow_reference": annual_cash_flow,
        "debt_components": debt_components,
        "source": "SEC EDGAR Company Facts",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


SEC_EVIDENCE_FORMS = {
    "10-K": {
        "category": "annual_results",
        "label": "Relatório anual",
        "materiality": "critical",
        "score": 100,
    },
    "20-F": {
        "category": "annual_results",
        "label": "Relatório anual internacional",
        "materiality": "critical",
        "score": 100,
    },
    "40-F": {
        "category": "annual_results",
        "label": "Relatório anual canadiano",
        "materiality": "critical",
        "score": 100,
    },
    "10-Q": {
        "category": "quarterly_results",
        "label": "Relatório trimestral",
        "materiality": "critical",
        "score": 96,
    },
    "6-K": {
        "category": "material_update",
        "label": "Atualização material internacional",
        "materiality": "high",
        "score": 90,
    },
    "8-K": {
        "category": "material_update",
        "label": "Acontecimento material",
        "materiality": "high",
        "score": 90,
    },
    "DEF 14A": {
        "category": "governance",
        "label": "Proxy e governance",
        "materiality": "medium",
        "score": 70,
    },
    "4": {
        "category": "insider_activity",
        "label": "Transação de insider",
        "materiality": "medium",
        "score": 62,
    },
    "S-3": {
        "category": "capital_markets",
        "label": "Registo de emissão de valores mobiliários",
        "materiality": "high",
        "score": 82,
    },
    "424B": {
        "category": "capital_markets",
        "label": "Prospecto de oferta",
        "materiality": "high",
        "score": 84,
    },
}

NEWS_CATEGORY_RULES = [
    (
        "results_guidance",
        "Resultados e guidance",
        [
            "earnings", "results", "quarter", "revenue", "profit",
            "guidance", "forecast", "outlook", "eps", "margin",
        ],
        "high",
        82,
    ),
    (
        "mergers_acquisitions",
        "Aquisições e operações societárias",
        [
            "acquisition", "acquire", "merger", "takeover", "buyout",
            "strategic review", "sale of", "divest",
        ],
        "high",
        84,
    ),
    (
        "legal_regulatory",
        "Regulação, investigação ou litígio",
        [
            "lawsuit", "investigation", "regulator", "regulatory",
            "antitrust", "fine", "probe", "court", "settlement",
        ],
        "high",
        86,
    ),
    (
        "capital_allocation",
        "Capital, dívida e retorno ao acionista",
        [
            "offering", "share sale", "debt", "bond", "credit facility",
            "buyback", "repurchase", "dividend", "capital raise",
        ],
        "high",
        80,
    ),
    (
        "management",
        "Alteração de administração",
        [
            "ceo", "cfo", "chairman", "appoint", "resign", "departure",
            "management change", "chief executive", "chief financial",
        ],
        "medium",
        72,
    ),
    (
        "commercial",
        "Contratos, clientes e parcerias",
        [
            "contract", "order", "backlog", "customer", "partnership",
            "agreement", "award", "supplier",
        ],
        "medium",
        68,
    ),
    (
        "product_operations",
        "Produto e operações",
        [
            "launch", "product", "approval", "fda", "production",
            "capacity", "factory", "recall", "outage",
        ],
        "medium",
        65,
    ),
]

CAUTION_NEWS_KEYWORDS = {
    "miss", "cuts guidance", "lower guidance", "downgrade", "decline",
    "fall", "drops", "lawsuit", "investigation", "probe", "fine",
    "recall", "delay", "layoff", "bankruptcy", "default", "offering",
}

POSITIVE_NEWS_KEYWORDS = {
    "beats", "raises guidance", "record", "wins contract", "approval",
    "buyback", "dividend increase", "expands", "growth accelerates",
}


def fetch_sec_submissions(cik: str) -> dict:
    clean_cik = str(cik or "").zfill(10)

    if not re.fullmatch(r"\d{10}", clean_cik):
        raise ValueError("CIK inválido para submissões SEC.")

    cached = SEC_SUBMISSIONS_CACHE.get(clean_cik)

    if cached:
        age = time.time() - cached["created_at"]
        if age < SEC_SUBMISSIONS_CACHE_TTL_SECONDS:
            return cached["data"]

    user_agent = os.getenv("SEC_USER_AGENT")
    if not user_agent:
        raise RuntimeError("SEC_USER_AGENT não está configurado.")

    request = Request(
        SEC_SUBMISSIONS_URL.format(cik=clean_cik),
        headers={
            "User-Agent": user_agent,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
        },
    )

    with urlopen(request, timeout=25) as response:
        raw = decode_http_body(response)
        data = json.loads(raw.decode("utf-8"))

    SEC_SUBMISSIONS_CACHE[clean_cik] = {
        "created_at": time.time(),
        "data": data,
    }
    return data


def normalize_sec_evidence_form(form: str) -> str:
    clean = str(form or "").strip().upper()
    clean = clean.removesuffix("/A")

    if clean.startswith("424B"):
        return "424B"

    return clean


def sec_filing_document_url(
    cik: str,
    accession_number: str,
    primary_document: str,
) -> str | None:
    if not cik or not accession_number or not primary_document:
        return None

    clean_cik = str(int(str(cik)))
    clean_accession = str(accession_number).replace("-", "")
    return SEC_ARCHIVES_URL.format(
        cik=clean_cik,
        accession=clean_accession,
        primary_document=primary_document,
    )


def parse_recent_sec_evidence(
    submissions: dict,
    days: int = 365,
    limit: int = 14,
) -> list[dict]:
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form") or []
    filing_dates = recent.get("filingDate") or []
    report_dates = recent.get("reportDate") or []
    acceptance_dates = recent.get("acceptanceDateTime") or []
    accessions = recent.get("accessionNumber") or []
    primary_documents = recent.get("primaryDocument") or []
    descriptions = recent.get("primaryDocDescription") or []
    cik = submissions.get("cik")
    cutoff = date.today().toordinal() - days
    results = []

    for index, raw_form in enumerate(forms):
        form = normalize_sec_evidence_form(raw_form)
        metadata = SEC_EVIDENCE_FORMS.get(form)

        if not metadata:
            continue

        filing_date = filing_dates[index] if index < len(filing_dates) else None

        try:
            filing_day = date.fromisoformat(filing_date)
        except (TypeError, ValueError):
            continue

        if filing_day.toordinal() < cutoff:
            continue

        accession = accessions[index] if index < len(accessions) else None
        primary_document = (
            primary_documents[index]
            if index < len(primary_documents)
            else None
        )
        report_date = report_dates[index] if index < len(report_dates) else None
        accepted = (
            acceptance_dates[index]
            if index < len(acceptance_dates)
            else None
        )
        description = (
            descriptions[index]
            if index < len(descriptions)
            else None
        )
        label = metadata["label"]
        headline = f"{form} — {label}"

        if report_date:
            headline += f" ({report_date})"

        results.append(
            {
                "id": f"sec:{accession or form + ':' + filing_date}",
                "event_type": "official_filing",
                "source_kind": "official",
                "source": "SEC EDGAR",
                "source_confidence": "highest",
                "event_date": filing_date,
                "event_timestamp": accepted or f"{filing_date}T00:00:00Z",
                "form": form,
                "category": metadata["category"],
                "category_label": label,
                "materiality": metadata["materiality"],
                "materiality_score": metadata["score"],
                "direction": "neutral",
                "headline": headline,
                "summary": (
                    description
                    or (
                        f"Documento regulatório oficial apresentado à SEC"
                        + (f" para o período terminado em {report_date}." if report_date else ".")
                    )
                ),
                "url": sec_filing_document_url(
                    str(cik),
                    accession,
                    primary_document,
                ),
                "accession_number": accession,
                "report_date": report_date,
                "primary_document": primary_document,
                "why_it_matters": (
                    "Documento oficial com prioridade máxima na hierarquia "
                    "de evidência do ThesisOS."
                ),
            }
        )

        if len(results) >= limit:
            break

    return results


def classify_company_news(headline: str, summary: str) -> dict:
    text = f"{headline or ''} {summary or ''}".casefold()
    category = "general_news"
    label = "Notícia empresarial"
    materiality = "low"
    score = 45

    for (
        candidate_category,
        candidate_label,
        keywords,
        candidate_materiality,
        candidate_score,
    ) in NEWS_CATEGORY_RULES:
        if any(keyword in text for keyword in keywords):
            category = candidate_category
            label = candidate_label
            materiality = candidate_materiality
            score = candidate_score
            break

    if any(keyword in text for keyword in CAUTION_NEWS_KEYWORDS):
        direction = "caution"
    elif any(keyword in text for keyword in POSITIVE_NEWS_KEYWORDS):
        direction = "potential_positive"
    else:
        direction = "neutral"

    return {
        "category": category,
        "category_label": label,
        "materiality": materiality,
        "materiality_score": score,
        "direction": direction,
    }


def fetch_company_news_evidence(
    symbol: str,
    days: int = 90,
    limit: int = 20,
) -> list[dict]:
    clean_symbol = str(symbol or "").strip().upper()

    if not clean_symbol:
        return []

    end_date = date.today()
    start_date = date.fromordinal(end_date.toordinal() - days)
    data = fetch_finnhub(
        "company-news",
        {
            "symbol": clean_symbol,
            "from": start_date.isoformat(),
            "to": end_date.isoformat(),
        },
    )

    if not isinstance(data, list):
        raise RuntimeError("Resposta inesperada da Finnhub Company News.")

    seen = set()
    events = []

    for item in data:
        headline = clean_official_text(item.get("headline"))
        url = item.get("url")
        identifier = item.get("id") or url or headline

        if not headline or identifier in seen:
            continue

        seen.add(identifier)
        summary = clean_official_text(item.get("summary"))
        classification = classify_company_news(headline, summary)
        timestamp = item.get("datetime")

        try:
            event_datetime = datetime.fromtimestamp(
                int(timestamp),
                tz=timezone.utc,
            )
            event_timestamp = event_datetime.isoformat()
            event_date = event_datetime.date().isoformat()
        except (TypeError, ValueError, OSError):
            event_timestamp = None
            event_date = None

        events.append(
            {
                "id": f"news:{identifier}",
                "event_type": "company_news",
                "source_kind": "news",
                "source": item.get("source") or "Finnhub Company News",
                "source_confidence": "secondary",
                "event_date": event_date,
                "event_timestamp": event_timestamp,
                "form": None,
                **classification,
                "headline": headline,
                "summary": summary[:500] if summary else "",
                "url": url,
                "image": item.get("image"),
                "related": item.get("related"),
                "why_it_matters": (
                    "Notícia classificada por tema e materialidade. "
                    "Quando possível, deve ser confirmada no documento original."
                ),
            }
        )

        if len(events) >= limit:
            break

    return events


def evidence_review_implications(events: list[dict]) -> list[str]:
    implications = []
    recent_official = [
        event
        for event in events
        if event.get("source_kind") == "official"
        and event.get("materiality") in {"critical", "high"}
    ]
    caution_events = [
        event
        for event in events
        if event.get("direction") == "caution"
        and event.get("materiality") in {"critical", "high"}
    ]

    if any(
        event.get("category") in {"annual_results", "quarterly_results"}
        for event in recent_official
    ):
        implications.append(
            "Rever receitas, margens, cash flow, dívida e guidance face à tese anterior."
        )

    if any(
        event.get("category") == "material_update"
        for event in recent_official
    ):
        implications.append(
            "Ler o 8-K/6-K antes de alterar a decisão ou o tamanho da posição."
        )

    if any(
        event.get("category") == "capital_markets"
        for event in recent_official
    ):
        implications.append(
            "Validar potencial diluição, custo da dívida e impacto no valor por ação."
        )

    if caution_events:
        implications.append(
            "Existe notícia material com sinal de cautela; confirmar a origem oficial."
        )

    if not implications:
        implications.append(
            "Nenhum evento material recente identificado que, por si só, altere a tese."
        )

    return implications


def build_evidence_snapshot(
    asset: dict,
    fundamentals: dict | None,
) -> dict:
    symbol = str(asset.get("symbol") or "").strip().upper()
    asset_type = asset.get("asset_type")
    cik = (fundamentals or {}).get("cik")
    cache_key = f"{asset_type}:{symbol}:{cik or ''}"
    cached = EVIDENCE_CACHE.get(cache_key)

    if cached:
        age = time.time() - cached["created_at"]
        if age < EVIDENCE_CACHE_TTL_SECONDS:
            return cached["data"]

    filings = []
    news = []
    errors = []
    filings_status = "not_applicable"
    news_status = "not_applicable"

    if asset_type == "stock":
        if not cik and symbol:
            try:
                company = find_sec_company(symbol)
                cik = (company or {}).get("cik")
            except Exception as error:
                errors.append(f"SEC company lookup: {error}")

        if cik:
            try:
                submissions = fetch_sec_submissions(cik)
                filings = parse_recent_sec_evidence(submissions)
                filings_status = "ok" if filings else "empty"
            except Exception as error:
                filings_status = "unavailable"
                errors.append(f"SEC submissions: {error}")
        else:
            filings_status = "unavailable"
            errors.append("CIK indisponível para consultar submissões SEC.")

        if symbol:
            try:
                news = fetch_company_news_evidence(symbol)
                news_status = "ok" if news else "empty"
            except Exception as error:
                news_status = "unavailable"
                errors.append(f"Finnhub company news: {error}")

    events = filings + news
    events.sort(
        key=lambda item: (
            item.get("event_timestamp") or item.get("event_date") or "",
            item.get("materiality_score") or 0,
        ),
        reverse=True,
    )

    material_events = sorted(
        [
            event
            for event in events
            if event.get("materiality") in {"critical", "high"}
        ],
        key=lambda item: (
            item.get("materiality_score") or 0,
            item.get("event_timestamp") or item.get("event_date") or "",
        ),
        reverse=True,
    )

    official_available = filings_status in {"ok", "empty"}
    news_available = news_status in {"ok", "empty"}

    if official_available and news_available:
        status = "ok"
        coverage = 100
    elif official_available:
        status = "partial"
        coverage = 65
    elif news_available:
        status = "partial"
        coverage = 40
    else:
        status = "unavailable"
        coverage = 0

    review_required = False
    review_reason = None
    today = date.today()

    for event in material_events:
        try:
            event_day = date.fromisoformat(event.get("event_date"))
        except (TypeError, ValueError):
            continue

        age_days = (today - event_day).days
        if (
            event.get("source_kind") == "official"
            and age_days <= 30
        ) or (
            event.get("direction") == "caution"
            and age_days <= 14
        ):
            review_required = True
            review_reason = event.get("headline")
            break

    snapshot = {
        "status": status,
        "coverage_percentage": coverage,
        "symbol": symbol,
        "cik": cik,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "filings_status": filings_status,
        "news_status": news_status,
        "official_filings_count": len(filings),
        "news_count": len(news),
        "material_events_count": len(material_events),
        "review_required": review_required,
        "review_reason": review_reason,
        "latest_event": events[0] if events else None,
        "latest_official_filing": filings[0] if filings else None,
        "events": events[:30],
        "material_events": material_events[:12],
        "thesis_implications": evidence_review_implications(events),
        "errors": errors,
        "source_hierarchy": [
            "SEC EDGAR official filings",
            "Company or regulatory source linked by the event",
            "Finnhub company news aggregation",
        ],
        "methodology_note": (
            "O motor classifica documentos e notícias por fonte, tema, "
            "recência e materialidade. Não inventa impacto financeiro nem "
            "substitui a leitura do documento original."
        ),
    }

    EVIDENCE_CACHE[cache_key] = {
        "created_at": time.time(),
        "data": snapshot,
    }
    return snapshot


def enrich_framework_engine_with_evidence(
    engine: dict,
    evidence: dict | None,
) -> dict:
    if not isinstance(engine, dict) or not evidence:
        return engine

    checklist = engine.get("framework_checklist", {}).get("items", [])
    documentation_item = next(
        (
            item
            for item in checklist
            if item.get("id") == "official_documentation"
        ),
        None,
    )

    official_count = evidence.get("official_filings_count", 0)
    news_count = evidence.get("news_count", 0)
    available = []

    if official_count:
        available.append("submissões SEC recentes")
    if any(
        event.get("form") in {"10-K", "10-Q", "20-F", "40-F"}
        for event in evidence.get("events", [])
    ):
        available.append("10-K/10-Q/20-F recentes")
    if any(
        event.get("form") in {"8-K", "6-K"}
        for event in evidence.get("events", [])
    ):
        available.append("8-K/6-K recentes")
    if news_count:
        available.append("notícias empresariais recentes")
    if evidence.get("material_events_count"):
        available.append("classificação de materialidade")

    if documentation_item and available:
        documentation_item["status"] = "partial"
        documentation_item["available_data"] = list(dict.fromkeys(
            documentation_item.get("available_data", []) + available
        ))
        documentation_item["missing_data"] = [
            value
            for value in documentation_item.get("missing_data", [])
            if not any(
                token in str(value).casefold()
                for token in ("8-k", "notícias materiais")
            )
        ]

    engine["evidence_snapshot"] = evidence
    engine["next_required_data"] = [
        item
        for item in engine.get("next_required_data", [])
        if not any(
            token in str(item).casefold()
            for token in ("8-k", "notícias materiais")
        )
    ]

    decision = engine.get("decision", {})
    decision["evidence_context"] = {
        "status": evidence.get("status"),
        "material_events_count": evidence.get("material_events_count"),
        "review_required": evidence.get("review_required"),
        "review_reason": evidence.get("review_reason"),
    }

    if evidence.get("review_required"):
        existing_reason = decision.get("reason") or ""
        event_reason = (
            " Existe um evento material recente que deve ser lido antes "
            "de alterar a decisão ou o tamanho da posição."
        )
        if event_reason.strip() not in existing_reason:
            decision["reason"] = (existing_reason + event_reason).strip()
        decision["event_gate"] = "review_required"

    framework = engine.get("framework_checklist", {})
    if checklist:
        available_count = sum(
            1
            for item in checklist
            if item.get("status") in {"available", "partial"}
        )
        framework["coverage_percentage"] = round(
            available_count / len(checklist) * 100
        )

    return engine

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


class OfficialEtfPageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tokens = []
        self.links = []
        self._skip_depth = 0
        self._current_link = None
        self._current_link_text = []

    def handle_starttag(self, tag, attrs):
        clean_tag = tag.lower()

        if clean_tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
            return

        if self._skip_depth:
            return

        if clean_tag == "a":
            attributes = dict(attrs)
            self._current_link = attributes.get("href")
            self._current_link_text = []

    def handle_endtag(self, tag):
        clean_tag = tag.lower()

        if clean_tag in {"script", "style", "noscript", "svg"}:
            if self._skip_depth:
                self._skip_depth -= 1
            return

        if self._skip_depth:
            return

        if clean_tag == "a" and self._current_link:
            text = clean_official_text(
                " ".join(self._current_link_text)
            )
            self.links.append(
                {
                    "text": text,
                    "href": self._current_link,
                }
            )
            self._current_link = None
            self._current_link_text = []

    def handle_data(self, data):
        if self._skip_depth:
            return

        value = clean_official_text(data)

        if not value:
            return

        self.tokens.append(value)

        if self._current_link is not None:
            self._current_link_text.append(value)


def clean_official_text(value):
    value = str(value or "").replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def normalize_official_text(value):
    value = clean_official_text(value).casefold()
    value = value.replace("’", "'")
    return value.rstrip(":'")


def fetch_official_html(url: str) -> str:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0 Safari/537.36 ThesisOS/0.3"
            ),
            "Accept": (
                "text/html,application/xhtml+xml,"
                "application/xml;q=0.9,*/*;q=0.8"
            ),
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )

    with urlopen(request, timeout=45) as response:
        raw = response.read()
        charset = (
            response.headers.get_content_charset()
            or "utf-8"
        )
        return raw.decode(charset, errors="replace")


def find_official_value(tokens, labels, max_distance=5):
    normalized_tokens = [
        normalize_official_text(token)
        for token in tokens
    ]
    normalized_labels = [
        normalize_official_text(label)
        for label in labels
    ]

    for index, token in enumerate(normalized_tokens):
        if token not in normalized_labels:
            continue

        for candidate_index in range(
            index + 1,
            min(index + 1 + max_distance, len(tokens)),
        ):
            candidate = clean_official_text(
                tokens[candidate_index]
            )
            normalized_candidate = normalize_official_text(
                candidate
            )

            if not candidate:
                continue

            if normalized_candidate in normalized_labels:
                continue

            if candidate in {"—", "-", "–"}:
                return None

            return candidate

    return None


def find_official_document_link(
    links,
    page_url,
    wanted_text,
):
    wanted = normalize_official_text(wanted_text)

    for link in links:
        if wanted in normalize_official_text(
            link.get("text")
        ):
            href = link.get("href")

            if href:
                return urljoin(page_url, href)

    return None


VANGUARD_DATE_PATTERN = r"\d{1,2}\s+[A-Za-z]{3}\s+\d{4}"
VANGUARD_PERCENT_PATTERN = r"[-+]?\d+(?:\.\d+)?%"
VANGUARD_NUMBER_PATTERN = r"\d[\d,]*(?:\.\d+)?"

VANGUARD_REGIONS = [
    "North America",
    "Emerging Markets",
    "Pacific",
    "Europe",
]

VANGUARD_SECTORS = [
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Financials",
    "Health Care",
    "Industrials",
    "Information Technology",
    "Technology",
    "Materials",
    "Real Estate",
    "Utilities",
    "Energy",
]


def first_vanguard_match(
    text,
    pattern,
    flags=re.IGNORECASE,
):
    match = re.search(pattern, text, flags)

    if not match:
        return None

    if match.lastindex == 1:
        return clean_official_text(match.group(1))

    return [
        clean_official_text(group)
        for group in match.groups()
    ]


def vanguard_percent_to_number(value):
    if value is None:
        return None

    try:
        return float(
            value.replace("%", "").replace(",", "")
        )
    except ValueError:
        return None


def vanguard_number_to_float(value):
    if value is None:
        return None

    try:
        return float(value.replace(",", ""))
    except ValueError:
        return None


def parse_vanguard_tracking_error(text):
    values = first_vanguard_match(
        text,
        (
            r"Annualized Tracking Error\s+"
            r"1 year\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+3 years\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+5 years\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")"
        ),
    )

    if not values:
        return {
            "one_year_percentage": None,
            "three_year_percentage": None,
            "five_year_percentage": None,
        }

    return {
        "one_year_percentage": (
            vanguard_percent_to_number(values[0])
        ),
        "three_year_percentage": (
            vanguard_percent_to_number(values[1])
        ),
        "five_year_percentage": (
            vanguard_percent_to_number(values[2])
        ),
    }


def parse_vanguard_characteristics(text):
    number_of_stocks = first_vanguard_match(
        text,
        (
            r"Number of stocks\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    price_to_earnings = first_vanguard_match(
        text,
        (
            r"Price earning\s*/\s*ratio\s*\(P/E\)\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s*x\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s*x\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    price_to_book = first_vanguard_match(
        text,
        (
            r"Price\s*/\s*accounting ratio\s*\(P/B\)\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s*x\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s*x\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    return_on_equity = first_vanguard_match(
        text,
        (
            r"Return on equity\s*\(ROE\)\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    earnings_growth = first_vanguard_match(
        text,
        (
            r"Earnings growth rate\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+("
            + VANGUARD_PERCENT_PATTERN
            + r")\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    return {
        "number_of_stocks": {
            "fund": (
                vanguard_number_to_float(
                    number_of_stocks[0]
                )
                if number_of_stocks
                else None
            ),
            "benchmark": (
                vanguard_number_to_float(
                    number_of_stocks[1]
                )
                if number_of_stocks
                else None
            ),
            "as_of": (
                number_of_stocks[2]
                if number_of_stocks
                else None
            ),
        },
        "price_to_earnings": {
            "fund": (
                vanguard_number_to_float(
                    price_to_earnings[0]
                )
                if price_to_earnings
                else None
            ),
            "benchmark": (
                vanguard_number_to_float(
                    price_to_earnings[1]
                )
                if price_to_earnings
                else None
            ),
            "as_of": (
                price_to_earnings[2]
                if price_to_earnings
                else None
            ),
        },
        "price_to_book": {
            "fund": (
                vanguard_number_to_float(
                    price_to_book[0]
                )
                if price_to_book
                else None
            ),
            "benchmark": (
                vanguard_number_to_float(
                    price_to_book[1]
                )
                if price_to_book
                else None
            ),
            "as_of": (
                price_to_book[2]
                if price_to_book
                else None
            ),
        },
        "return_on_equity_percentage": {
            "fund": (
                vanguard_percent_to_number(
                    return_on_equity[0]
                )
                if return_on_equity
                else None
            ),
            "benchmark": (
                vanguard_percent_to_number(
                    return_on_equity[1]
                )
                if return_on_equity
                else None
            ),
            "as_of": (
                return_on_equity[2]
                if return_on_equity
                else None
            ),
        },
        "earnings_growth_percentage": {
            "fund": (
                vanguard_percent_to_number(
                    earnings_growth[0]
                )
                if earnings_growth
                else None
            ),
            "benchmark": (
                vanguard_percent_to_number(
                    earnings_growth[1]
                )
                if earnings_growth
                else None
            ),
            "as_of": (
                earnings_growth[2]
                if earnings_growth
                else None
            ),
        },
    }


def parse_vanguard_market_allocation(text):
    start = text.find("Market allocation")

    if start < 0:
        return []

    end = text.find("Holdings details", start)
    section = text[start:end if end > start else None]

    region_pattern = "|".join(
        re.escape(region)
        for region in sorted(
            VANGUARD_REGIONS,
            key=len,
            reverse=True,
        )
    )

    row_pattern = re.compile(
        (
            r"(?P<country>[A-Za-z][A-Za-z .'-]*?)\s+"
            r"(?P<region>"
            + region_pattern
            + r")\s+"
            r"(?P<fund>"
            + VANGUARD_PERCENT_PATTERN
            + r")\s+"
            r"(?P<benchmark>"
            + VANGUARD_PERCENT_PATTERN
            + r")\s+"
            r"(?P<variance>"
            + VANGUARD_PERCENT_PATTERN
            + r")"
        )
    )

    rows = []

    for match in row_pattern.finditer(section):
        rows.append(
            {
                "country": clean_official_text(
                    match.group("country")
                ),
                "region": match.group("region"),
                "fund_percentage": (
                    vanguard_percent_to_number(
                        match.group("fund")
                    )
                ),
                "benchmark_percentage": (
                    vanguard_percent_to_number(
                        match.group("benchmark")
                    )
                ),
                "variance_percentage_points": (
                    vanguard_percent_to_number(
                        match.group("variance")
                    )
                ),
            }
        )

    return rows


def clean_vanguard_holding_name(value):
    name = clean_official_text(value)

    header_prefixes = [
        (
            "of market value Sector Region "
            "Market value Shares "
        ),
        (
            "Percentage of market value Sector Region "
            "Market value Shares "
        ),
        (
            "% of market value Sector Region "
            "Market value Shares "
        ),
    ]

    for prefix in header_prefixes:
        if name.startswith(prefix):
            name = name[len(prefix):].strip()
            break

    name = re.sub(
        (
            r"^(?:of market value|percentage of market value)\s+"
            r"sector\s+region\s+market value\s+shares\s+"
        ),
        "",
        name,
        flags=re.IGNORECASE,
    )

    return clean_official_text(name)


def parse_vanguard_holdings(text):
    start = text.find("Holdings details")

    if start < 0:
        return []

    end = text.find("Total allocation percentages", start)
    section = text[start:end if end > start else None]

    sector_pattern = "|".join(
        re.escape(sector)
        for sector in sorted(
            VANGUARD_SECTORS,
            key=len,
            reverse=True,
        )
    )

    row_pattern = re.compile(
        (
            r"(?P<name>[A-Za-z0-9]"
            r"[A-Za-z0-9&.,()' -]*?)\s+"
            r"(?P<weight>\d+(?:\.\d+)?%)\s+"
            r"(?P<sector>"
            + sector_pattern
            + r")\s+"
            r"(?P<region>[A-Z]{2})\s+"
            r"(?P<market_value>(?:US\$|\$|£|€)"
            r"[\d,]+(?:\.\d+)?)\s+"
            r"(?P<shares>[\d,]+)"
        )
    )

    rows = []

    for match in row_pattern.finditer(section):
        rows.append(
            {
                "name": clean_vanguard_holding_name(
                    match.group("name")
                ),
                "weight_percentage": (
                    vanguard_percent_to_number(
                        match.group("weight")
                    )
                ),
                "sector": match.group("sector"),
                "region_code": match.group("region"),
                "market_value": match.group(
                    "market_value"
                ),
                "shares": vanguard_number_to_float(
                    match.group("shares")
                ),
            }
        )

    return rows


def parse_vanguard_prices_and_structure(text):
    outstanding = first_vanguard_match(
        text,
        (
            r"Outstanding shares\s+("
            + VANGUARD_NUMBER_PATTERN
            + r")\s+At closure\s+("
            + VANGUARD_DATE_PATTERN
            + r")"
        ),
    )

    nav_high = first_vanguard_match(
        text,
        (
            r"NAV 52-week high\s+"
            r"((?:US\$|\$|£|€)"
            + VANGUARD_NUMBER_PATTERN
            + r")"
        ),
    )

    nav_low = first_vanguard_match(
        text,
        (
            r"NAV 52-week low\s+"
            r"((?:US\$|\$|£|€)"
            + VANGUARD_NUMBER_PATTERN
            + r")"
        ),
    )

    listed_currencies = first_vanguard_match(
        text,
        r"Listed currencies:\s*([A-Z, ]+?)\s+Base currency:",
    )

    base_currency = first_vanguard_match(
        text,
        r"Base currency:\s*([A-Z]{3})",
    )

    return {
        "outstanding_shares": {
            "value": (
                vanguard_number_to_float(
                    outstanding[0]
                )
                if outstanding
                else None
            ),
            "as_of": (
                outstanding[1]
                if outstanding
                else None
            ),
        },
        "nav_52_week_high": nav_high,
        "nav_52_week_low": nav_low,
        "listed_currencies": (
            [
                item.strip()
                for item in listed_currencies.split(",")
            ]
            if listed_currencies
            else []
        ),
        "base_currency": base_currency,
    }


VANGUARD_FACTSHEET_SECTORS = [
    "Consumer Discretionary",
    "Consumer Staples",
    "Telecommunications",
    "Basic Materials",
    "Technology",
    "Financials",
    "Industrials",
    "Health Care",
    "Real Estate",
    "Utilities",
    "Energy",
]


def fetch_official_bytes(url: str) -> bytes:
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/149.0 Safari/537.36 ThesisOS/0.5"
            ),
            "Accept": "*/*",
            "Accept-Language": "en-GB,en;q=0.9",
            "Cache-Control": "no-cache",
        },
    )

    with urlopen(request, timeout=60) as response:
        return response.read()


def extract_official_pdf_text(
    pdf_bytes: bytes,
    mode: str = "plain",
) -> str:
    try:
        import logging
        from pypdf import PdfReader
    except ImportError:
        try:
            import logging
            from PyPDF2 import PdfReader
        except ImportError as error:
            raise RuntimeError(
                "A biblioteca pypdf não está instalada."
            ) from error

    logging.getLogger("pypdf").setLevel(logging.ERROR)
    logging.getLogger("pypdf._page").setLevel(logging.ERROR)

    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = []

    for page in reader.pages:
        if mode == "layout":
            try:
                page_text = page.extract_text(
                    extraction_mode="layout"
                )
            except TypeError:
                page_text = page.extract_text()
        else:
            page_text = page.extract_text()

        pages.append(page_text or "")

    return "\n".join(pages)


def first_vanguard_document_number(text, patterns):
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return float(match.group(1))

    return None


def first_vanguard_document_text(text, patterns):
    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )

        if match:
            return clean_official_text(match.group(1))

    return None


def isolate_vanguard_sector_section(text):
    normalized = clean_official_text(text)
    start = normalized.casefold().find("weighted exposure")

    if start < 0:
        return ""

    section = normalized[start:]
    marker_positions = []

    for marker in (
        "Market allocation",
        "Source: Vanguard",
        "Glossary for ETF attributes",
    ):
        marker_index = section.casefold().find(
            marker.casefold()
        )

        if marker_index > 0:
            marker_positions.append(marker_index)

    if marker_positions:
        section = section[:min(marker_positions)]

    return section


def vanguard_sector_names_in_order(section):
    found = []

    for sector in VANGUARD_FACTSHEET_SECTORS:
        match = re.search(
            re.escape(sector),
            section,
            flags=re.IGNORECASE,
        )

        if match:
            found.append((match.start(), sector))

    found.sort(key=lambda item: item[0])
    return [sector for _, sector in found]


def vanguard_sector_numbers_in_order(section):
    values = []

    for match in re.finditer(
        r"(?<![\d.])(\d{1,2}(?:\.\d+)?)(?:\s*%)?(?![\d.])",
        section,
    ):
        value = float(match.group(1))

        if 0 <= value <= 100:
            values.append(value)

    return values


def parse_vanguard_sector_allocation(text):
    section = isolate_vanguard_sector_section(text)

    if not section:
        return []

    direct_rows = []

    for sector in sorted(
        VANGUARD_FACTSHEET_SECTORS,
        key=len,
        reverse=True,
    ):
        match = re.search(
            (
                re.escape(sector)
                + r"[\s:–—-]{1,40}"
                + r"(\d{1,2}(?:\.\d+)?)\s*%?"
            ),
            section,
            flags=re.IGNORECASE,
        )

        if match:
            direct_rows.append(
                {
                    "sector": sector,
                    "weight_percentage": float(match.group(1)),
                }
            )

    direct_total = sum(
        item["weight_percentage"]
        for item in direct_rows
    )

    if len(direct_rows) >= 8 and 95 <= direct_total <= 105:
        direct_rows.sort(
            key=lambda item: item["weight_percentage"],
            reverse=True,
        )
        return direct_rows

    ordered_sectors = vanguard_sector_names_in_order(section)
    ordered_values = vanguard_sector_numbers_in_order(section)

    if (
        len(ordered_sectors) >= 8
        and len(ordered_values) >= len(ordered_sectors)
    ):
        candidate_values = ordered_values[:len(ordered_sectors)]
        candidate_total = sum(candidate_values)

        if 95 <= candidate_total <= 105:
            rows = [
                {
                    "sector": sector,
                    "weight_percentage": value,
                }
                for sector, value in zip(
                    ordered_sectors,
                    candidate_values,
                )
            ]
            rows.sort(
                key=lambda item: item["weight_percentage"],
                reverse=True,
            )
            return rows

    return direct_rows


def parse_vanguard_official_documents(
    factsheet_plain_text: str,
    factsheet_layout_text: str,
    kiid_text: str,
) -> dict:
    combined_text = (
        clean_official_text(factsheet_plain_text)
        + " "
        + clean_official_text(factsheet_layout_text)
        + " "
        + clean_official_text(kiid_text)
    )

    ongoing_charges = first_vanguard_document_number(
        combined_text,
        [
            (
                r"Ongoing\s+Charges\s+Figure"
                r"[^\d]{0,40}(\d+(?:\.\d+)?)\s*%"
            ),
            (
                r"Ongoing\s+charges"
                r"[^\d]{0,40}(\d+(?:\.\d+)?)\s*%"
            ),
        ],
    )

    factsheet_date = first_vanguard_document_text(
        factsheet_plain_text + "\n" + factsheet_layout_text,
        [
            (
                r"Factsheet\s*\|\s*"
                r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})"
            ),
            (
                r"Data\s+as\s+at\s+"
                r"(\d{1,2}\s+[A-Za-z]+\s+\d{4})"
            ),
        ],
    )

    kiid_date = first_vanguard_document_text(
        kiid_text,
        [
            (
                r"accurate\s+as\s+at\s+"
                r"(\d{1,2}/\d{1,2}/\d{4})"
            ),
        ],
    )

    equity_yield = first_vanguard_document_number(
        factsheet_plain_text,
        [
            (
                r"Equity\s+yield\s*\(dividend\)"
                r"[^\d]{0,30}(\d+(?:\.\d+)?)\s*%"
            ),
        ],
    )

    if equity_yield is None:
        equity_yield = first_vanguard_document_number(
            factsheet_layout_text,
            [
                (
                    r"Equity\s+yield\s*\(dividend\)"
                    r"[^\d]{0,60}(\d+(?:\.\d+)?)\s*%"
                ),
            ],
        )

    portfolio_turnover = first_vanguard_document_number(
        factsheet_plain_text,
        [
            (
                r"(?:Portfolio\s+)?Turnover\s+rate"
                r"[^\d+-]{0,30}([-+]?\d+(?:\.\d+)?)\s*%"
            ),
        ],
    )

    if portfolio_turnover is None:
        portfolio_turnover = first_vanguard_document_number(
            factsheet_layout_text,
            [
                (
                    r"(?:Portfolio\s+)?Turnover\s+rate"
                    r"[^\d+-]{0,60}([-+]?\d+(?:\.\d+)?)\s*%"
                ),
            ],
        )

    sector_allocation = parse_vanguard_sector_allocation(
        factsheet_layout_text
    )

    if len(sector_allocation) < 8:
        sector_allocation = parse_vanguard_sector_allocation(
            factsheet_plain_text
        )

    sector_total = (
        round(
            sum(
                item["weight_percentage"]
                for item in sector_allocation
            ),
            4,
        )
        if sector_allocation
        else None
    )

    return {
        "status": "ok",
        "ongoing_charges_percentage": ongoing_charges,
        "factsheet_date": factsheet_date,
        "kiid_date": kiid_date,
        "equity_yield_percentage": equity_yield,
        "portfolio_turnover_percentage": portfolio_turnover,
        "sector_allocation": sector_allocation,
        "sector_total_percentage": sector_total,
        "largest_sector": (
            sector_allocation[0]
            if sector_allocation
            else None
        ),
        "coverage": {
            "ocf_available": ongoing_charges is not None,
            "sector_count": len(sector_allocation),
            "sector_total_close_to_100": (
                sector_total is not None
                and 98 <= sector_total <= 102
            ),
            "equity_yield_available": equity_yield is not None,
            "turnover_available": portfolio_turnover is not None,
        },
    }


def get_vanguard_official_document_data(
    factsheet_url: str | None,
    kiid_url: str | None,
) -> dict:
    if not factsheet_url:
        return {
            "status": "unavailable",
            "reason": "Factsheet oficial não encontrado.",
        }

    factsheet_bytes = fetch_official_bytes(factsheet_url)
    factsheet_plain_text = extract_official_pdf_text(
        factsheet_bytes,
        mode="plain",
    )
    factsheet_layout_text = extract_official_pdf_text(
        factsheet_bytes,
        mode="layout",
    )

    kiid_text = ""

    if kiid_url:
        kiid_text = extract_official_pdf_text(
            fetch_official_bytes(kiid_url),
            mode="plain",
        )

    return parse_vanguard_official_documents(
        factsheet_plain_text,
        factsheet_layout_text,
        kiid_text,
    )


def build_vanguard_etf_profile(
    asset: dict,
    source_url: str,
) -> dict:
    page_html = fetch_official_html(source_url)
    parser = OfficialEtfPageParser()
    parser.feed(page_html)
    tokens = parser.tokens
    page_text = clean_official_text(" ".join(tokens))

    tracking_error = parse_vanguard_tracking_error(
        page_text
    )
    characteristics = parse_vanguard_characteristics(
        page_text
    )
    market_allocation = parse_vanguard_market_allocation(
        page_text
    )
    holdings = parse_vanguard_holdings(page_text)
    prices_and_structure = (
        parse_vanguard_prices_and_structure(page_text)
    )

    factsheet_url = find_official_document_link(
        parser.links,
        source_url,
        "Factsheet",
    )
    kiid_url = find_official_document_link(
        parser.links,
        source_url,
        "KIID",
    )

    official_document_data = {
        "status": "unavailable",
        "reason": "Documentos ainda não processados.",
    }

    try:
        official_document_data = (
            get_vanguard_official_document_data(
                factsheet_url,
                kiid_url,
            )
        )
    except Exception as error:
        official_document_data = {
            "status": "unavailable",
            "reason": str(error),
        }

    top_10_holdings = holdings[:10]

    top_10_weight = (
        round(
            sum(
                item["weight_percentage"]
                for item in top_10_holdings
                if item.get("weight_percentage") is not None
            ),
            4,
        )
        if top_10_holdings
        else None
    )

    profile = {
        "issuer": "Vanguard",
        "name": asset.get("name"),
        "symbol": asset.get("symbol"),
        "isin": asset.get("isin"),
        "share_class_inception": find_official_value(
            tokens,
            ["Share class inception"],
        ),
        "listing_date": find_official_value(
            tokens,
            ["Listing date"],
        ),
        "investment_structure": find_official_value(
            tokens,
            ["Investment structure"],
        ),
        "share_class_assets": find_official_value(
            tokens,
            ["Share Class Assets", "Share Class Assets'"],
        ),
        "total_assets": find_official_value(
            tokens,
            ["Total Assets"],
        ),
        "risk_indicator": find_official_value(
            tokens,
            ["Risk indicator"],
        ),
        "strategy": find_official_value(
            tokens,
            ["Strategy"],
        ),
        "asset_class": find_official_value(
            tokens,
            ["Asset Class"],
        ),
        "investment_method": find_official_value(
            tokens,
            ["Investment method"],
        ),
        "index_ticker": find_official_value(
            tokens,
            ["Index ticker"],
        ),
        "benchmark": find_official_value(
            tokens,
            ["Benchmark"],
        ),
        "dividend_schedule": find_official_value(
            tokens,
            ["Dividend schedule"],
        ),
        "tax_status": find_official_value(
            tokens,
            ["Tax status"],
        ),
        "domicile": find_official_value(
            tokens,
            ["Domicile"],
        ),
        "legal_entity": find_official_value(
            tokens,
            ["Legal entity"],
        ),
        "investment_manager": find_official_value(
            tokens,
            ["Investment manager"],
        ),
        "ocf_ter": (
            (
                f"{official_document_data.get('ongoing_charges_percentage'):.2f}%"
                if official_document_data.get(
                    "ongoing_charges_percentage"
                ) is not None
                else None
            )
            or find_official_value(
                tokens,
                ["OCF/TER", "OCF", "TER"],
            )
        ),
        "ongoing_charges_percentage": (
            official_document_data.get(
                "ongoing_charges_percentage"
            )
        ),
        "factsheet_date": official_document_data.get(
            "factsheet_date"
        ),
        "kiid_date": official_document_data.get("kiid_date"),
        "equity_yield_percentage": (
            official_document_data.get(
                "equity_yield_percentage"
            )
        ),
        "portfolio_turnover_percentage": (
            official_document_data.get(
                "portfolio_turnover_percentage"
            )
        ),
        "sector_allocation": official_document_data.get(
            "sector_allocation",
            [],
        ),
        "sector_total_percentage": (
            official_document_data.get(
                "sector_total_percentage"
            )
        ),
        "largest_sector": official_document_data.get(
            "largest_sector"
        ),
        "official_document_data": official_document_data,
        "number_of_stocks": (
            characteristics
            .get("number_of_stocks", {})
            .get("fund")
            or find_official_value(
                tokens,
                ["Number of stocks"],
            )
        ),
        "tracking_error": tracking_error,
        "characteristics": characteristics,
        "market_allocation": market_allocation,
        "top_holdings": top_10_holdings,
        "concentration": {
            "top_10_weight_percentage": top_10_weight,
            "largest_holding_percentage": (
                top_10_holdings[0].get(
                    "weight_percentage"
                )
                if top_10_holdings
                else None
            ),
            "largest_country_percentage": (
                market_allocation[0].get(
                    "fund_percentage"
                )
                if market_allocation
                else None
            ),
        },
        "prices_and_structure": prices_and_structure,
        "distribution_policy": (
            "Accumulating"
            if "ACCUMULATING" in str(
                asset.get("name") or ""
            ).upper()
            else None
        ),
        "factsheet_url": factsheet_url,
        "kiid_url": kiid_url,
        "source_url": source_url,
        "source": "Vanguard official product page",
    }

    core_checks = {
        "share_class_inception": bool(
            profile.get("share_class_inception")
        ),
        "listing_date": bool(profile.get("listing_date")),
        "investment_structure": bool(
            profile.get("investment_structure")
        ),
        "share_class_assets": bool(
            profile.get("share_class_assets")
        ),
        "total_assets": bool(profile.get("total_assets")),
        "investment_method": bool(
            profile.get("investment_method")
        ),
        "benchmark": bool(profile.get("benchmark")),
        "domicile": bool(profile.get("domicile")),
        "number_of_stocks": bool(
            profile.get("number_of_stocks")
        ),
        "tracking_error": any(
            value is not None
            for value in tracking_error.values()
        ),
        "top_holdings": bool(top_10_holdings),
        "market_allocation": bool(market_allocation),
        "aggregate_valuation": any(
            characteristics.get(key, {}).get("fund")
            is not None
            for key in [
                "price_to_earnings",
                "price_to_book",
            ]
        ),
        "outstanding_shares": (
            prices_and_structure
            .get("outstanding_shares", {})
            .get("value")
            is not None
        ),
        "base_currency": bool(
            prices_and_structure.get("base_currency")
        ),
        "ongoing_charges": (
            profile.get("ongoing_charges_percentage")
            is not None
        ),
        "sector_allocation": bool(
            profile.get("sector_allocation")
        ),
        "equity_yield": (
            profile.get("equity_yield_percentage")
            is not None
        ),
        "portfolio_turnover": (
            profile.get("portfolio_turnover_percentage")
            is not None
        ),
    }

    available = sum(core_checks.values())

    profile["coverage"] = {
        "available_core_fields": available,
        "required_core_fields": len(core_checks),
        "percentage": round(
            available / len(core_checks) * 100,
            2,
        ),
        "checks": core_checks,
    }

    return profile


def get_official_etf_profile(asset: dict) -> dict | None:
    isin = str(asset.get("isin") or "").upper()
    registry_item = ETF_OFFICIAL_PROFILE_REGISTRY.get(isin)

    if not registry_item:
        return None

    cached = ETF_PROFILE_CACHE.get(isin)

    if cached:
        age = time.time() - cached["created_at"]

        if age < ETF_PROFILE_CACHE_TTL_SECONDS:
            return cached["data"]

    issuer = registry_item.get("issuer")
    source_url = registry_item.get("source_url")

    if issuer == "Vanguard":
        profile = build_vanguard_etf_profile(
            asset,
            source_url,
        )
    else:
        return None

    ETF_PROFILE_CACHE[isin] = {
        "created_at": time.time(),
        "data": profile,
    }

    return profile


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

def search_eodhd_ticker_listings(query: str) -> dict:
    clean_query = query.strip().upper()
    eodhd_matches = fetch_eodhd_search(clean_query)

    exact_matches = [
        item
        for item in eodhd_matches
        if str(item.get("Code") or "").upper() == clean_query
    ]

    exact_matches.sort(
        key=rank_eodhd_match,
        reverse=True,
    )

    normalized = [
        normalize_eodhd_match(item)
        for item in exact_matches[:20]
    ]

    return {
        "query": clean_query,
        "query_type": "ticker",
        "total_results": len(exact_matches),
        "returned_results": len(normalized),
        "requires_selection": len(normalized) > 1,
        "results": normalized,
        "source": "EODHD",
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
    clean_identifier = identifier.strip().upper()
    is_isin = bool(
        re.fullmatch(
            r"[A-Z]{2}[A-Z0-9]{9}[0-9]",
            clean_identifier,
        )
    )

    search_result = (
        search_assets(clean_identifier)
        if is_isin
        else search_eodhd_ticker_listings(clean_identifier)
    )

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


def yahoo_symbol_for_asset(asset: dict) -> str:
    symbol = str(asset.get("symbol") or "").strip().upper()

    if not symbol:
        raise ValueError("O ativo não tem ticker para histórico técnico.")

    if "." in symbol:
        return symbol

    exchange = str(
        asset.get("exchange_code")
        or asset.get("exchange")
        or ""
    ).strip().upper()

    suffix = YAHOO_EXCHANGE_SUFFIX.get(exchange, "")
    return f"{symbol}{suffix}"


def finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def calculate_latest_rsi(closes: list[float], period: int = 14):
    if len(closes) <= period:
        return None

    deltas = [
        closes[index] - closes[index - 1]
        for index in range(1, len(closes))
    ]

    gains = [max(delta, 0.0) for delta in deltas]
    losses = [max(-delta, 0.0) for delta in deltas]

    average_gain = sum(gains[:period]) / period
    average_loss = sum(losses[:period]) / period

    for index in range(period, len(deltas)):
        average_gain = (
            (average_gain * (period - 1)) + gains[index]
        ) / period
        average_loss = (
            (average_loss * (period - 1)) + losses[index]
        ) / period

    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return round(100 - (100 / (1 + relative_strength)), 2)


def simple_moving_average(values: list[float], sessions: int):
    if len(values) < sessions:
        return None

    return round(sum(values[-sessions:]) / sessions, 4)


def period_return(values: list[float], sessions: int):
    if len(values) <= sessions or values[-sessions - 1] == 0:
        return None

    return round(
        ((values[-1] / values[-sessions - 1]) - 1) * 100,
        2,
    )


def annualized_volatility(values: list[float]):
    if len(values) < 22:
        return None

    returns = []

    for previous, current in zip(values, values[1:]):
        if previous <= 0 or current <= 0:
            continue
        returns.append(math.log(current / previous))

    if len(returns) < 20:
        return None

    mean = sum(returns) / len(returns)
    variance = sum(
        (value - mean) ** 2
        for value in returns
    ) / (len(returns) - 1)

    return round(math.sqrt(variance) * math.sqrt(252) * 100, 2)


def maximum_drawdown(values: list[float]):
    if not values:
        return None

    peak = values[0]
    worst = 0.0

    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak) - 1)

    return round(worst * 100, 2)


def technical_rule(
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
        "source": "Yahoo Finance via yfinance",
    }


def unavailable_technical_rule(
    rule_id: str,
    label: str,
    unit: str,
    max_points: int,
) -> dict:
    return technical_rule(
        rule_id,
        label,
        None,
        unit,
        0,
        max_points,
        "unavailable",
        "Histórico insuficiente para este indicador.",
    )


def classify_technical_score(score):
    if score is None:
        return {
            "code": "insufficient_data",
            "label": "Dados técnicos insuficientes",
        }

    if score >= 80:
        return {"code": "strong", "label": "Momento forte"}
    if score >= 65:
        return {"code": "positive", "label": "Tendência positiva"}
    if score >= 45:
        return {"code": "neutral", "label": "Momento neutro"}

    return {"code": "weak", "label": "Tendência fraca"}


def build_technical_rules(indicators: dict) -> list[dict]:
    rules = []
    close = indicators.get("last_close")
    sma50 = indicators.get("sma_50")
    sma200 = indicators.get("sma_200")
    rsi = indicators.get("rsi_14")
    return_6m = indicators.get("return_6m_percentage")
    return_1y = indicators.get("return_1y_percentage")
    pullback = indicators.get("pullback_from_52w_high_percentage")
    volatility = indicators.get("annualized_volatility_percentage")

    if close is None or sma200 is None:
        rules.append(unavailable_technical_rule(
            "price_vs_sma200", "Preço vs média de 200 sessões", "%", 20
        ))
    else:
        distance = round(((close / sma200) - 1) * 100, 2)
        if distance >= 5:
            points, status, text = 20, "strong", "Preço claramente acima da tendência de longo prazo."
        elif distance >= 0:
            points, status, text = 15, "positive", "Preço acima da média de 200 sessões."
        elif distance >= -10:
            points, status, text = 7, "watch", "Preço ligeiramente abaixo da tendência de longo prazo."
        else:
            points, status, text = 0, "warning", "Preço muito abaixo da média de 200 sessões."
        rules.append(technical_rule(
            "price_vs_sma200", "Preço vs média de 200 sessões", distance, "%",
            points, 20, status, text,
        ))

    if sma50 is None or sma200 is None:
        rules.append(unavailable_technical_rule(
            "sma50_vs_sma200", "Média 50 vs 200 sessões", "%", 15
        ))
    else:
        distance = round(((sma50 / sma200) - 1) * 100, 2)
        if distance >= 3:
            points, status, text = 15, "strong", "Estrutura de tendência positiva."
        elif distance >= 0:
            points, status, text = 11, "positive", "Média de 50 acima da média de 200."
        elif distance >= -3:
            points, status, text = 6, "watch", "Tendência de médio prazo sem confirmação."
        else:
            points, status, text = 0, "warning", "Média de 50 abaixo da média de 200."
        rules.append(technical_rule(
            "sma50_vs_sma200", "Média 50 vs 200 sessões", distance, "%",
            points, 15, status, text,
        ))

    if rsi is None:
        rules.append(unavailable_technical_rule(
            "rsi_14", "RSI 14 sessões", "", 15
        ))
    else:
        if 45 <= rsi <= 68:
            points, status, text = 15, "positive", "Momentum saudável sem sobrecompra extrema."
        elif 35 <= rsi < 45:
            points, status, text = 11, "watch", "Momentum moderado; possível zona de recuperação."
        elif 25 <= rsi < 35:
            points, status, text = 9, "watch", "Ativo tecnicamente pressionado; exige confirmação."
        elif 68 < rsi <= 75:
            points, status, text = 8, "watch", "Momentum forte, mas já esticado."
        elif rsi > 75:
            points, status, text = 3, "warning", "RSI elevado e risco de sobre-extensão."
        else:
            points, status, text = 3, "warning", "RSI muito baixo e tendência fragilizada."
        rules.append(technical_rule(
            "rsi_14", "RSI 14 sessões", rsi, "",
            points, 15, status, text,
        ))

    for rule_id, label, value, max_points in [
        ("return_6m", "Retorno a 6 meses", return_6m, 10),
        ("return_1y", "Retorno a 1 ano", return_1y, 10),
    ]:
        if value is None:
            rules.append(unavailable_technical_rule(rule_id, label, "%", max_points))
        else:
            if value >= 15:
                points, status, text = max_points, "strong", "Momentum positivo no período."
            elif value >= 0:
                points, status, text = round(max_points * 0.7), "positive", "Retorno positivo no período."
            elif value >= -10:
                points, status, text = round(max_points * 0.35), "watch", "Retorno ligeiramente negativo."
            else:
                points, status, text = 0, "warning", "Retorno negativo relevante."
            rules.append(technical_rule(
                rule_id, label, value, "%", points, max_points, status, text,
            ))

    if pullback is None:
        rules.append(unavailable_technical_rule(
            "pullback_52w", "Pullback desde máximo de 52 semanas", "%", 20
        ))
    else:
        if 5 <= pullback <= 18:
            points, status, text = 20, "positive", "Correção relevante sem queda extrema."
        elif 0 <= pullback < 5:
            points, status, text = 13, "watch", "Preço próximo do máximo; margem técnica limitada."
        elif 18 < pullback <= 30:
            points, status, text = 12, "watch", "Pullback profundo; validar a tese e a tendência."
        else:
            points, status, text = 4, "warning", "Queda muito profunda face ao máximo anual."
        rules.append(technical_rule(
            "pullback_52w", "Pullback desde máximo de 52 semanas", pullback, "%",
            points, 20, status, text,
        ))

    if volatility is None:
        rules.append(unavailable_technical_rule(
            "volatility", "Volatilidade anualizada", "%", 10
        ))
    else:
        if volatility <= 20:
            points, status, text = 10, "positive", "Volatilidade relativamente controlada."
        elif volatility <= 35:
            points, status, text = 7, "neutral", "Volatilidade moderada."
        elif volatility <= 55:
            points, status, text = 3, "watch", "Volatilidade elevada."
        else:
            points, status, text = 0, "warning", "Volatilidade muito elevada."
        rules.append(technical_rule(
            "volatility", "Volatilidade anualizada", volatility, "%",
            points, 10, status, text,
        ))

    return rules


def get_technical_snapshot(asset: dict) -> dict:
    yahoo_symbol = yahoo_symbol_for_asset(asset)
    cache_key = yahoo_symbol.upper()
    cached = TECHNICAL_CACHE.get(cache_key)

    if cached:
        age = time.time() - cached["created_at"]
        if age < TECHNICAL_CACHE_TTL_SECONDS:
            return cached["data"]

    try:
        import yfinance as yf
    except ImportError as error:
        raise RuntimeError(
            "A dependência yfinance não está instalada."
        ) from error

    history = yf.Ticker(yahoo_symbol).history(
        period="2y",
        interval="1d",
        auto_adjust=False,
        actions=False,
    )

    if history is None or history.empty or "Close" not in history:
        raise ValueError("O Yahoo Finance não devolveu histórico suficiente.")

    history = history.dropna(subset=["Close"])
    closes = [
        float(value)
        for value in history["Close"].tolist()
        if finite_number(value) is not None
    ]

    if len(closes) < 20:
        raise ValueError("Histórico técnico insuficiente.")

    volumes = []
    if "Volume" in history:
        volumes = [
            finite_number(value)
            for value in history["Volume"].tolist()
        ]
        volumes = [value for value in volumes if value is not None]

    last_close = closes[-1]
    window_52 = closes[-252:] if len(closes) >= 252 else closes
    high_52 = max(window_52)
    low_52 = min(window_52)
    pullback = round((1 - (last_close / high_52)) * 100, 2) if high_52 else None
    distance_low = round(((last_close / low_52) - 1) * 100, 2) if low_52 else None

    indicators = {
        "last_close": round(last_close, 4),
        "sma_20": simple_moving_average(closes, 20),
        "sma_50": simple_moving_average(closes, 50),
        "sma_200": simple_moving_average(closes, 200),
        "rsi_14": calculate_latest_rsi(closes, 14),
        "return_1m_percentage": period_return(closes, 21),
        "return_3m_percentage": period_return(closes, 63),
        "return_6m_percentage": period_return(closes, 126),
        "return_1y_percentage": period_return(closes, 252),
        "high_52w": round(high_52, 4),
        "low_52w": round(low_52, 4),
        "pullback_from_52w_high_percentage": pullback,
        "distance_from_52w_low_percentage": distance_low,
        "annualized_volatility_percentage": annualized_volatility(window_52),
        "maximum_drawdown_percentage": maximum_drawdown(window_52),
        "average_volume_20": (
            round(sum(volumes[-20:]) / len(volumes[-20:]))
            if volumes else None
        ),
    }

    rules = build_technical_rules(indicators)
    available_rules = [rule for rule in rules if rule["status"] != "unavailable"]
    achieved = sum(rule["points"] for rule in available_rules)
    available_max = sum(rule["max_points"] for rule in available_rules)
    total_max = sum(rule["max_points"] for rule in rules)
    score = round((achieved / available_max) * 100) if available_max else None
    coverage = round((available_max / total_max) * 100) if total_max else 0

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

    as_of = None
    try:
        latest_index = history.index[-1]
        as_of = latest_index.date().isoformat()
    except Exception:
        as_of = None

    result = {
        "symbol": yahoo_symbol,
        "provider": "Yahoo Finance via yfinance",
        "as_of": as_of,
        "history_sessions": len(closes),
        "score": score,
        "classification": classify_technical_score(score),
        "coverage_percentage": coverage,
        "indicators": indicators,
        "rules": rules,
        "positive_signals": positive_signals,
        "warning_signals": warning_signals,
        "methodology_note": (
            "Indicadores de preço e tendência usados para timing, pullback e risco. "
            "Não substituem análise fundamental, valuation ou contexto da carteira."
        ),
    }

    TECHNICAL_CACHE[cache_key] = {
        "created_at": time.time(),
        "data": result,
    }

    return result


def enrich_framework_engine_with_technical(
    engine: dict,
    technical: dict | None,
) -> dict:
    if not isinstance(engine, dict) or not technical:
        return engine

    checklist = engine.get("framework_checklist", {}).get("items", [])
    technical_item = next(
        (item for item in checklist if item.get("id") == "technical"),
        None,
    )

    indicators = technical.get("indicators", {})
    available_labels = [
        label
        for key, label in [
            ("sma_20", "média móvel de 20 sessões"),
            ("sma_50", "média móvel de 50 sessões"),
            ("sma_200", "média móvel de 200 sessões"),
            ("rsi_14", "RSI 14 sessões"),
            ("pullback_from_52w_high_percentage", "pullback de 52 semanas"),
            ("annualized_volatility_percentage", "volatilidade anualizada"),
            ("maximum_drawdown_percentage", "drawdown histórico"),
            ("return_1y_percentage", "retorno a 1 ano"),
        ]
        if indicators.get(key) is not None
    ]

    if technical_item:
        technical_item["status"] = (
            "available"
            if technical.get("coverage_percentage", 0) >= 80
            else "partial"
        )
        technical_item["available_data"] = list(dict.fromkeys(
            technical_item.get("available_data", []) + available_labels
        ))
        technical_item["missing_data"] = [
            value
            for value in technical_item.get("missing_data", [])
            if not any(
                token in value.casefold()
                for token in (
                    "pullback", "média", "rsi", "volatil", "máximo", "mínimo"
                )
            )
        ]

    framework = engine.get("framework_checklist", {})
    if checklist:
        available_count = sum(
            1 for item in checklist
            if item.get("status") in {"available", "partial"}
        )
        coverage = round((available_count / len(checklist)) * 100)
        framework["coverage_percentage"] = coverage
        framework["confidence"] = (
            "high" if coverage >= 70 else
            "moderate" if coverage >= 40 else
            "low"
        )

    engine["technical_snapshot"] = technical
    engine["next_required_data"] = [
        item
        for item in engine.get("next_required_data", [])
        if not any(
            token in str(item).casefold()
            for token in (
                "análise técnica", "pullback", "médias móveis", "rsi"
            )
        )
    ]

    decision = engine.get("decision", {})
    if decision and technical.get("classification"):
        decision["technical_context"] = {
            "score": technical.get("score"),
            "classification": technical.get("classification"),
            "pullback_percentage": indicators.get(
                "pullback_from_52w_high_percentage"
            ),
            "rsi_14": indicators.get("rsi_14"),
        }

    return engine


PRODUCT_NAME = "ThesisOS Alpha"
PRODUCT_VERSION = "0.1.0-alpha"
RELEASE_STAGE = "alpha"

FRAMEWORK_ENGINE_VERSION = "0.9"


def fact_value(fact):
    if not isinstance(fact, dict):
        return None

    value = fact.get("value")

    if isinstance(value, (int, float)):
        return float(value)

    return None


def rule_result(
    rule_id: str,
    area: str,
    label: str,
    value,
    unit: str,
    points: int,
    max_points: int,
    status: str,
    interpretation: str,
    source: str,
) -> dict:
    return {
        "id": rule_id,
        "area": area,
        "label": label,
        "value": value,
        "unit": unit,
        "points": points,
        "max_points": max_points,
        "status": status,
        "interpretation": interpretation,
        "source": source,
    }


def unavailable_rule(
    rule_id: str,
    area: str,
    label: str,
    unit: str,
    max_points: int,
    source: str,
) -> dict:
    return rule_result(
        rule_id=rule_id,
        area=area,
        label=label,
        value=None,
        unit=unit,
        points=0,
        max_points=max_points,
        status="unavailable",
        interpretation="Dados ainda não disponíveis.",
        source=source,
    )


def evaluate_revenue_growth(value):
    if value is None:
        return unavailable_rule(
            "revenue_growth_yoy",
            "growth",
            "Crescimento homólogo das receitas",
            "%",
            15,
            "SEC EDGAR",
        )

    if value >= 15:
        points, status, text = 15, "strong", "Crescimento forte das receitas."
    elif value >= 8:
        points, status, text = 12, "positive", "Crescimento saudável das receitas."
    elif value >= 3:
        points, status, text = 8, "neutral", "Crescimento moderado das receitas."
    elif value >= 0:
        points, status, text = 5, "watch", "Receitas praticamente estáveis."
    else:
        points, status, text = 0, "warning", "As receitas estão em contração."

    return rule_result(
        "revenue_growth_yoy",
        "growth",
        "Crescimento homólogo das receitas",
        value,
        "%",
        points,
        15,
        status,
        text,
        "SEC EDGAR",
    )


def evaluate_income_growth(rule_id, label, value):
    if value is None:
        return unavailable_rule(
            rule_id,
            "growth",
            label,
            "%",
            12,
            "SEC EDGAR",
        )

    if value >= 15:
        points, status, text = 12, "strong", "Crescimento forte do resultado."
    elif value >= 5:
        points, status, text = 9, "positive", "Crescimento positivo do resultado."
    elif value >= 0:
        points, status, text = 6, "neutral", "Resultado estável ou com crescimento reduzido."
    else:
        points, status, text = 0, "warning", "O resultado está em contração."

    return rule_result(
        rule_id,
        "growth",
        label,
        value,
        "%",
        points,
        12,
        status,
        text,
        "SEC EDGAR",
    )


def evaluate_margin(rule_id, label, value, operating=False):
    max_points = 15 if operating else 12

    if value is None:
        return unavailable_rule(
            rule_id,
            "profitability",
            label,
            "%",
            max_points,
            "SEC EDGAR + ThesisOS calculation",
        )

    if operating:
        if value >= 25:
            points, status, text = 15, "strong", "Margem operacional elevada."
        elif value >= 15:
            points, status, text = 12, "positive", "Margem operacional saudável."
        elif value >= 8:
            points, status, text = 8, "neutral", "Margem operacional moderada."
        elif value >= 0:
            points, status, text = 4, "watch", "Margem operacional reduzida."
        else:
            points, status, text = 0, "warning", "Resultado operacional negativo."
    else:
        if value >= 20:
            points, status, text = 12, "strong", "Margem líquida elevada."
        elif value >= 10:
            points, status, text = 10, "positive", "Margem líquida saudável."
        elif value >= 5:
            points, status, text = 7, "neutral", "Margem líquida moderada."
        elif value >= 0:
            points, status, text = 4, "watch", "Margem líquida reduzida."
        else:
            points, status, text = 0, "warning", "A empresa apresenta prejuízo líquido."

    return rule_result(
        rule_id,
        "profitability",
        label,
        value,
        "%",
        points,
        max_points,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_dilution(value):
    if value is None:
        return unavailable_rule(
            "diluted_shares_yoy",
            "dilution",
            "Variação homóloga das ações diluídas",
            "%",
            10,
            "SEC EDGAR",
        )

    if value <= -1:
        points, status, text = 10, "strong", "O número diluído de ações diminuiu."
    elif value <= 0:
        points, status, text = 8, "positive", "Não existe diluição observável no período."
    elif value <= 2:
        points, status, text = 5, "neutral", "Diluição baixa, mas deve ser acompanhada."
    elif value <= 5:
        points, status, text = 2, "watch", "Diluição material no período."
    else:
        points, status, text = 0, "warning", "Diluição elevada no período."

    return rule_result(
        "diluted_shares_yoy",
        "dilution",
        "Variação homóloga das ações diluídas",
        value,
        "%",
        points,
        10,
        status,
        text,
        "SEC EDGAR",
    )


def evaluate_liabilities(value):
    if value is None:
        return unavailable_rule(
            "liabilities_to_assets",
            "balance_sheet",
            "Passivos em percentagem dos ativos",
            "%",
            14,
            "SEC EDGAR + ThesisOS calculation",
        )

    if value <= 50:
        points, status, text = 14, "strong", "Estrutura de balanço conservadora neste indicador."
    elif value <= 65:
        points, status, text = 11, "positive", "Nível de passivos controlado neste indicador."
    elif value <= 80:
        points, status, text = 6, "watch", "Peso elevado dos passivos; exige análise da dívida."
    else:
        points, status, text = 1, "warning", "Peso muito elevado dos passivos."

    return rule_result(
        "liabilities_to_assets",
        "balance_sheet",
        "Passivos em percentagem dos ativos",
        value,
        "%",
        points,
        14,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_cash_to_assets(value):
    if value is None:
        return unavailable_rule(
            "cash_to_assets",
            "balance_sheet",
            "Caixa em percentagem dos ativos",
            "%",
            10,
            "SEC EDGAR + ThesisOS calculation",
        )

    if value >= 15:
        points, status, text = 10, "strong", "Reserva de caixa elevada relativamente aos ativos."
    elif value >= 8:
        points, status, text = 8, "positive", "Reserva de caixa relevante."
    elif value >= 3:
        points, status, text = 5, "neutral", "Reserva de caixa moderada."
    elif value >= 1:
        points, status, text = 2, "watch", "Reserva de caixa reduzida."
    else:
        points, status, text = 0, "warning", "Caixa muito reduzida neste indicador."

    return rule_result(
        "cash_to_assets",
        "balance_sheet",
        "Caixa em percentagem dos ativos",
        value,
        "%",
        points,
        10,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_fcf_margin(value):
    if value is None:
        return unavailable_rule(
            "free_cash_flow_margin",
            "cash_flow_quality",
            "Margem de free cash flow",
            "%",
            12,
            "SEC EDGAR + ThesisOS calculation",
        )

    if value >= 20:
        points, status, text = 12, "strong", "Margem de free cash flow elevada."
    elif value >= 10:
        points, status, text = 10, "positive", "Margem de free cash flow saudável."
    elif value >= 5:
        points, status, text = 7, "neutral", "Margem de free cash flow moderada."
    elif value >= 0:
        points, status, text = 3, "watch", "Conversão em free cash flow reduzida."
    else:
        points, status, text = 0, "warning", "Free cash flow negativo."

    return rule_result(
        "free_cash_flow_margin",
        "cash_flow_quality",
        "Margem de free cash flow",
        value,
        "%",
        points,
        12,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_cash_conversion(value):
    if value is None:
        return unavailable_rule(
            "cash_conversion",
            "cash_flow_quality",
            "Fluxo operacional / lucro líquido",
            "%",
            12,
            "SEC EDGAR + ThesisOS calculation",
        )

    if 80 <= value <= 150:
        points, status, text = 12, "strong", "Conversão do lucro em caixa muito sólida."
    elif 60 <= value <= 200:
        points, status, text = 9, "positive", "Conversão do lucro em caixa positiva."
    elif value >= 40:
        points, status, text = 6, "neutral", "Conversão em caixa moderada."
    elif value >= 0:
        points, status, text = 3, "watch", "Conversão do lucro em caixa fraca."
    else:
        points, status, text = 0, "warning", "Fluxo operacional negativo."

    return rule_result(
        "cash_conversion",
        "cash_flow_quality",
        "Fluxo operacional / lucro líquido",
        value,
        "%",
        points,
        12,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_sbc_intensity(value):
    if value is None:
        return unavailable_rule(
            "sbc_to_revenue",
            "dilution",
            "Stock-based compensation / receitas",
            "%",
            8,
            "SEC EDGAR + ThesisOS calculation",
        )

    if value <= 1:
        points, status, text = 8, "strong", "Stock-based compensation muito reduzida."
    elif value <= 3:
        points, status, text = 7, "positive", "Stock-based compensation controlada."
    elif value <= 5:
        points, status, text = 5, "neutral", "Stock-based compensation material, mas moderada."
    elif value <= 10:
        points, status, text = 2, "watch", "Stock-based compensation elevada."
    else:
        points, status, text = 0, "warning", "Stock-based compensation muito elevada."

    return rule_result(
        "sbc_to_revenue",
        "dilution",
        "Stock-based compensation / receitas",
        value,
        "%",
        points,
        8,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_net_debt_to_fcf(value):
    if value is None:
        return unavailable_rule(
            "net_debt_to_annual_fcf",
            "balance_sheet",
            "Dívida líquida / FCF anual",
            "x",
            12,
            "SEC EDGAR + ThesisOS calculation",
        )

    if value <= 0:
        points, status, text = 12, "strong", "A empresa apresenta caixa líquido."
    elif value <= 1:
        points, status, text = 11, "positive", "Dívida líquida facilmente coberta pelo FCF anual."
    elif value <= 2:
        points, status, text = 9, "positive", "Cobertura da dívida por FCF confortável."
    elif value <= 3:
        points, status, text = 6, "neutral", "Alavancagem moderada face ao FCF."
    elif value <= 5:
        points, status, text = 2, "watch", "Alavancagem elevada face ao FCF."
    else:
        points, status, text = 0, "warning", "Dívida líquida muito elevada face ao FCF."

    return rule_result(
        "net_debt_to_annual_fcf",
        "balance_sheet",
        "Dívida líquida / FCF anual",
        value,
        "x",
        points,
        12,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def evaluate_capital_returns(value, components_complete):
    if value is None or not components_complete:
        return unavailable_rule(
            "capital_returns_to_fcf",
            "capital_allocation",
            "Dividendos e recompras / FCF",
            "%",
            6,
            "SEC EDGAR + ThesisOS calculation",
        )

    if 50 <= value <= 100:
        points, status, text = 6, "strong", (
            "Retorno aos acionistas coberto pelo FCF; "
            "a qualidade das recompras depende do valuation."
        )
    elif 0 <= value < 50:
        points, status, text = 5, "positive", (
            "Retorno aos acionistas conservador face ao FCF."
        )
    elif value <= 125:
        points, status, text = 3, "watch", (
            "Retorno aos acionistas próximo ou acima do FCF."
        )
    else:
        points, status, text = 0, "warning", (
            "Dividendos e recompras excedem claramente o FCF."
        )

    return rule_result(
        "capital_returns_to_fcf",
        "capital_allocation",
        "Dividendos e recompras / FCF",
        value,
        "%",
        points,
        6,
        status,
        text,
        "SEC EDGAR + ThesisOS calculation",
    )


def framework_item(
    item_id: str,
    label: str,
    status: str,
    available_data: list,
    missing_data: list,
) -> dict:
    return {
        "id": item_id,
        "label": label,
        "status": status,
        "available_data": available_data,
        "missing_data": missing_data,
    }


def classify_quantitative_score(score):
    if score is None:
        return {
            "code": "insufficient_data",
            "label": "Dados insuficientes",
        }

    if score >= 80:
        return {"code": "strong", "label": "Forte"}

    if score >= 65:
        return {"code": "positive", "label": "Positiva"}

    if score >= 45:
        return {"code": "mixed", "label": "Mista"}

    return {"code": "weak", "label": "Fraca"}


def build_stock_framework_engine(
    asset: dict,
    fundamentals: dict | None,
    sources: dict,
    data_quality: dict,
) -> dict:
    duration = (fundamentals or {}).get("duration_metrics", {})
    instant = (fundamentals or {}).get("instant_metrics", {})
    derived = (fundamentals or {}).get("derived_metrics", {})

    revenue = duration.get("revenue", {})
    net_income = duration.get("net_income", {})
    operating_income = duration.get("operating_income", {})
    diluted_shares = duration.get("diluted_shares", {})
    cash_flow = (fundamentals or {}).get(
        "cash_flow_and_allocation",
        {},
    )
    debt_components = (fundamentals or {}).get(
        "debt_components",
        {},
    )

    assets_value = fact_value(instant.get("assets"))
    cash_value = fact_value(instant.get("cash"))

    cash_to_assets = None

    if assets_value not in (None, 0) and cash_value is not None:
        cash_to_assets = round((cash_value / assets_value) * 100, 2)

    rules = [
        evaluate_revenue_growth(
            revenue.get("quarter_yoy_percentage")
        ),
        evaluate_income_growth(
            "net_income_growth_yoy",
            "Crescimento homólogo do lucro líquido",
            net_income.get("quarter_yoy_percentage"),
        ),
        evaluate_income_growth(
            "operating_income_growth_yoy",
            "Crescimento homólogo do resultado operacional",
            operating_income.get("quarter_yoy_percentage"),
        ),
        evaluate_margin(
            "operating_margin",
            "Margem operacional trimestral",
            derived.get("operating_margin_quarter_percentage"),
            operating=True,
        ),
        evaluate_margin(
            "net_margin",
            "Margem líquida trimestral",
            derived.get("net_margin_quarter_percentage"),
            operating=False,
        ),
        evaluate_dilution(
            diluted_shares.get("quarter_yoy_percentage")
        ),
        evaluate_liabilities(
            derived.get("liabilities_to_assets_percentage")
        ),
        evaluate_cash_to_assets(cash_to_assets),
        evaluate_fcf_margin(
            derived.get("free_cash_flow_margin_percentage")
        ),
        evaluate_cash_conversion(
            derived.get(
                "operating_cash_flow_to_net_income_percentage"
            )
        ),
        evaluate_sbc_intensity(
            derived.get(
                "stock_based_compensation_to_revenue_percentage"
            )
        ),
        evaluate_net_debt_to_fcf(
            derived.get("net_debt_to_annual_free_cash_flow")
        ),
        evaluate_capital_returns(
            derived.get(
                "capital_returns_to_free_cash_flow_percentage"
            ),
            derived.get(
                "capital_returns_components_complete"
            ),
        ),
    ]

    available_rules = [
        rule
        for rule in rules
        if rule["status"] != "unavailable"
    ]

    achieved_points = sum(
        rule["points"]
        for rule in available_rules
    )

    available_max_points = sum(
        rule["max_points"]
        for rule in available_rules
    )

    total_max_points = sum(
        rule["max_points"]
        for rule in rules
    )

    quantitative_score = None

    if available_max_points:
        quantitative_score = round(
            (achieved_points / available_max_points) * 100
        )

    quantitative_coverage = round(
        (available_max_points / total_max_points) * 100
    ) if total_max_points else 0

    classification = classify_quantitative_score(
        quantitative_score
    )

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

    checklist = [
        framework_item(
            "asset_identity",
            "O que estamos realmente a comprar?",
            "partial",
            [
                "tipo de ativo",
                "ticker",
                "bolsa",
                "moeda",
                "indústria",
            ],
            [
                "papel na carteira",
                "horizonte",
                "condições da tese",
                "sinais de invalidação",
            ],
        ),
        framework_item(
            "official_documentation",
            "Documentação e fontes oficiais",
            "partial",
            [
                "Company Facts SEC",
                "10-Q/10-K quantitativos",
                "identificação OpenFIGI",
                "cotação de mercado",
            ],
            [
                "notas às contas",
                "8-K",
                "conference call",
                "guidance",
                "notícias materiais",
            ],
        ),
        framework_item(
            "growth",
            "Crescimento",
            "partial",
            [
                "receitas YoY",
                "lucro líquido YoY",
                "resultado operacional YoY",
            ],
            [
                "crescimento orgânico",
                "backlog",
                "guidance",
                "crescimento de FCF",
                "mercado endereçável",
            ],
        ),
        framework_item(
            "profitability_quality",
            "Rentabilidade e qualidade dos lucros",
            "partial",
            [
                "margem operacional",
                "margem líquida",
                "free cash flow",
                "margem de free cash flow",
                "conversão de lucro em caixa",
                "capex",
            ],
            [
                "margem bruta",
                "ROIC",
                "ROE",
                "contas a receber e inventários",
                "normalização de itens não recorrentes",
            ],
        ),
        framework_item(
            "balance_sheet",
            "Balanço e dívida",
            "partial",
            [
                "ativos",
                "passivos",
                "capital próprio",
                "caixa",
                "dívida reportada",
                "dívida líquida",
                "dívida líquida / FCF anual",
            ],
            [
                "dívida líquida/EBITDA",
                "cobertura de juros",
                "taxas e maturidades",
                "covenants",
                "leases e pensões",
            ],
        ),
        framework_item(
            "dilution",
            "Diluição e valor por ação",
            "partial",
            [
                "variação das ações diluídas",
                "stock-based compensation / receitas",
                "recompras reportadas",
            ],
            [
                "opções",
                "RSUs",
                "convertíveis",
                "warrants",
                "preço médio das recompras",
            ],
        ),
        framework_item(
            "capital_allocation",
            "Alocação de capital",
            "partial",
            [
                "capex",
                "dividendos",
                "recompras",
                "retorno aos acionistas / FCF",
            ],
            [
                "retorno sobre reinvestimento",
                "aquisições e desinvestimentos",
                "valuation das recompras",
                "consistência histórica da política",
            ],
        ),
        framework_item(
            "business_quality",
            "Qualidade do negócio e management",
            "missing",
            [],
            [
                "moat",
                "pricing power",
                "concentração de clientes",
                "fornecedores",
                "management",
                "alocação de capital",
                "insiders",
                "concorrência",
            ],
        ),
        framework_item(
            "valuation",
            "Valuation e cenários",
            "missing",
            [],
            [
                "múltiplos atuais e históricos",
                "comparáveis",
                "reverse DCF",
                "bear/base/bull",
                "retorno esperado",
            ],
        ),
        framework_item(
            "technical",
            "Pullback e análise técnica",
            "missing",
            [],
            [
                "máximos",
                "médias móveis",
                "RSI",
                "volume",
                "suportes e resistências",
                "força relativa",
            ],
        ),
        framework_item(
            "portfolio_fit",
            "Encaixe na carteira",
            "missing",
            [],
            [
                "peso atual",
                "limite por empresa",
                "setor e geografia",
                "correlação",
                "liquidez futura",
            ],
        ),
        framework_item(
            "decision_monitoring",
            "Decisão e monitorização",
            "blocked",
            [],
            [
                "comprar/manter/evitar/vender",
                "zona de entrada",
                "tamanho da posição",
                "plano de reforços",
                "catalisadores",
                "quebra da tese",
                "próxima revisão",
                "alertas",
            ],
        ),
    ]

    framework_sections_available = sum(
        1
        for item in checklist
        if item["status"] in {"available", "partial"}
    )

    framework_coverage = round(
        (framework_sections_available / len(checklist)) * 100
    )

    if framework_coverage >= 70:
        confidence = "high"
    elif framework_coverage >= 40:
        confidence = "moderate"
    else:
        confidence = "low"

    next_required_data = [
        "ROIC, ROE e margem bruta",
        "receivables, inventários e itens não recorrentes",
        "cobertura de juros, maturidades, leases e covenants",
        "crescimento orgânico, backlog e guidance",
        "moat, concorrência e qualidade da administração",
        "retorno sobre reinvestimento e valuation das recompras",
        "valuation, reverse DCF e cenários",
        "análise técnica e pullback",
        "peso e encaixe na carteira",
    ]

    return {
        "version": FRAMEWORK_ENGINE_VERSION,
        "asset_type": "stock",
        "status": "partial_assessment",
        "scope": (
            "Snapshot quantitativo baseado em crescimento, margens, "
            "cash flow, dívida, diluição e alocação de capital. "
            "Não representa ainda uma análise integral."
        ),
        "frameworks_applied": [
            {
                "id": "analysis",
                "name": "Framework de análise",
                "status": "partial",
            },
            {
                "id": "documentation",
                "name": "Framework de documentação",
                "status": "partial",
                "official_sources_used": [
                    source.get("provider")
                    for source in sources.values()
                    if source.get("status") == "ok"
                ],
            },
            {
                "id": "decision_monitoring",
                "name": "Framework de decisão e monitorização",
                "status": "blocked",
                "reason": (
                    "Valuation, contexto da carteira, análise "
                    "qualitativa e momento de entrada ainda ausentes."
                ),
            },
        ],
        "quantitative_snapshot": {
            "score": quantitative_score,
            "classification": classification,
            "achieved_points": achieved_points,
            "available_max_points": available_max_points,
            "total_max_points": total_max_points,
            "coverage_percentage": quantitative_coverage,
            "rules": rules,
            "positive_signals": positive_signals,
            "warning_signals": warning_signals,
            "methodology_note": (
                "Limiares genéricos e transparentes aplicados a "
                "dados reportados. Devem ser ajustados posteriormente "
                "ao setor, modelo de negócio e ciclo económico."
            ),
        },
        "framework_checklist": {
            "coverage_percentage": framework_coverage,
            "confidence": confidence,
            "items": checklist,
        },
        "data_quality": data_quality,
        "next_required_data": next_required_data,
        "decision": {
            "status": "awaiting_full_assessment",
            "action": "monitor",
            "label": "Aguardar valuation e revisão qualitativa",
            "buy_hold_avoid_sell": None,
            "entry_zone": None,
            "position_size": None,
            "reinforcement_plan": None,
            "catalysts": [],
            "thesis_break_signals": [],
            "next_review": "Após novo filing ou dado material",
            "reason": (
                "Os frameworks não permitem uma recomendação final "
                "apenas com o snapshot quantitativo disponível."
            ),
        },
    }


def classify_etf_structural_score(score):
    if score is None:
        return {
            "code": "insufficient_data",
            "label": "Dados insuficientes",
        }

    if score >= 85:
        return {
            "code": "very_strong_etf_structure",
            "label": "Estrutura muito forte",
        }

    if score >= 70:
        return {
            "code": "strong_etf_structure",
            "label": "Estrutura forte",
        }

    if score >= 55:
        return {
            "code": "reasonable_etf_structure",
            "label": "Estrutura razoável",
        }

    if score >= 40:
        return {
            "code": "weak_etf_structure",
            "label": "Estrutura frágil",
        }

    return {
        "code": "insufficient_etf_structure",
        "label": "Estrutura insuficiente",
    }


def evaluate_etf_cost_rule(value):
    if value is None:
        return unavailable_rule(
            "etf_ongoing_charges",
            "costs",
            "OCF/TER oficial",
            "%",
            20,
            "Official issuer documents",
        )

    if value <= 0.15:
        points, status, text = 20, "strong", "Custo anual muito reduzido."
    elif value <= 0.25:
        points, status, text = 18, "positive", "Custo anual reduzido."
    elif value <= 0.40:
        points, status, text = 14, "neutral", "Custo anual moderado."
    elif value <= 0.60:
        points, status, text = 8, "watch", "Custo anual acima da média de ETFs simples."
    else:
        points, status, text = 2, "warning", "Custo anual elevado."

    return rule_result(
        "etf_ongoing_charges",
        "costs",
        "OCF/TER oficial",
        value,
        "%",
        points,
        20,
        status,
        text,
        "Official issuer documents",
    )


def evaluate_etf_tracking_rule(value):
    if value is None:
        return unavailable_rule(
            "etf_tracking_error_5y",
            "tracking",
            "Tracking error a 5 anos",
            "%",
            20,
            "Official issuer documents",
        )

    if value <= 0.10:
        points, status, text = 20, "strong", "Tracking histórico muito próximo do índice."
    elif value <= 0.20:
        points, status, text = 17, "positive", "Tracking histórico eficiente."
    elif value <= 0.40:
        points, status, text = 12, "neutral", "Tracking histórico aceitável."
    elif value <= 0.75:
        points, status, text = 6, "watch", "Tracking error material."
    else:
        points, status, text = 1, "warning", "Tracking error elevado."

    return rule_result(
        "etf_tracking_error_5y",
        "tracking",
        "Tracking error a 5 anos",
        value,
        "%",
        points,
        20,
        status,
        text,
        "Official issuer documents",
    )


def evaluate_etf_positions_rule(value):
    if value is None:
        return unavailable_rule(
            "etf_number_of_positions",
            "diversification",
            "Número de posições",
            "",
            15,
            "Official issuer documents",
        )

    if value >= 1000:
        points, status, text = 15, "strong", "Diversificação nominal muito elevada."
    elif value >= 500:
        points, status, text = 13, "positive", "Diversificação nominal elevada."
    elif value >= 100:
        points, status, text = 9, "neutral", "Diversificação nominal razoável."
    elif value >= 50:
        points, status, text = 5, "watch", "Número de posições relativamente reduzido."
    else:
        points, status, text = 2, "warning", "ETF concentrado em poucas posições."

    return rule_result(
        "etf_number_of_positions",
        "diversification",
        "Número de posições",
        value,
        "",
        points,
        15,
        status,
        text,
        "Official issuer documents",
    )


def evaluate_etf_top10_rule(value):
    if value is None:
        return unavailable_rule(
            "etf_top10_concentration",
            "concentration",
            "Peso das 10 maiores posições",
            "%",
            10,
            "Official issuer documents + ThesisOS calculation",
        )

    if value <= 20:
        points, status, text = 10, "strong", "Concentração reduzida nas maiores posições."
    elif value <= 30:
        points, status, text = 8, "positive", "Concentração moderada nas maiores posições."
    elif value <= 40:
        points, status, text = 6, "neutral", "Concentração relevante nas maiores posições."
    elif value <= 55:
        points, status, text = 3, "watch", "Top 10 com peso elevado."
    else:
        points, status, text = 0, "warning", "Top 10 excessivamente concentrado."

    return rule_result(
        "etf_top10_concentration",
        "concentration",
        "Peso das 10 maiores posições",
        value,
        "%",
        points,
        10,
        status,
        text,
        "Official issuer documents + ThesisOS calculation",
    )


def evaluate_etf_country_concentration_rule(value):
    if value is None:
        return unavailable_rule(
            "etf_largest_country",
            "concentration",
            "Peso do maior país",
            "%",
            10,
            "Official issuer documents",
        )

    if value <= 35:
        points, status, text = 10, "strong", "Exposição geográfica muito distribuída."
    elif value <= 50:
        points, status, text = 8, "positive", "Exposição geográfica equilibrada."
    elif value <= 65:
        points, status, text = 5, "watch", "Existe concentração material no maior país."
    elif value <= 80:
        points, status, text = 2, "watch", "Concentração geográfica elevada."
    else:
        points, status, text = 0, "warning", "Concentração geográfica muito elevada."

    return rule_result(
        "etf_largest_country",
        "concentration",
        "Peso do maior país",
        value,
        "%",
        points,
        10,
        status,
        text,
        "Official issuer documents",
    )


def evaluate_etf_sector_concentration_rule(value, sector_name):
    if value is None:
        return unavailable_rule(
            "etf_largest_sector",
            "concentration",
            "Peso do maior setor",
            "%",
            10,
            "Official issuer documents",
        )

    if value <= 20:
        points, status, text = 10, "strong", "Exposição setorial muito distribuída."
    elif value <= 30:
        points, status, text = 8, "positive", "Exposição setorial equilibrada."
    elif value <= 40:
        points, status, text = 5, "watch", "Existe concentração material no maior setor."
    elif value <= 50:
        points, status, text = 2, "watch", "Concentração setorial elevada."
    else:
        points, status, text = 0, "warning", "Concentração setorial muito elevada."

    label = (
        f"Maior setor — {sector_name}"
        if sector_name
        else "Peso do maior setor"
    )

    return rule_result(
        "etf_largest_sector",
        "concentration",
        label,
        value,
        "%",
        points,
        10,
        status,
        text,
        "Official issuer documents",
    )


def evaluate_etf_structure_rule(profile):
    checks = [
        bool(profile.get("benchmark")),
        bool(profile.get("factsheet_url")),
        bool(profile.get("kiid_url")),
        bool(profile.get("total_assets") or profile.get("share_class_assets")),
        bool(profile.get("investment_method")),
        bool(profile.get("domicile")),
        bool(profile.get("share_class_inception") or profile.get("listing_date")),
    ]

    weights = [3, 2, 2, 3, 2, 1, 2]
    points = sum(
        weight
        for available, weight in zip(checks, weights)
        if available
    )

    if points >= 13:
        status, text = "strong", "Estrutura e documentação oficial muito completas."
    elif points >= 10:
        status, text = "positive", "Estrutura e documentação oficial sólidas."
    elif points >= 7:
        status, text = "neutral", "Estrutura parcialmente documentada."
    elif points >= 4:
        status, text = "watch", "Faltam elementos estruturais importantes."
    else:
        status, text = "warning", "Estrutura insuficientemente documentada."

    return rule_result(
        "etf_structure_documentation",
        "structure",
        "Estrutura e documentação",
        f"{points}/15",
        "",
        points,
        15,
        status,
        text,
        "Official issuer documents + ThesisOS checks",
    )


def build_etf_framework_engine(
    asset: dict,
    etf_profile: dict | None,
    sources: dict,
    data_quality: dict,
) -> dict:
    etf_profile = etf_profile or {}

    official_available = [
        label
        for key, label in [
            ("source_url", "página oficial do emitente"),
            ("factsheet_url", "factsheet oficial"),
            ("kiid_url", "KID/KIID oficial"),
            ("share_class_inception", "início da classe"),
            ("listing_date", "data de listagem"),
        ]
        if etf_profile.get(key)
    ]

    official_missing = [
        label
        for key, label in [
            ("source_url", "página oficial do emitente"),
            ("factsheet_url", "factsheet oficial"),
            ("kiid_url", "KID/KIID oficial"),
            ("share_class_inception", "início da classe"),
            ("listing_date", "data de listagem"),
        ]
        if not etf_profile.get(key)
    ]

    methodology_available = [
        label
        for key, label in [
            ("benchmark", "índice seguido"),
            ("investment_method", "método de replicação"),
            ("investment_structure", "estrutura do fundo"),
            ("strategy", "estratégia oficial"),
        ]
        if etf_profile.get(key)
    ]

    methodology_missing = [
        label
        for key, label in [
            ("benchmark", "índice seguido"),
            ("investment_method", "método de replicação"),
            ("investment_structure", "estrutura do fundo"),
            ("strategy", "estratégia oficial"),
        ]
        if not etf_profile.get(key)
    ] + [
        "regras detalhadas de inclusão",
        "rebalanceamento",
    ]

    holdings_available = [
        label
        for available, label in [
            (
                bool(etf_profile.get("number_of_stocks")),
                "número de posições",
            ),
            (
                bool(etf_profile.get("top_holdings")),
                "top holdings",
            ),
            (
                bool(etf_profile.get("market_allocation")),
                "exposição por países",
            ),
            (
                bool(
                    etf_profile.get("concentration", {}).get(
                        "top_10_weight_percentage"
                    )
                    is not None
                ),
                "concentração do top 10",
            ),
            (
                bool(
                    etf_profile.get("top_holdings")
                    and any(
                        item.get("sector")
                        for item in etf_profile.get(
                            "top_holdings",
                            [],
                        )
                    )
                ),
                "setores das principais posições",
            ),
            (
                bool(etf_profile.get("sector_allocation")),
                "alocação setorial completa",
            ),
        ]
        if available
    ]

    holdings_missing = [
        item
        for item in [
            (
                "top holdings"
                if not etf_profile.get("top_holdings")
                else None
            ),
            (
                "geografias"
                if not etf_profile.get(
                    "market_allocation"
                )
                else None
            ),
            (
                "alocação setorial completa"
                if not etf_profile.get("sector_allocation")
                else None
            ),
            "exposição por moedas das holdings",
            "overlap com outros ETFs",
        ]
        if item
    ]

    tracking_error = etf_profile.get(
        "tracking_error",
        {},
    )
    characteristics = etf_profile.get(
        "characteristics",
        {},
    )
    prices_and_structure = etf_profile.get(
        "prices_and_structure",
        {},
    )
    concentration = etf_profile.get(
        "concentration",
        {},
    )
    sector_allocation = etf_profile.get(
        "sector_allocation",
        [],
    )
    largest_sector = etf_profile.get(
        "largest_sector",
        {},
    ) or {}
    ongoing_charges = etf_profile.get(
        "ongoing_charges_percentage"
    )
    equity_yield = etf_profile.get(
        "equity_yield_percentage"
    )
    portfolio_turnover = etf_profile.get(
        "portfolio_turnover_percentage"
    )

    tracking_error_available = any(
        value is not None
        for value in tracking_error.values()
    )

    implementation_available = [
        label
        for available, label in [
            (
                ongoing_charges is not None
                or bool(etf_profile.get("ocf_ter")),
                "TER/OCF",
            ),
            (
                equity_yield is not None,
                "dividend yield agregado",
            ),
            (
                portfolio_turnover is not None,
                "turnover reportado",
            ),
            (
                bool(etf_profile.get("share_class_assets")),
                "ativos da classe",
            ),
            (
                bool(etf_profile.get("total_assets")),
                "ativos totais",
            ),
            (
                bool(etf_profile.get("investment_method")),
                "replicação",
            ),
            (
                bool(etf_profile.get("domicile")),
                "domicílio",
            ),
            (
                bool(etf_profile.get("distribution_policy")),
                "política de distribuição",
            ),
            (
                bool(etf_profile.get("tax_status")),
                "estatuto fiscal",
            ),
            (
                tracking_error_available,
                "tracking error",
            ),
            (
                (
                    prices_and_structure
                    .get("outstanding_shares", {})
                    .get("value")
                    is not None
                ),
                "unidades em circulação",
            ),
            (
                bool(
                    prices_and_structure.get(
                        "base_currency"
                    )
                ),
                "moeda base",
            ),
            (
                bool(
                    prices_and_structure.get(
                        "listed_currencies"
                    )
                ),
                "moedas de listagem",
            ),
        ]
        if available
    ]

    implementation_missing = [
        label
        for available, label in [
            (
                ongoing_charges is not None
                or bool(etf_profile.get("ocf_ter")),
                "TER/OCF",
            ),
            (
                equity_yield is not None,
                "dividend yield agregado",
            ),
            (
                portfolio_turnover is not None,
                "turnover reportado",
            ),
            (
                bool(etf_profile.get("share_class_assets")),
                "ativos da classe",
            ),
            (
                bool(etf_profile.get("total_assets")),
                "ativos totais",
            ),
            (
                bool(etf_profile.get("investment_method")),
                "replicação",
            ),
            (
                bool(etf_profile.get("domicile")),
                "domicílio",
            ),
            (
                bool(etf_profile.get("distribution_policy")),
                "política de distribuição",
            ),
            (
                bool(etf_profile.get("tax_status")),
                "estatuto fiscal",
            ),
            (
                tracking_error_available,
                "tracking error",
            ),
        ]
        if not available
    ] + [
        "tracking difference",
        "spread",
        "liquidez",
    ]

    valuation_available = [
        label
        for key, label in [
            ("price_to_earnings", "P/E agregado"),
            ("price_to_book", "P/B agregado"),
            (
                "return_on_equity_percentage",
                "ROE agregado",
            ),
            (
                "earnings_growth_percentage",
                "crescimento agregado dos lucros",
            ),
        ]
        if characteristics.get(key, {}).get("fund")
        is not None
    ]

    valuation_missing = [
        "valuation histórico",
        "earnings yield",
        "comparação com ETFs alternativos",
    ]

    technical_available = [
        label
        for key, label in [
            ("nav_52_week_high", "máximo NAV 52 semanas"),
            ("nav_52_week_low", "mínimo NAV 52 semanas"),
        ]
        if prices_and_structure.get(key)
    ]

    technical_missing = [
        "pullback",
        "médias móveis",
        "suportes",
        "RSI",
        "força relativa",
    ]

    profile_available = bool(etf_profile)

    etf_rules = [
        evaluate_etf_cost_rule(ongoing_charges),
        evaluate_etf_tracking_rule(
            tracking_error.get("five_year_percentage")
        ),
        evaluate_etf_positions_rule(
            etf_profile.get("number_of_stocks")
        ),
        evaluate_etf_top10_rule(
            concentration.get("top_10_weight_percentage")
        ),
        evaluate_etf_country_concentration_rule(
            concentration.get("largest_country_percentage")
        ),
        evaluate_etf_sector_concentration_rule(
            largest_sector.get("weight_percentage"),
            largest_sector.get("sector"),
        ),
        evaluate_etf_structure_rule(etf_profile),
    ] if profile_available else []

    etf_available_rules = [
        rule
        for rule in etf_rules
        if rule["status"] != "unavailable"
    ]

    etf_achieved_points = sum(
        rule["points"]
        for rule in etf_available_rules
    )

    etf_available_max_points = sum(
        rule["max_points"]
        for rule in etf_available_rules
    )

    etf_total_max_points = sum(
        rule["max_points"]
        for rule in etf_rules
    )

    etf_structural_score = None

    if etf_available_max_points:
        etf_structural_score = round(
            (
                etf_achieved_points
                / etf_available_max_points
            )
            * 100
        )

    etf_structural_coverage = (
        round(
            (
                etf_available_max_points
                / etf_total_max_points
            )
            * 100
        )
        if etf_total_max_points
        else 0
    )

    etf_structural_classification = (
        classify_etf_structural_score(
            etf_structural_score
        )
    )

    etf_positive_signals = [
        {
            "rule_id": rule["id"],
            "label": rule["label"],
            "value": rule["value"],
            "unit": rule["unit"],
            "interpretation": rule["interpretation"],
        }
        for rule in etf_rules
        if rule["status"] in {"strong", "positive"}
    ]

    etf_warning_signals = [
        {
            "rule_id": rule["id"],
            "label": rule["label"],
            "value": rule["value"],
            "unit": rule["unit"],
            "interpretation": rule["interpretation"],
        }
        for rule in etf_rules
        if rule["status"] in {"watch", "warning"}
    ]

    checklist = [
        framework_item(
            "asset_identity",
            "O que estamos realmente a comprar?",
            "partial",
            [
                "tipo ETF",
                "ticker/ISIN",
                "listagem",
                "moeda",
                "país",
            ],
            [
                "papel na carteira",
                "horizonte",
                "tese e invalidadores",
            ],
        ),
        framework_item(
            "official_documentation",
            "Documentação e fontes",
            "partial",
            [
                "identificação OpenFIGI/EODHD",
                "cotação Finnhub/EODHD",
                "câmbio BCE",
                *official_available,
            ],
            [
                *official_missing,
                "relatório anual do fundo",
            ],
        ),
        framework_item(
            "index_methodology",
            "Índice e metodologia",
            (
                "partial"
                if methodology_available
                else "missing"
            ),
            methodology_available,
            methodology_missing,
        ),
        framework_item(
            "holdings_exposure",
            "Holdings e exposição real",
            (
                "partial"
                if holdings_available
                else "missing"
            ),
            holdings_available,
            holdings_missing,
        ),
        framework_item(
            "costs_implementation",
            "Custos e implementação",
            (
                "partial"
                if implementation_available
                else "missing"
            ),
            implementation_available,
            implementation_missing,
        ),
        framework_item(
            "aggregate_valuation",
            "Valuation agregado",
            (
                "partial"
                if valuation_available
                else "missing"
            ),
            valuation_available,
            valuation_missing,
        ),
        framework_item(
            "technical",
            "Pullback e momento de entrada",
            (
                "partial"
                if technical_available
                else "missing"
            ),
            technical_available,
            technical_missing,
        ),
        framework_item(
            "portfolio_fit",
            "Encaixe na carteira",
            "missing",
            [],
            [
                "overlap com ETFs existentes",
                "peso",
                "correlação",
                "concentração escondida",
                "risco cambial",
            ],
        ),
        framework_item(
            "decision_monitoring",
            "Decisão e monitorização",
            "blocked",
            [],
            [
                "comprar/manter/evitar/vender",
                "zona de entrada",
                "tamanho",
                "reforços",
                "alertas",
                "próxima revisão",
            ],
        ),
    ]

    framework_sections_available = sum(
        1
        for item in checklist
        if item["status"] in {"available", "partial"}
    )

    framework_coverage = round(
        (framework_sections_available / len(checklist)) * 100
    )

    return {
        "version": FRAMEWORK_ENGINE_VERSION,
        "asset_type": "etf",
        "status": (
            "partial_etf_fundamentals"
            if profile_available
            else "insufficient_etf_fundamentals"
        ),
        "scope": (
            (
                "Identificação, preço, moeda, composição, custos, "
                "alocação setorial, tracking error, concentração, "
                "valuation agregado e score estrutural já estão ligados. "
                "Tracking difference, liquidez, análise técnica e "
                "carteira continuam incompletos."
            )
            if profile_available
            else (
                "Identificação, preço e moeda já estão ligados. "
                "A análise estrutural do ETF ainda necessita de dados "
                "oficiais sobre custos, índice, holdings e tracking."
            )
        ),
        "frameworks_applied": [
            {
                "id": "analysis",
                "name": "Framework de análise",
                "status": "partial",
            },
            {
                "id": "documentation",
                "name": "Framework de documentação",
                "status": "partial",
                "official_sources_used": [
                    source.get("provider")
                    for source in sources.values()
                    if source.get("status") == "ok"
                ],
            },
            {
                "id": "decision_monitoring",
                "name": "Framework de decisão e monitorização",
                "status": "blocked",
                "reason": (
                    "Tracking difference, liquidez, análise técnica, "
                    "overlap e contexto da carteira ainda incompletos."
                ),
            },
        ],
        "quantitative_snapshot": {
            "score": etf_structural_score,
            "classification": etf_structural_classification,
            "coverage_percentage": etf_structural_coverage,
            "rules": etf_rules,
            "positive_signals": etf_positive_signals,
            "warning_signals": etf_warning_signals,
            "etf_metrics": {
                "tracking_error": tracking_error,
                "characteristics": characteristics,
                "concentration": concentration,
                "prices_and_structure": prices_and_structure,
                "ongoing_charges_percentage": ongoing_charges,
                "equity_yield_percentage": equity_yield,
                "portfolio_turnover_percentage": portfolio_turnover,
                "sector_allocation": sector_allocation,
                "largest_sector": largest_sector,
            },
            "methodology_note": (
                "Score estrutural de ETF baseado em custos, tracking, "
                "diversificação, concentração, estrutura e qualidade "
                "documental. Não avalia o momento de compra, retorno "
                "esperado nem o encaixe na carteira."
            ),
        },
        "framework_checklist": {
            "coverage_percentage": framework_coverage,
            "confidence": (
                "moderate"
                if profile_available
                else "low"
            ),
            "items": checklist,
        },
        "data_quality": data_quality,
        "next_required_data": [
            item
            for item in [
                (
                    "factsheet/KID oficial"
                    if not etf_profile.get("factsheet_url")
                    else None
                ),
                (
                    "TER/OCF"
                    if ongoing_charges is None
                    and not etf_profile.get("ocf_ter")
                    else None
                ),
                "tracking difference",
                "liquidez e spread",
                (
                    "alocação setorial completa"
                    if not sector_allocation
                    else None
                ),
                "exposição cambial das holdings",
                "overlap com a carteira",
                "valuation histórico e comparação com alternativas",
                "análise técnica e zona de reforço",
            ]
            if item
        ],
        "decision": {
            "status": (
                "awaiting_remaining_etf_assessment"
                if profile_available
                else "awaiting_etf_fundamentals"
            ),
            "action": "monitor",
            "label": (
                "Aguardar tracking difference, técnica e carteira"
                if profile_available
                else "Aguardar dados estruturais do ETF"
            ),
            "buy_hold_avoid_sell": None,
            "entry_zone": None,
            "position_size": None,
            "reinforcement_plan": None,
            "catalysts": [],
            "thesis_break_signals": [],
            "next_review": (
                "Após integração de tracking difference, técnica e overlap"
                if profile_available
                else "Após integração do factsheet oficial"
            ),
            "reason": (
                (
                    "Custos, composição, concentração, tracking error e "
                    "valuation agregado já estão parcialmente ligados, "
                    "mas faltam tracking difference, liquidez, momento "
                    "de entrada e encaixe na carteira."
                )
                if profile_available
                else (
                    "A identificação e a cotação não bastam para "
                    "avaliar a qualidade e o encaixe de um ETF."
                )
            ),
        },
    }


def build_framework_engine(
    asset: dict,
    fundamentals: dict | None,
    etf_profile: dict | None,
    sources: dict,
    data_quality: dict,
) -> dict:
    asset_type = asset.get("asset_type")

    if asset_type == "stock":
        return build_stock_framework_engine(
            asset,
            fundamentals,
            sources,
            data_quality,
        )

    if asset_type == "etf":
        return build_etf_framework_engine(
            asset,
            etf_profile,
            sources,
            data_quality,
        )

    return {
        "version": FRAMEWORK_ENGINE_VERSION,
        "asset_type": asset_type,
        "status": "unsupported_asset_type",
        "scope": (
            "Este tipo de ativo ainda não tem um motor específico."
        ),
        "frameworks_applied": [],
        "quantitative_snapshot": {
            "score": None,
            "classification": {
                "code": "not_available",
                "label": "Não disponível",
            },
            "coverage_percentage": 0,
            "rules": [],
            "positive_signals": [],
            "warning_signals": [],
        },
        "framework_checklist": {
            "coverage_percentage": 0,
            "confidence": "low",
            "items": [],
        },
        "data_quality": data_quality,
        "next_required_data": [],
        "decision": {
            "status": "unsupported",
            "action": "none",
            "label": "Motor ainda não disponível",
            "buy_hold_avoid_sell": None,
        },
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
            asset, search_result = build_eodhd_asset_snapshot(
                clean_identifier,
                exchange,
                ticker,
            )

            if not asset:
                selection_options = search_result.get(
                    "results",
                    [],
                )

                if selection_options:
                    return {
                        "error": (
                            "A pesquisa devolveu várias listagens. "
                            "Escolhe uma bolsa ou um ticker."
                        ),
                        "query": clean_identifier,
                        "requires_selection": True,
                        "selection_options": selection_options,
                        "source": search_result.get("source"),
                    }, 409

                return {
                    "error": "Ativo não encontrado.",
                    "query": clean_identifier,
                }, 404

        if asset.get("market_provider") == "EODHD":
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

    etf_profile = None

    if asset.get("asset_type") == "etf":
        try:
            etf_profile = get_official_etf_profile(asset)

            if etf_profile:
                sources["etf_structure"] = analysis_source(
                    etf_profile.get("issuer") or "Official issuer",
                    "ok",
                    source_url=etf_profile.get("source_url"),
                    coverage_percentage=(
                        etf_profile.get("coverage", {}).get(
                            "percentage"
                        )
                    ),
                )
            else:
                sources["etf_structure"] = analysis_source(
                    "Official issuer",
                    "unavailable",
                    reason=(
                        "Ainda não existe um adaptador oficial "
                        "para este ETF."
                    ),
                )

        except Exception as error:
            sources["etf_structure"] = analysis_source(
                "Official issuer",
                "unavailable",
                detail=str(error),
            )
            warnings.append(
                "O perfil estrutural oficial do ETF está "
                "temporariamente indisponível."
            )

    technical = None

    try:
        technical = get_technical_snapshot(asset)
        sources["technical"] = analysis_source(
            "Yahoo Finance via yfinance",
            "ok",
            symbol=technical.get("symbol"),
            as_of=technical.get("as_of"),
            coverage_percentage=technical.get(
                "coverage_percentage"
            ),
        )
    except Exception as error:
        sources["technical"] = analysis_source(
            "Yahoo Finance via yfinance",
            "unavailable",
            detail=str(error),
        )
        warnings.append(
            "A análise técnica está temporariamente indisponível."
        )

    evidence = None

    if asset.get("asset_type") == "stock":
        try:
            evidence = build_evidence_snapshot(asset, fundamentals)
            evidence_status = (
                "ok"
                if evidence.get("status") == "ok"
                else "partial"
                if evidence.get("status") == "partial"
                else "unavailable"
            )
            sources["evidence"] = analysis_source(
                "SEC EDGAR + Finnhub Company News",
                evidence_status,
                coverage_percentage=evidence.get(
                    "coverage_percentage"
                ),
                official_filings=evidence.get(
                    "official_filings_count"
                ),
                news_items=evidence.get("news_count"),
            )
        except Exception as error:
            sources["evidence"] = analysis_source(
                "Evidence & Events Engine",
                "unavailable",
                detail=str(error),
            )
            warnings.append(
                "Os eventos e notícias recentes estão "
                "temporariamente indisponíveis."
            )
    else:
        sources["evidence"] = analysis_source(
            "Evidence & Events Engine",
            "not_applicable",
            reason="A versão atual está otimizada para ações.",
        )

    valuation = None
    if asset.get("asset_type") == "stock":
        try:
            valuation = build_stock_valuation_snapshot(asset, fundamentals)
            valuation_status = (
                "ok"
                if valuation and valuation.get("score") is not None
                else "unavailable"
            )
            sources["valuation"] = analysis_source(
                "ThesisOS calculation from Finnhub + SEC EDGAR",
                valuation_status,
                coverage_percentage=(valuation or {}).get(
                    "coverage_percentage"
                ),
            )
        except Exception as error:
            sources["valuation"] = analysis_source(
                "ThesisOS Valuation Engine",
                "unavailable",
                detail=str(error),
            )
            warnings.append(
                "O valuation quantitativo está temporariamente indisponível."
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
        "technical",
    ]

    if asset.get("asset_type") == "stock":
        required_source_keys.extend(["fundamentals", "evidence"])

    if asset.get("asset_type") == "etf":
        required_source_keys.append("etf_structure")

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
        "etf_profile": etf_profile,
        "technical": technical,
        "valuation": valuation,
        "evidence": evidence,
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

    payload["framework_engine"] = build_framework_engine(
        asset=asset,
        fundamentals=fundamentals,
        etf_profile=etf_profile,
        sources=sources,
        data_quality=payload["data_quality"],
    )

    payload["framework_engine"] = enrich_framework_engine_with_technical(
        payload["framework_engine"],
        technical,
    )
    payload["framework_engine"] = enrich_framework_engine_with_valuation(
        payload["framework_engine"],
        valuation,
    )
    payload["framework_engine"] = enrich_framework_engine_with_evidence(
        payload["framework_engine"],
        evidence,
    )

    return payload, 200


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

def resolve_analysis_payload(
    identifier: str,
    base_currency: str = "EUR",
    exchange: str | None = None,
    ticker: str | None = None,
) -> tuple[dict, int]:
    payload, status = build_analysis_payload(
        identifier,
        base_currency=base_currency,
        exchange=exchange,
        ticker=ticker,
    )

    if status != 409 or not payload.get("requires_selection"):
        return payload, status

    options = payload.get("selection_options", [])

    if not options:
        return payload, status

    preferred_order = {
        "XETRA": 0,
        "US": 1,
        "NYSE": 2,
        "NASDAQ": 3,
        "LSE": 4,
    }

    selected = sorted(
        options,
        key=lambda item: (
            preferred_order.get(
                str(item.get("exchange_code") or "").upper(),
                99,
            ),
            0 if str(item.get("currency") or "").upper() == "EUR" else 1,
        ),
    )[0]

    return build_analysis_payload(
        identifier,
        base_currency=base_currency,
        exchange=selected.get("exchange_code"),
        ticker=selected.get("ticker"),
    )



def radar_rule_block(rules: list[dict], areas: set[str]) -> dict:
    selected = [
        rule for rule in rules
        if rule.get("area") in areas
    ]

    total_max = sum(
        finite_number(rule.get("max_points")) or 0
        for rule in selected
    )
    available = [
        rule for rule in selected
        if rule.get("status") != "unavailable"
    ]
    available_max = sum(
        finite_number(rule.get("max_points")) or 0
        for rule in available
    )
    achieved = sum(
        finite_number(rule.get("points")) or 0
        for rule in available
    )

    score = (
        round((achieved / total_max) * 100)
        if total_max
        else None
    )
    coverage = (
        round((available_max / total_max) * 100)
        if total_max
        else 0
    )

    return {
        "score": score,
        "coverage_percentage": coverage,
        "available_rules": len(available),
        "total_rules": len(selected),
    }



def radar_score_breakdown(payload: dict) -> dict:
    engine = payload.get("framework_engine", {})
    snapshot = engine.get("quantitative_snapshot", {})
    technical = payload.get("technical") or {}
    valuation = payload.get("valuation") or {}
    asset_type = payload.get("asset", {}).get("asset_type")

    technical_score = finite_number(technical.get("score"))
    valuation_score = finite_number(valuation.get("score"))
    data_score = finite_number(
        payload.get("data_quality", {}).get("completeness_percentage")
    )

    if asset_type == "stock":
        rules = snapshot.get("rules") or []

        operational = radar_rule_block(
            rules,
            {"growth", "profitability", "cash_flow_quality"},
        )
        financial = radar_rule_block(
            rules,
            {"balance_sheet", "dilution", "capital_allocation"},
        )

        components = {
            "operational_quality": {
                **operational,
                "weight": 0.35,
            },
            "financial_strength": {
                **financial,
                "weight": 0.25,
            },
            "valuation": {
                "score": valuation_score or 0,
                "weight": 0.20,
            },
            "technical_entry": {
                "score": technical_score or 0,
                "weight": 0.10,
            },
            "data_quality": {
                "score": data_score or 0,
                "weight": 0.10,
            },
        }

        score = round(sum(
            component["score"] * component["weight"]
            for component in components.values()
        ))

        return {
            "score": score,
            "model": "stock_framework_v2",
            "components": components,
        }

    quality_score = finite_number(snapshot.get("score"))
    weighted = []

    weights = {
        "structure": 0.70,
        "technical": 0.20,
        "data_quality": 0.10,
    }

    if quality_score is not None:
        weighted.append((quality_score, weights["structure"]))
    if technical_score is not None:
        weighted.append((technical_score, weights["technical"]))
    if data_score is not None:
        weighted.append((data_score, weights["data_quality"]))

    if not weighted:
        return {
            "score": None,
            "model": "asset_structure_v1",
            "components": {},
        }

    total_weight = sum(weight for _, weight in weighted)
    score = round(
        sum(value * weight for value, weight in weighted)
        / total_weight
    )

    return {
        "score": score,
        "model": "asset_structure_v1",
        "components": {
            "structure": {
                "score": quality_score,
                "weight": weights["structure"],
            },
            "technical_entry": {
                "score": technical_score,
                "weight": weights["technical"],
            },
            "data_quality": {
                "score": data_score,
                "weight": weights["data_quality"],
            },
        },
    }


def radar_composite_score(payload: dict):
    return radar_score_breakdown(payload).get("score")


def radar_status(score, payload):
    coverage = payload.get("data_quality", {}).get(
        "completeness_percentage", 0
    )
    evidence = payload.get("evidence") or {}

    if evidence.get("review_required"):
        return {
            "code": "event_review",
            "label": "Rever evento material",
            "action": "Ler documento/notícia antes de agir",
        }

    if score is None or coverage < 50:
        return {
            "code": "insufficient_data",
            "label": "Dados insuficientes",
            "action": "Completar fontes",
        }
    if score >= 82:
        return {
            "code": "candidate",
            "label": "Candidata forte",
            "action": "Análise aprofundada",
        }
    if score >= 68:
        return {
            "code": "monitor",
            "label": "Monitorizar",
            "action": "Rever valuation e entrada",
        }

    return {
        "code": "caution",
        "label": "Baixa prioridade",
        "action": "Aguardar melhoria",
    }


LOGOKIT_ISSUER_DOMAINS = (
    (("SPDR", "STATE STREET", "SSGA"), "statestreet.com"),
    (("ISHARES",), "ishares.com"),
    (("BLACKROCK",), "blackrock.com"),
    (("VANGUARD",), "vanguard.com"),
    (("AMUNDI",), "amundi.com"),
    (("INVESCO",), "invesco.com"),
    (("XTRACKERS", "DWS"), "dws.com"),
    (("VANECK",), "vaneck.com"),
    (("WISDOMTREE",), "wisdomtree.com"),
    (("GLOBAL X",), "globalxetfs.com"),
)


def logokit_issuer_domain(name: str | None) -> str | None:
    normalized = re.sub(
        r"[^A-Z0-9]+",
        " ",
        str(name or "").upper(),
    ).strip()

    for aliases, domain in LOGOKIT_ISSUER_DOMAINS:
        if any(alias in normalized for alias in aliases):
            return domain

    return None


def fetch_logokit_logo(
    symbol: str,
    name: str | None = None,
    asset_type: str | None = None,
) -> tuple[bytes, str]:
    safe_symbol = str(symbol or "").strip().upper()

    if not re.fullmatch(r"[A-Z0-9.\-]{1,20}", safe_symbol):
        raise ValueError("Símbolo inválido.")

    normalized_asset_type = str(
        asset_type or ""
    ).strip().upper()

    cache_key = "|".join([
        safe_symbol,
        str(name or "").strip().upper(),
        normalized_asset_type,
    ])

    cached = LOGO_CACHE.get(cache_key)
    if cached:
        age = time.time() - cached["created_at"]
        if age < LOGO_CACHE_TTL_SECONDS:
            return cached["content"], cached["content_type"]

    token = os.getenv("LOGOKIT_PUBLISHABLE_TOKEN", "").strip()
    if not token or not token.startswith("pk_"):
        raise RuntimeError("LogoKit não está configurado.")

    lookups = [("ticker", safe_symbol)]

    issuer_domain = (
        logokit_issuer_domain(name)
        if normalized_asset_type in {"ETF", "FUND", "MUTUALFUND"}
        else None
    )
    if issuer_domain:
        lookups.append(("domain", issuer_domain))

    query = urlencode({
        "token": token,
        "size": 64,
        "fallback": "404",
    })

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/149 Safari/537.36"
        ),
        "Accept": "image/avif,image/webp,image/png,image/*,*/*;q=0.8",
        "Referer": (
            "https://thesisosreplitstarter-1-zip--tmgms14.replit.app/"
        ),
    }

    attempted = set()

    for lookup_type, value in lookups:
        lookup_key = (lookup_type, value)

        if lookup_key in attempted:
            continue

        attempted.add(lookup_key)

        if lookup_type == "ticker":
            endpoint = f"ticker/{quote(value)}"
        else:
            endpoint = quote(value, safe=".")

        request = Request(
            f"https://img.logokit.com/{endpoint}?{query}",
            headers=headers,
        )

        try:
            with urlopen(request, timeout=20) as response:
                content = response.read()
                content_type = (
                    response.headers.get("Content-Type")
                    or "image/png"
                ).split(";")[0].strip()

                if not content or not content_type.startswith("image/"):
                    continue

                LOGO_CACHE[cache_key] = {
                    "created_at": time.time(),
                    "content": content,
                    "content_type": content_type,
                    "lookup_type": lookup_type,
                    "lookup_value": value,
                }

                return content, content_type

        except HTTPError as error:
            if error.code in {403, 404, 429}:
                continue
            continue
        except URLError:
            continue

    raise FileNotFoundError("Logótipo indisponível.")


def radar_result_from_payload(payload: dict) -> dict:
    asset = payload.get("asset", {})
    engine = payload.get("framework_engine", {})
    snapshot = engine.get("quantitative_snapshot", {})
    technical = payload.get("technical") or {}
    valuation = payload.get("valuation") or {}
    valuation_metrics = valuation.get("metrics", {})
    evidence = payload.get("evidence") or {}
    etf_profile = payload.get("etf_profile") or {}
    indicators = technical.get("indicators", {})
    score_breakdown = radar_score_breakdown(payload)
    score = score_breakdown.get("score")
    status = radar_status(score, payload)

    positives = []
    warnings = []

    for source in (
        snapshot.get("positive_signals", []),
        valuation.get("positive_signals", []),
        technical.get("positive_signals", []),
    ):
        for item in source:
            label = item.get("label")
            if label and label not in positives:
                positives.append(label)

    for source in (
        snapshot.get("warning_signals", []),
        valuation.get("warning_signals", []),
        technical.get("warning_signals", []),
    ):
        for item in source:
            label = item.get("label")
            if label and label not in warnings:
                warnings.append(label)

    return {
        "symbol": asset.get("symbol"),
        "name": asset.get("name"),
        "asset_type": asset.get("asset_type"),
        "exchange": asset.get("exchange"),
        "currency": asset.get("currency"),
        "country": asset.get("country"),
        "sector": asset.get("sector"),
        "industry": asset.get("industry"),
        "logo": asset.get("logo"),
        "website": asset.get("website"),
        "etf_largest_sector": etf_profile.get("largest_sector"),
        "etf_sector_allocation": etf_profile.get("sector_allocation", []),
        "etf_market_allocation": etf_profile.get("market_allocation", []),
        "etf_top_holdings": etf_profile.get("top_holdings", []),
        "etf_concentration": etf_profile.get("concentration", {}),
        "price": asset.get("price"),
        "market_change_percentage": asset.get("change_percentage"),
        "radar_score": score,
        "score_model": score_breakdown.get("model"),
        "score_components": score_breakdown.get("components", {}),
        "status": status,
        "quality_score": snapshot.get("score"),
        "quality_classification": snapshot.get("classification"),
        "valuation_score": valuation.get("score"),
        "valuation_classification": valuation.get("classification"),
        "evidence_status": evidence.get("status"),
        "material_events_count": evidence.get("material_events_count"),
        "event_review_required": evidence.get("review_required"),
        "latest_event": evidence.get("latest_event"),
        "free_cash_flow_yield_percentage": valuation_metrics.get(
            "free_cash_flow_yield_percentage"
        ),
        "price_to_earnings": valuation_metrics.get("price_to_earnings"),
        "reverse_dcf_implied_growth_percentage": valuation_metrics.get(
            "reverse_dcf_implied_growth_percentage"
        ),
        "technical_score": technical.get("score"),
        "technical_classification": technical.get("classification"),
        "framework_coverage_percentage": engine.get(
            "framework_checklist", {}
        ).get("coverage_percentage"),
        "data_completeness_percentage": payload.get(
            "data_quality", {}
        ).get("completeness_percentage"),
        "pullback_percentage": indicators.get(
            "pullback_from_52w_high_percentage"
        ),
        "rsi_14": indicators.get("rsi_14"),
        "return_6m_percentage": indicators.get(
            "return_6m_percentage"
        ),
        "volatility_percentage": indicators.get(
            "annualized_volatility_percentage"
        ),
        "positive_reasons": positives[:4],
        "warning_reasons": warnings[:4],
        "decision_label": engine.get("decision", {}).get("label"),
        "analysis_query": payload.get("query"),
        "analysis_exchange": asset.get("exchange_code"),
        "analysis_ticker": asset.get("symbol"),
    }


def radar_discovery_number(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    return number if math.isfinite(number) else None


def build_radar_discovery_sources(yf) -> dict:
    query = yf.EquityQuery

    def common_equity_filters(
        minimum_market_cap: int = 5_000_000_000,
    ) -> list:
        return [
            query("eq", ["region", "us"]),
            query(
                "gt",
                ["intradaymarketcap", minimum_market_cap],
            ),
            query("gt", ["avgdailyvol3m", 500_000]),
            query("gt", ["eodprice", 1]),
        ]

    non_financial_sectors = [
        "Basic Materials",
        "Communication Services",
        "Consumer Cyclical",
        "Consumer Defensive",
        "Energy",
        "Healthcare",
        "Industrials",
        "Technology",
        "Utilities",
    ]

    quality_nonfinancial = query(
        "and",
        common_equity_filters() + [
            query(
                "is-in",
                ["sector", *non_financial_sectors],
            ),
            query(
                "btwn",
                [
                    "returnonequity.lasttwelvemonths",
                    12,
                    60,
                ],
            ),
            query(
                "btwn",
                [
                    "ebitdamargin.lasttwelvemonths",
                    12,
                    60,
                ],
            ),
            query(
                "gt",
                [
                    "leveredfreecashflow.lasttwelvemonths",
                    0,
                ],
            ),
            query(
                "lt",
                ["netdebtebitda.lasttwelvemonths", 3],
            ),
        ],
    )

    quality_financial = query(
        "and",
        common_equity_filters() + [
            query(
                "eq",
                ["sector", "Financial Services"],
            ),
            query(
                "btwn",
                [
                    "returnonequity.lasttwelvemonths",
                    10,
                    35,
                ],
            ),
            query(
                "gt",
                ["netincomemargin.lasttwelvemonths", 8],
            ),
            query(
                "btwn",
                ["peratio.lasttwelvemonths", 5, 30],
            ),
        ],
    )

    profitable_growth = query(
        "and",
        common_equity_filters(2_000_000_000) + [
            query(
                "btwn",
                [
                    "totalrevenues1yrgrowth.lasttwelvemonths",
                    8,
                    60,
                ],
            ),
            query(
                "btwn",
                [
                    "epsgrowth.lasttwelvemonths",
                    8,
                    100,
                ],
            ),
            query(
                "gt",
                ["ebitdamargin.lasttwelvemonths", 8],
            ),
            query(
                "gt",
                [
                    "leveredfreecashflow.lasttwelvemonths",
                    0,
                ],
            ),
        ],
    )

    quality_growth = query(
        "and",
        common_equity_filters(2_000_000_000) + [
            query(
                "is-in",
                ["sector", *non_financial_sectors],
            ),
            query(
                "btwn",
                [
                    "totalrevenues1yrgrowth.lasttwelvemonths",
                    5,
                    40,
                ],
            ),
            query(
                "btwn",
                [
                    "epsgrowth.lasttwelvemonths",
                    5,
                    80,
                ],
            ),
            query(
                "btwn",
                [
                    "returnonequity.lasttwelvemonths",
                    12,
                    60,
                ],
            ),
            query(
                "gt",
                [
                    "leveredfreecashflow.lasttwelvemonths",
                    0,
                ],
            ),
            query(
                "lt",
                ["netdebtebitda.lasttwelvemonths", 3.5],
            ),
        ],
    )

    technology_growth = query(
        "and",
        common_equity_filters(2_000_000_000) + [
            query("eq", ["sector", "Technology"]),
            query(
                "btwn",
                [
                    "totalrevenues1yrgrowth.lasttwelvemonths",
                    8,
                    60,
                ],
            ),
            query(
                "btwn",
                [
                    "epsgrowth.lasttwelvemonths",
                    8,
                    120,
                ],
            ),
            query(
                "gt",
                ["ebitdamargin.lasttwelvemonths", 10],
            ),
            query(
                "gt",
                [
                    "leveredfreecashflow.lasttwelvemonths",
                    0,
                ],
            ),
        ],
    )

    value_quality = query(
        "and",
        common_equity_filters() + [
            query(
                "is-in",
                ["sector", *non_financial_sectors],
            ),
            query(
                "btwn",
                [
                    "returnonequity.lasttwelvemonths",
                    10,
                    60,
                ],
            ),
            query(
                "gt",
                [
                    "leveredfreecashflow.lasttwelvemonths",
                    0,
                ],
            ),
            query(
                "btwn",
                ["peratio.lasttwelvemonths", 5, 30],
            ),
            query(
                "lt",
                ["netdebtebitda.lasttwelvemonths", 3.5],
            ),
        ],
    )

    return {
        "quality_nonfinancial": {
            "query": quality_nonfinancial,
            "count": 30,
            "sort_field": "intradaymarketcap",
            "sort_asc": False,
        },
        "quality_financial": {
            "query": quality_financial,
            "count": 30,
            "sort_field": "intradaymarketcap",
            "sort_asc": False,
        },
        "profitable_growth": {
            "query": profitable_growth,
            "count": 30,
            "sort_field": (
                "totalrevenues1yrgrowth.lasttwelvemonths"
            ),
            "sort_asc": False,
        },
        "quality_growth": {
            "query": quality_growth,
            "count": 30,
            "sort_field": "intradaymarketcap",
            "sort_asc": False,
        },
        "technology_growth": {
            "query": technology_growth,
            "count": 30,
            "sort_field": (
                "totalrevenues1yrgrowth.lasttwelvemonths"
            ),
            "sort_asc": False,
        },
        "value_quality": {
            "query": value_quality,
            "count": 30,
            "sort_field": "peratio.lasttwelvemonths",
            "sort_asc": True,
        },
        "top_etfs_us": {
            "predefined": "top_etfs_us",
            "count": 30,
        },
        "top_performing_etfs": {
            "predefined": "top_performing_etfs",
            "count": 30,
        },
    }


def radar_discovery_score(
    quote_data: dict,
    source_count: int,
    asset_type: str,
) -> float:
    price = radar_discovery_number(
        quote_data.get("regularMarketPrice")
    )
    volume = radar_discovery_number(
        quote_data.get("averageDailyVolume3Month")
    ) or 0
    if asset_type == "ETF":
        source_score = min(10.0, source_count * 5.0)
    else:
        source_score = min(
            25.0,
            8.0 + max(0, source_count - 1) * 8.0,
        )

    if asset_type == "ETF":
        net_assets = radar_discovery_number(
            quote_data.get("netAssets")
        ) or 0
        expense_ratio = radar_discovery_number(
            quote_data.get("netExpenseRatio")
        )
        annual_return = radar_discovery_number(
            quote_data.get("annualReturnNavY3")
        )
        change_52w = radar_discovery_number(
            quote_data.get("fiftyTwoWeekChangePercent")
        )

        if net_assets >= 50_000_000_000:
            size_score = 30
        elif net_assets >= 10_000_000_000:
            size_score = 26
        elif net_assets >= 1_000_000_000:
            size_score = 21
        elif net_assets >= 100_000_000:
            size_score = 14
        else:
            size_score = 0

        if volume >= 5_000_000:
            liquidity_score = 25
        elif volume >= 1_000_000:
            liquidity_score = 21
        elif volume >= 250_000:
            liquidity_score = 17
        elif volume >= 25_000:
            liquidity_score = 10
        else:
            liquidity_score = 0

        if expense_ratio is None:
            cost_score = 0
        elif expense_ratio <= 0.10:
            cost_score = 20
        elif expense_ratio <= 0.25:
            cost_score = 16
        elif expense_ratio <= 0.50:
            cost_score = 10
        elif expense_ratio <= 0.80:
            cost_score = 5
        else:
            cost_score = 0

        performance_score = 0
        if annual_return is not None:
            performance_score += 8 if annual_return > 0 else 2
        if change_52w is not None:
            if change_52w > 0:
                performance_score += 7
            elif change_52w >= -15:
                performance_score += 3

        return round(min(
            100.0,
            size_score
            + liquidity_score
            + cost_score
            + performance_score
            + source_score,
        ), 2)

    market_cap = radar_discovery_number(
        quote_data.get("marketCap")
    ) or 0
    forward_pe = radar_discovery_number(
        quote_data.get("forwardPE")
    )
    trailing_pe = radar_discovery_number(
        quote_data.get("trailingPE")
    )
    price_to_book = radar_discovery_number(
        quote_data.get("priceToBook")
    )
    average_200d = radar_discovery_number(
        quote_data.get("twoHundredDayAverage")
    )
    change_52w = radar_discovery_number(
        quote_data.get("fiftyTwoWeekChangePercent")
    )

    if market_cap >= 200_000_000_000:
        size_score = 20
    elif market_cap >= 50_000_000_000:
        size_score = 18
    elif market_cap >= 10_000_000_000:
        size_score = 15
    elif market_cap >= 2_000_000_000:
        size_score = 10
    else:
        size_score = 0

    if volume >= 20_000_000:
        liquidity_score = 20
    elif volume >= 5_000_000:
        liquidity_score = 17
    elif volume >= 1_000_000:
        liquidity_score = 13
    elif volume >= 500_000:
        liquidity_score = 8
    else:
        liquidity_score = 0

    valuation_score = 0
    if forward_pe is not None and forward_pe > 0:
        if forward_pe <= 15:
            valuation_score += 10
        elif forward_pe <= 25:
            valuation_score += 7
        elif forward_pe <= 40:
            valuation_score += 3

    if trailing_pe is not None and trailing_pe > 0:
        if trailing_pe <= 15:
            valuation_score += 8
        elif trailing_pe <= 30:
            valuation_score += 6
        elif trailing_pe <= 45:
            valuation_score += 2

    if price_to_book is not None and price_to_book > 0:
        if price_to_book <= 3:
            valuation_score += 5
        elif price_to_book <= 8:
            valuation_score += 3

    valuation_score = min(20, valuation_score)

    technical_score = 0
    if price is not None and average_200d:
        technical_score += 8 if price >= average_200d else 3

    if change_52w is not None:
        if -30 <= change_52w <= 20:
            technical_score += 7
        elif 20 < change_52w <= 60:
            technical_score += 4
        elif change_52w < -30:
            technical_score += 2

    return round(min(
        100.0,
        size_score
        + liquidity_score
        + valuation_score
        + technical_score
        + source_score,
    ), 2)


def radar_select_discovery_candidates(
    universe: str,
    candidates: list[dict],
) -> tuple[list[dict], dict]:
    def sources(item):
        return set(item.get("sources") or [])

    def append_from(
        bucket: list[dict],
        selected: list[dict],
        selected_symbols: set[str],
        count: int = 1,
    ) -> int:
        appended = 0

        while bucket and appended < count:
            item = bucket.pop(0)
            symbol = item["symbol"]

            if symbol in selected_symbols:
                continue

            selected.append(item)
            selected_symbols.add(symbol)
            appended += 1

        return appended

    if universe == "discover_growth_us":
        strong_growth_sources = {
            "profitable_growth",
            "technology_growth",
        }
        selected = [
            item
            for item in candidates
            if sources(item) & strong_growth_sources
        ]

        return selected, {
            "strategy": "strong_growth_sources_required",
            "required_sources": sorted(strong_growth_sources),
            "excluded_quality_growth_only": True,
        }

    if universe == "discover_quality_us":
        financial_only = [
            item
            for item in candidates
            if sources(item) == {"quality_financial"}
        ]
        diversified_quality = [
            item
            for item in candidates
            if sources(item) != {"quality_financial"}
        ]

        selected = []
        selected_symbols = set()

        while diversified_quality or financial_only:
            before = len(selected)

            append_from(
                diversified_quality,
                selected,
                selected_symbols,
                count=3,
            )
            append_from(
                financial_only,
                selected,
                selected_symbols,
                count=1,
            )

            if len(selected) == before:
                break

        return selected, {
            "strategy": "quality_source_interleave",
            "sequence": [
                "3 diversified quality",
                "1 financial quality",
            ],
            "maximum_financial_only_share_in_normal_prefix": 0.25,
        }

    if universe == "discover_market_us":
        quality_bucket = [
            item
            for item in candidates
            if sources(item) & {
                "quality_nonfinancial",
                "quality_growth",
            }
        ]
        growth_bucket = [
            item
            for item in candidates
            if sources(item) & {
                "profitable_growth",
                "technology_growth",
            }
        ]
        value_bucket = [
            item
            for item in candidates
            if "value_quality" in sources(item)
        ]
        financial_bucket = [
            item
            for item in candidates
            if sources(item) == {"quality_financial"}
        ]

        selected = []
        selected_symbols = set()
        buckets = [
            quality_bucket,
            growth_bucket,
            value_bucket,
            financial_bucket,
        ]

        while any(buckets):
            before = len(selected)

            for bucket in buckets:
                append_from(
                    bucket,
                    selected,
                    selected_symbols,
                    count=1,
                )

            if len(selected) == before:
                break

        remaining = list(candidates)
        append_from(
            remaining,
            selected,
            selected_symbols,
            count=len(remaining),
        )

        return selected, {
            "strategy": "multi_factor_round_robin",
            "sequence": [
                "quality",
                "profitable growth",
                "value quality",
                "financial quality",
            ],
        }

    if universe == "discover_etf_us":
        excluded_terms = re.compile(
            r"\b("
            r"2X|3X|"
            r"ULTRAPRO|ULTRASHORT|ULTRA|"
            r"LEVERAGED|INVERSE|BEAR"
            r")\b",
            re.IGNORECASE,
        )

        selected = []
        exclusions = {
            "leveraged_or_inverse": 0,
            "insufficient_assets": 0,
            "insufficient_liquidity": 0,
        }

        for item in candidates:
            name = str(item.get("name") or "")
            net_assets = item.get("net_assets")
            average_volume = item.get("average_volume_3m") or 0

            if excluded_terms.search(name):
                exclusions["leveraged_or_inverse"] += 1
                continue

            if net_assets is not None and net_assets < 500_000_000:
                exclusions["insufficient_assets"] += 1
                continue

            if average_volume < 100_000:
                exclusions["insufficient_liquidity"] += 1
                continue

            selected.append(item)

        return selected, {
            "strategy": "liquid_unleveraged_etfs",
            "minimum_net_assets_usd_when_available": 500_000_000,
            "minimum_average_volume_3m": 100_000,
            "excluded_name_terms": [
                "2x",
                "3x",
                "ultra",
                "leveraged",
                "inverse",
                "bear",
            ],
            "exclusions": exclusions,
        }

    return list(candidates), {
        "strategy": "discovery_score",
    }


def discover_radar_symbols(
    universe: str,
    limit: int,
    force_refresh: bool = False,
) -> tuple[list[str], dict]:
    config = RADAR_DISCOVERY_UNIVERSES[universe]
    cached = RADAR_DISCOVERY_CACHE.get(universe)

    if cached and not force_refresh:
        age = time.time() - cached["created_at"]
        if age < RADAR_DISCOVERY_CACHE_TTL_SECONDS:
            cached_data = cached["data"]
            return (
                list(cached_data["symbols"][:limit]),
                {
                    **cached_data["metadata"],
                    "cached": True,
                    "shortlist_count": min(
                        limit,
                        len(cached_data["symbols"]),
                    ),
                },
            )

    try:
        import yfinance as yf
    except ImportError as error:
        raise RuntimeError(
            "A dependência yfinance não está instalada."
        ) from error

    expected_type = config["asset_type"]
    source_registry = build_radar_discovery_sources(yf)
    merged = {}
    source_errors = []

    for source_name in config["sources"]:
        source = source_registry[source_name]

        try:
            if source.get("query") is not None:
                response = yf.screen(
                    source["query"],
                    count=source.get("count", 30),
                    sortField=source.get("sort_field"),
                    sortAsc=source.get("sort_asc"),
                )
            else:
                response = yf.screen(
                    source["predefined"],
                    count=source.get("count", 30),
                )

            quotes = (
                response.get("quotes", [])
                if isinstance(response, dict)
                else []
            )
        except Exception as error:
            source_errors.append({
                "source": source_name,
                "screener": source.get(
                    "predefined",
                    "custom_equity_query",
                ),
                "error": str(error),
            })
            continue

        for quote_data in quotes:
            symbol = str(
                quote_data.get("symbol") or ""
            ).strip().upper()
            quote_type = str(
                quote_data.get("quoteType") or ""
            ).strip().upper()

            if not symbol or quote_type != expected_type:
                continue

            record = merged.setdefault(symbol, {
                "symbol": symbol,
                "quote": dict(quote_data),
                "sources": [],
            })

            if source_name not in record["sources"]:
                record["sources"].append(source_name)

            for key, value in quote_data.items():
                current_value = record["quote"].get(key)
                if current_value is None or current_value == "":
                    record["quote"][key] = value

    eligible = []

    for record in merged.values():
        quote_data = record["quote"]
        price = radar_discovery_number(
            quote_data.get("regularMarketPrice")
        )
        volume = radar_discovery_number(
            quote_data.get("averageDailyVolume3Month")
        ) or 0

        if price is None or price <= 1:
            continue

        if expected_type == "EQUITY":
            market_cap = radar_discovery_number(
                quote_data.get("marketCap")
            ) or 0

            if market_cap < 2_000_000_000:
                continue
            if volume < 500_000:
                continue
        else:
            net_assets = radar_discovery_number(
                quote_data.get("netAssets")
            )

            if net_assets is not None and net_assets < 100_000_000:
                continue
            if volume < 25_000:
                continue

        score = radar_discovery_score(
            quote_data,
            len(record["sources"]),
            expected_type,
        )

        eligible.append({
            "symbol": record["symbol"],
            "name": (
                quote_data.get("longName")
                or quote_data.get("shortName")
                or record["symbol"]
            ),
            "quote_type": expected_type,
            "exchange": quote_data.get("exchange"),
            "currency": quote_data.get("currency"),
            "market_cap": radar_discovery_number(
                quote_data.get("marketCap")
            ),
            "net_assets": radar_discovery_number(
                quote_data.get("netAssets")
            ),
            "average_volume_3m": volume,
            "price": price,
            "forward_pe": radar_discovery_number(
                quote_data.get("forwardPE")
            ),
            "trailing_pe": radar_discovery_number(
                quote_data.get("trailingPE")
            ),
            "expense_ratio": radar_discovery_number(
                quote_data.get("netExpenseRatio")
            ),
            "change_52w_percentage": radar_discovery_number(
                quote_data.get("fiftyTwoWeekChangePercent")
            ),
            "discovery_score": score,
            "sources": record["sources"],
            "screeners": record["sources"],
        })

    eligible.sort(
        key=lambda item: (
            item["discovery_score"],
            item.get("market_cap") or item.get("net_assets") or 0,
            item["average_volume_3m"],
        ),
        reverse=True,
    )

    if expected_type == "EQUITY":
        unique_companies = []
        company_keys = set()

        for item in eligible:
            company_key = re.sub(
                r"[^A-Z0-9]+",
                "",
                str(item.get("name") or item["symbol"]).upper(),
            )

            if company_key in company_keys:
                continue

            company_keys.add(company_key)
            unique_companies.append(item)

        eligible = unique_companies

    selected_candidates, selection_policy = (
        radar_select_discovery_candidates(
            universe,
            eligible,
        )
    )

    fallback_used = not selected_candidates
    if fallback_used:
        symbols = list(config["fallback"])
    else:
        symbols = [
            item["symbol"]
            for item in selected_candidates
        ]

    metadata = {
        "mode": "automatic_discovery",
        "label": config["label"],
        "asset_type": expected_type,
        "provider": "Yahoo Finance screener via yfinance",
        "sources": config["sources"],
        "screeners": config["sources"],
        "candidate_pool_count": len(merged),
        "eligible_count": len(eligible),
        "selection_pool_count": len(selected_candidates),
        "shortlist_count": min(limit, len(symbols)),
        "selection_policy": selection_policy,
        "fallback_used": fallback_used,
        "fallback_symbols": (
            list(config["fallback"])
            if fallback_used
            else []
        ),
        "source_errors": source_errors,
        "screener_errors": source_errors,
        "prefilter": {
            "equities": {
                "minimum_market_cap_usd": 2_000_000_000,
                "minimum_average_volume_3m": 500_000,
                "minimum_price_usd": 1,
            },
            "etfs": {
                "minimum_net_assets_usd_when_available": 100_000_000,
                "minimum_average_volume_3m": 25_000,
                "minimum_price_usd": 1,
            },
        },
        "candidates": selected_candidates[:25],
        "cached": False,
    }

    RADAR_DISCOVERY_CACHE[universe] = {
        "created_at": time.time(),
        "data": {
            "symbols": symbols,
            "metadata": metadata,
        },
    }

    return symbols[:limit], metadata


def parse_radar_symbols(
    universe: str,
    symbols_value: str | None,
    limit: int,
    force_refresh: bool = False,
) -> tuple[list[str], dict]:
    if symbols_value:
        candidates = re.split(r"[,;\s]+", symbols_value.upper())
        symbols = [
            symbol
            for symbol in candidates
            if re.fullmatch(r"[A-Z0-9.\-]{1,20}", symbol)
        ]
        metadata = {
            "mode": "custom",
            "candidate_pool_count": len(symbols),
            "eligible_count": len(symbols),
            "shortlist_count": min(limit, len(symbols)),
            "fallback_used": False,
            "cached": False,
        }
    elif universe in RADAR_DISCOVERY_UNIVERSES:
        return discover_radar_symbols(
            universe,
            limit,
            force_refresh=force_refresh,
        )
    else:
        symbols = list(
            RADAR_UNIVERSES.get(
                universe,
                RADAR_UNIVERSES["core_us"],
            )
        )
        metadata = {
            "mode": "static",
            "candidate_pool_count": len(symbols),
            "eligible_count": len(symbols),
            "shortlist_count": min(limit, len(symbols)),
            "fallback_used": False,
            "cached": False,
        }

    unique = []
    for symbol in symbols:
        if symbol not in unique:
            unique.append(symbol)

    return unique[:limit], metadata


def build_opportunity_radar(
    universe: str = "core_us",
    symbols_value: str | None = None,
    limit: int = 8,
    force_refresh: bool = False,
) -> dict:
    symbols, discovery = parse_radar_symbols(
        universe,
        symbols_value,
        limit,
        force_refresh=force_refresh,
    )
    cache_key = json.dumps(
        {"universe": universe, "symbols": symbols},
        sort_keys=True,
    )
    cached = RADAR_CACHE.get(cache_key)

    if cached and not force_refresh:
        age = time.time() - cached["created_at"]
        if age < RADAR_CACHE_TTL_SECONDS:
            return {**cached["data"], "cached": True}

    results = []
    errors = []

    def analyze(symbol):
        return symbol, resolve_analysis_payload(symbol)

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(symbols)))) as executor:
        futures = {
            executor.submit(analyze, symbol): symbol
            for symbol in symbols
        }

        for future in as_completed(futures):
            symbol = futures[future]
            try:
                _, (payload, status) = future.result()
                if status == 200:
                    results.append(radar_result_from_payload(payload))
                else:
                    errors.append({
                        "symbol": symbol,
                        "status": status,
                        "error": payload.get("error", "Análise indisponível."),
                    })
            except Exception as error:
                errors.append({
                    "symbol": symbol,
                    "status": 500,
                    "error": str(error),
                })

    results.sort(
        key=lambda item: (
            item.get("radar_score") is not None,
            item.get("radar_score") or -1,
            item.get("data_completeness_percentage") or -1,
        ),
        reverse=True,
    )

    data = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "universe": universe,
        "symbols_requested": symbols,
        "universe_mode": discovery.get("mode"),
        "discovery": discovery,
        "analysed_count": len(results),
        "error_count": len(errors),
        "results": results,
        "errors": errors,
        "cached": False,
        "methodology": {
            "stock_model": "stock_framework_v2",
            "stock_weights": {
                "operational_quality": 0.35,
                "financial_strength": 0.25,
                "valuation": 0.20,
                "technical_entry": 0.10,
                "data_quality": 0.10,
            },
            "etf_model": "asset_structure_v1",
            "etf_weights": {
                "structure": 0.70,
                "technical_entry": 0.20,
                "data_quality": 0.10,
            },
            "discovery_policy": (
                "Os universos automáticos usam screeners do Yahoo Finance, "
                "aplicam filtros mínimos de dimensão, liquidez e preço e só "
                "depois enviam a shortlist para o framework completo."
            ),
            "coverage_policy": (
                "Nas ações, regras quantitativas indisponíveis "
                "reduzem os sub-scores operacional e financeiro."
            ),
            "event_policy": (
                "Eventos materiais que exigem revisão bloqueiam "
                "a decisão operacional, independentemente do score."
            ),
            "decision_limit": (
                "O ranking identifica candidatas para análise aprofundada. "
                "Não substitui revisão qualitativa, notícias, encaixe na "
                "carteira, zona de entrada ou dimensionamento da posição."
            ),
        },
    }

    RADAR_CACHE[cache_key] = {
        "created_at": time.time(),
        "data": data,
    }
    return data


def build_comparison(
    left: str,
    right: str,
    left_exchange: str | None = None,
    right_exchange: str | None = None,
) -> tuple[dict, int]:
    left_payload, left_status = resolve_analysis_payload(
        left,
        exchange=left_exchange,
    )
    right_payload, right_status = resolve_analysis_payload(
        right,
        exchange=right_exchange,
    )

    if left_status != 200 or right_status != 200:
        return {
            "error": "Não foi possível analisar os dois ativos.",
            "left_status": left_status,
            "right_status": right_status,
            "left": left_payload,
            "right": right_payload,
        }, 400

    left_type = left_payload.get("asset", {}).get("asset_type")
    right_type = right_payload.get("asset", {}).get("asset_type")

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "same_asset_type": left_type == right_type,
        "warning": (
            None
            if left_type == right_type
            else (
                "Os ativos pertencem a categorias diferentes. "
                "As métricas devem ser interpretadas separadamente."
            )
        ),
        "left": left_payload,
        "right": right_payload,
    }, 200


class ThesisOSHandler(SimpleHTTPRequestHandler):
    def send_json(
        self,
        payload: dict,
        status: int = 200,
        extra_headers: dict | None = None,
    ) -> None:
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
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def read_json_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ValueError("Content-Length inválido.") from error
        if length <= 0:
            return {}
        if length > SYNC_MAX_REQUEST_BYTES:
            raise ValueError("Pedido demasiado grande.")
        raw = self.rfile.read(length)
        try:
            value = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("Corpo JSON inválido.") from error
        if not isinstance(value, dict):
            raise ValueError("O corpo do pedido deve ser um objeto JSON.")
        return value


    def supabase_auth_context(self):
        token = bearer_token_from_headers(self.headers)
        if not token:
            return None, None
        try:
            user = supabase_auth_user(token)
        except (PermissionError, RuntimeError):
            return token, None
        return token, user

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/api/auth/config":
            config = auth_configuration()
            self.send_json({
                "status": "ok",
                "configured": config["configured"],
                "url": config["url"] if config["configured"] else None,
                "publishable_key": (
                    config["publishable_key"]
                    if config["configured"]
                    else None
                ),
                "provider": config["provider"],
                "table": config["table"],
                "missing": config["missing"],
                "allowed_namespaces": sorted(SYNC_ALLOWED_NAMESPACES),
            })
            return

        if parsed_url.path == "/api/auth/session":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "status": "ok",
                    "authenticated": False,
                    "user": None,
                    "row_count": 0,
                })
                return
            try:
                cloud = fetch_user_cloud_state(user.get("id"), access_token)
                self.send_json({
                    "status": "ok",
                    "authenticated": True,
                    "user": {
                        "id": user.get("id"),
                        "email": user.get("email"),
                        "email_confirmed_at": user.get("email_confirmed_at"),
                        "last_sign_in_at": user.get("last_sign_in_at"),
                    },
                    "row_count": cloud["row_count"],
                    "cloud_metadata": cloud["metadata"],
                })
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível validar a sessão Supabase.",
                    "detail": str(error),
                }, status=502)
            return

        if parsed_url.path == "/api/user/state":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "error": "Inicia sessão com uma conta Supabase."
                }, status=401)
                return
            try:
                payload = fetch_user_cloud_state(user.get("id"), access_token)
                self.send_json({
                    "status": "ok",
                    "user_id": user.get("id"),
                    **payload,
                })
            except PermissionError as error:
                self.send_json({"error": str(error)}, status=401)
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível ler o estado da conta.",
                    "detail": str(error),
                }, status=502)
            return


        if parsed_url.path == "/api/logo":
            query = parse_qs(parsed_url.query)
            symbol = str(
                (query.get("symbol") or [""])[0]
            ).strip().upper()

            name = str(
                (query.get("name") or [""])[0]
            ).strip()
            asset_type = str(
                (query.get("asset_type") or [""])[0]
            ).strip().upper()
            try:
                content, content_type = fetch_logokit_logo(
                    symbol,
                    name=name,
                    asset_type=asset_type,
                )
            except (ValueError, FileNotFoundError, RuntimeError):
                self.send_response(404)
                self.send_header("Cache-Control", "public, max-age=300")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return

            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header(
                "Cache-Control",
                "public, max-age=86400, immutable",
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if parsed_url.path == "/api/health":
            self.send_json(
                {
                    "status": "ok",
                    "service": f"{PRODUCT_NAME} API",
                    "version": PRODUCT_VERSION,
                    "release_stage": RELEASE_STAGE,
                    "framework_engine_version": FRAMEWORK_ENGINE_VERSION,
                    "providers": [
                        "Finnhub",
                        "OpenFIGI",
                        "EODHD",
                        "SEC EDGAR",
                        "ECB Data Portal",
                        "Yahoo Finance via yfinance",
                        "LogoKit stock and ETF logos",
                        "SEC submissions and Finnhub Company News",
                    ],
                }
            )
            return

        if parsed_url.path.startswith("/api/analysis/"):
            self.handle_analysis_request(parsed_url)
            return

        if parsed_url.path == "/api/radar":
            self.handle_radar_request(parsed_url)
            return

        if parsed_url.path == "/api/compare":
            self.handle_compare_request(parsed_url)
            return

        if parsed_url.path.startswith("/api/technical/"):
            self.handle_technical_request(parsed_url)
            return

        if parsed_url.path.startswith("/api/valuation/"):
            self.handle_valuation_request(parsed_url)
            return

        if parsed_url.path.startswith("/api/evidence/"):
            self.handle_evidence_request(parsed_url)
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


    def do_POST(self) -> None:
        parsed_url = urlparse(self.path)

        if parsed_url.path == "/api/user/state":
            access_token, user = self.supabase_auth_context()
            if not access_token or not user:
                self.send_json({
                    "error": "Inicia sessão com uma conta Supabase."
                }, status=401)
                return
            try:
                body = self.read_json_body()
                result = upsert_user_cloud_state(
                    user.get("id"),
                    access_token,
                    body.get("state"),
                )
                self.send_json({
                    "status": "ok",
                    "user_id": user.get("id"),
                    **result,
                })
            except PermissionError as error:
                self.send_json({"error": str(error)}, status=401)
            except ValueError as error:
                self.send_json({"error": str(error)}, status=400)
            except Exception as error:
                self.send_json({
                    "error": "Não foi possível guardar o estado da conta.",
                    "detail": str(error),
                }, status=502)
            return


        self.send_json({"error": "Endpoint POST não encontrado."}, status=404)

    def handle_radar_request(self, parsed_url) -> None:
        query = parse_qs(parsed_url.query)
        universe = query.get("universe", ["core_us"])[0]
        symbols_value = query.get("symbols", [None])[0]
        refresh_value = str(query.get("refresh", ["0"])[0]).strip().lower()
        force_refresh = refresh_value not in {"", "0", "false", "no", "off"}

        try:
            limit = int(query.get("limit", ["8"])[0])
        except ValueError:
            limit = 8

        limit = max(1, min(limit, 15))

        try:
            payload = build_opportunity_radar(
                universe=universe,
                symbols_value=symbols_value,
                limit=limit,
                force_refresh=force_refresh,
            )
            self.send_json(payload)
        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao executar o Opportunity Radar.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_compare_request(self, parsed_url) -> None:
        query = parse_qs(parsed_url.query)
        left = query.get("left", [""])[0].strip().upper()
        right = query.get("right", [""])[0].strip().upper()

        if not left or not right:
            self.send_json(
                {"error": "Indica os dois ativos a comparar."},
                status=400,
            )
            return

        try:
            payload, status = build_comparison(
                left,
                right,
                left_exchange=query.get("left_exchange", [None])[0],
                right_exchange=query.get("right_exchange", [None])[0],
            )
            self.send_json(payload, status=status)
        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao comparar os ativos.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_technical_request(self, parsed_url) -> None:
        identifier = unquote(
            parsed_url.path.removeprefix("/api/technical/")
        ).strip().upper()

        if not identifier:
            self.send_json({"error": "Identificador inválido."}, status=400)
            return

        try:
            payload, status = resolve_analysis_payload(identifier)
            if status != 200:
                self.send_json(payload, status=status)
                return
            self.send_json(payload.get("technical") or {})
        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao calcular análise técnica.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_valuation_request(self, parsed_url) -> None:
        identifier = unquote(
            parsed_url.path.removeprefix("/api/valuation/")
        ).strip().upper()

        if not identifier:
            self.send_json({"error": "Identificador inválido."}, status=400)
            return

        try:
            payload, status = resolve_analysis_payload(identifier)
            if status != 200:
                self.send_json(payload, status=status)
                return
            self.send_json(payload.get("valuation") or {})
        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao calcular valuation.",
                    "detail": str(error),
                },
                status=500,
            )

    def handle_evidence_request(self, parsed_url) -> None:
        identifier = unquote(
            parsed_url.path.removeprefix("/api/evidence/")
        ).strip().upper()

        if not identifier:
            self.send_json({"error": "Identificador inválido."}, status=400)
            return

        query = parse_qs(parsed_url.query)

        try:
            payload, status = resolve_analysis_payload(
                identifier,
                exchange=query.get("exchange", [None])[0],
                ticker=query.get("ticker", [None])[0],
            )
            if status != 200:
                self.send_json(payload, status=status)
                return
            self.send_json(payload.get("evidence") or {})
        except Exception as error:
            self.send_json(
                {
                    "error": "Erro ao recolher evidência e eventos.",
                    "detail": str(error),
                },
                status=500,
            )

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


def run_server():
    server = ThreadingHTTPServer(
        (HOST, PORT),
        ThesisOSHandler,
    )

    print(f"ThesisOS disponível na porta {PORT}")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServidor encerrado.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_server()
