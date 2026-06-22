import json
import os
import re
import time
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen


HOST = "0.0.0.0"
PORT = 3000

FINNHUB_BASE_URL = "https://finnhub.io/api/v1"
OPENFIGI_MAPPING_URL = "https://api.openfigi.com/v3/mapping"

OPENFIGI_CACHE_TTL_SECONDS = 24 * 60 * 60
OPENFIGI_CACHE = {}


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
                    ],
                }
            )
            return

        if parsed_url.path.startswith("/api/search/"):
            self.handle_search_request(parsed_url.path)
            return

        if parsed_url.path.startswith("/api/asset/"):
            self.handle_asset_request(parsed_url.path)
            return

        super().do_GET()

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
            result = search_openfigi(query)

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
