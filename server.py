import json
import os
import re
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlencode, urlparse
from urllib.request import Request, urlopen


HOST = "0.0.0.0"
PORT = 3000
FINNHUB_BASE_URL = "https://finnhub.io/api/v1"


def fetch_finnhub(endpoint: str, params: dict) -> dict:
    """Consulta a Finnhub sem revelar a API key ao frontend."""
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
            "User-Agent": "ThesisOS/0.2",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=15) as response:
        data = json.loads(response.read().decode("utf-8"))

    if isinstance(data, dict) and data.get("error"):
        raise RuntimeError(data["error"])

    return data


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
                    "provider": "Finnhub",
                }
            )
            return

        if parsed_url.path.startswith("/api/asset/"):
            self.handle_asset_request(parsed_url.path)
            return

        # Continua a servir o index.html e os restantes ficheiros.
        super().do_GET()

    def handle_asset_request(self, path: str) -> None:
        symbol = unquote(path.removeprefix("/api/asset/")).strip().upper()

        # Permite tickers como MCD, BRK.B ou RACE.MI.
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

            has_profile = bool(profile.get("name"))
            has_quote = bool(quote_data.get("c"))

            if not has_profile and not has_quote:
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

            asset = {
                "symbol": profile.get("ticker") or symbol,
                "name": profile.get("name") or symbol,
                "asset_type": "stock",
                "price": quote_data.get("c"),
                "change": quote_data.get("d"),
                "change_percentage": quote_data.get("dp"),
                "open": quote_data.get("o"),
                "high": quote_data.get("h"),
                "low": quote_data.get("l"),
                "previous_close": quote_data.get("pc"),
                "currency": profile.get("currency"),
                "exchange": profile.get("exchange"),
                "country": profile.get("country"),
                "sector": None,
                "industry": profile.get("finnhubIndustry"),
                "ipo_date": profile.get("ipo"),
                "market_cap_millions": profile.get("marketCapitalization"),
                "shares_outstanding_millions": profile.get("shareOutstanding"),
                "website": profile.get("weburl"),
                "logo": profile.get("logo"),
                "updated_at": updated_at,
                "source": "Finnhub",
            }

            self.send_json(asset)

        except HTTPError as error:
            self.send_json(
                {
                    "error": "A Finnhub recusou o pedido.",
                    "status": error.code,
                    "detail": ("Verifica a API key ou os limites da conta Finnhub."),
                },
                status=502,
            )

        except URLError:
            self.send_json(
                {
                    "error": "Não foi possível contactar a Finnhub.",
                    "detail": "Verifica a ligação e tenta novamente.",
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
