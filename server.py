import csv
import gzip
import io
import json
import os
import re
import time
import zlib
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

OPENFIGI_CACHE_TTL_SECONDS = 24 * 60 * 60
EODHD_CACHE_TTL_SECONDS = 24 * 60 * 60
SEC_CACHE_TTL_SECONDS = 24 * 60 * 60
ECB_CACHE_TTL_SECONDS = 12 * 60 * 60
ETF_PROFILE_CACHE_TTL_SECONDS = 24 * 60 * 60
OPENFIGI_CACHE = {}
EODHD_CACHE = {}
SEC_CACHE = {}
ECB_CACHE = {}
ETF_PROFILE_CACHE = {}

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


def build_vanguard_etf_profile(
    asset: dict,
    source_url: str,
) -> dict:
    page_html = fetch_official_html(source_url)
    parser = OfficialEtfPageParser()
    parser.feed(page_html)
    tokens = parser.tokens

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
        "ocf_ter": find_official_value(
            tokens,
            ["OCF/TER", "OCF", "TER"],
        ),
        "number_of_stocks": find_official_value(
            tokens,
            ["Number of stocks"],
        ),
        "distribution_policy": (
            "Accumulating"
            if "ACCUMULATING" in str(
                asset.get("name") or ""
            ).upper()
            else None
        ),
        "factsheet_url": find_official_document_link(
            parser.links,
            source_url,
            "Factsheet",
        ),
        "source_url": source_url,
        "source": "Vanguard official product page",
    }

    core_fields = [
        "share_class_inception",
        "listing_date",
        "investment_structure",
        "share_class_assets",
        "total_assets",
        "investment_method",
        "benchmark",
        "domicile",
    ]

    available = sum(
        1
        for field in core_fields
        if profile.get(field) not in (None, "", "—")
    )

    profile["coverage"] = {
        "available_core_fields": available,
        "required_core_fields": len(core_fields),
        "percentage": round(
            available / len(core_fields) * 100,
            2,
        ),
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



FRAMEWORK_ENGINE_VERSION = "0.3"


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
        "número de posições"
        for key in ["number_of_stocks"]
        if etf_profile.get(key)
    ]

    holdings_missing = [
        "top holdings",
        "setores",
        "geografias",
        "moedas",
        "concentração",
        "overlap",
    ]

    implementation_available = [
        label
        for key, label in [
            ("ocf_ter", "TER/OCF"),
            ("share_class_assets", "ativos da classe"),
            ("total_assets", "ativos totais"),
            ("investment_method", "replicação"),
            ("domicile", "domicílio"),
            ("distribution_policy", "política de distribuição"),
            ("tax_status", "estatuto fiscal"),
        ]
        if etf_profile.get(key)
    ]

    implementation_missing = [
        label
        for key, label in [
            ("ocf_ter", "TER/OCF"),
            ("share_class_assets", "ativos da classe"),
            ("total_assets", "ativos totais"),
            ("investment_method", "replicação"),
            ("domicile", "domicílio"),
            ("distribution_policy", "política de distribuição"),
            ("tax_status", "estatuto fiscal"),
        ]
        if not etf_profile.get(key)
    ] + [
        "tracking difference",
        "tracking error",
        "spread",
        "liquidez",
    ]

    profile_available = bool(etf_profile)

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
                "KID/KIID",
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
            "missing",
            [],
            [
                "P/E agregado",
                "crescimento dos lucros",
                "valuation histórico",
                "comparação com benchmark",
            ],
        ),
        framework_item(
            "technical",
            "Pullback e momento de entrada",
            "missing",
            [],
            [
                "pullback",
                "médias móveis",
                "suportes",
                "RSI",
                "força relativa",
            ],
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
                "Identificação, preço, moeda e parte dos dados "
                "estruturais oficiais do ETF já estão ligados. "
                "Holdings detalhadas, tracking, valuation e carteira "
                "continuam incompletos."
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
                    "TER, holdings, metodologia, overlap, valuation "
                    "agregado e carteira ainda ausentes."
                ),
            },
        ],
        "quantitative_snapshot": {
            "score": None,
            "classification": {
                "code": "insufficient_data",
                "label": "Dados insuficientes",
            },
            "coverage_percentage": (
                etf_profile.get("coverage", {}).get(
                    "percentage",
                    0,
                )
                if profile_available
                else 0
            ),
            "rules": [],
            "positive_signals": [],
            "warning_signals": [],
            "methodology_note": (
                "O ThesisOS não atribui um score empresarial a ETFs."
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
                    if not etf_profile.get("ocf_ter")
                    else None
                ),
                "tracking difference e tracking error",
                "liquidez e spread",
                "top holdings, setores, geografias e moedas",
                "overlap com a carteira",
                "valuation agregado",
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
                "Aguardar holdings, tracking, valuation e carteira"
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
                "Após integração de holdings e tracking"
                if profile_available
                else "Após integração do factsheet oficial"
            ),
            "reason": (
                (
                    "Os dados oficiais melhoram a análise estrutural, "
                    "mas ainda não permitem avaliar concentração, "
                    "tracking, valuation e encaixe na carteira."
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
