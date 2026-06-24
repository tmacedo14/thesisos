# ThesisOS — Investment Intelligence

ThesisOS is a functional academic prototype for evidence-based investment analysis. It identifies stocks and ETFs, gathers market and official data, applies transparent rules based on three investment frameworks, and clearly separates facts, calculations, interpretation, and missing information.

## Main capabilities

- Search by ticker or ISIN, with listing selection for European instruments.
- Stock analysis using Finnhub, OpenFIGI, SEC EDGAR and ECB data.
- ETF analysis using EODHD listings plus official issuer documentation.
- Fundamental/structural Framework Engine with explicit coverage and limitations.
- Cash-flow, debt, dilution and capital-allocation analysis for US stocks.
- ETF costs, tracking error, holdings, countries, sectors and aggregate valuation.
- Technical Engine using Yahoo Finance history through `yfinance`: moving averages, RSI, returns, pullback, volatility and drawdown.
- Automated Opportunity Radar for configurable universes and the browser watchlist.
- Persistent local watchlist.
- Stock-to-stock and ETF-to-ETF comparison.
- Printable analysis that can be saved as PDF from the browser.

## Data sources

- **Finnhub:** US quotes and company profiles.
- **OpenFIGI:** instrument identification and security type.
- **EODHD:** European ticker/ISIN listing discovery and previous close.
- **SEC EDGAR Company Facts:** official US financial statements.
- **ECB Data Portal:** official EUR reference exchange rates.
- **Yahoo Finance via yfinance:** historical daily price and volume data.
- **Official ETF issuers:** product pages, factsheets and KIID documents. The current official adapter covers Vanguard FTSE All-World UCITS ETF (VWCE/VWRP/VWRA share class).

## Opportunity Radar methodology

The Radar is a screening funnel, not an order generator. Its current composite score uses:

- 70% fundamental or ETF structural score;
- 20% Technical Engine score;
- 10% data completeness.

A high result means **candidate for deeper analysis**. It does not mean “buy”. Final decisions remain blocked until valuation, current news, portfolio fit, position size and entry plan are sufficiently assessed.

## Run locally / Replit

1. Install dependencies:

```bash
pip install -r requirements.txt
```

2. Configure Replit Secrets / environment variables:

```text
FINNHUB_API_KEY
OPENFIGI_API_KEY
EODHD_API_TOKEN
SEC_USER_AGENT
```

`SEC_USER_AGENT` should identify the application and include a contact email, following SEC access guidance.

3. Start the server:

```bash
python3 server.py
```

4. Open port `3000`.

## Useful demo flow

1. Search `AAPL` to demonstrate stock fundamentals, cash flow, debt and technical analysis.
2. Search `VWCE`, select XETRA, and demonstrate official ETF structure, costs, tracking and holdings.
3. Open **Opportunity Radar** and execute the Core US universe.
4. Add an asset to **Watchlist**.
5. Compare `MSFT` with `AAPL`.
6. In an analysis, choose **Imprimir / PDF**.

## Architecture and safety

- All API keys stay server-side in environment variables.
- The browser never receives broker credentials.
- The watchlist is stored only in browser `localStorage`.
- No trading orders are created or sent.
- Provider failures produce explicit unavailable states rather than fabricated values.
- In-memory caches reduce repeated calls and rate-limit pressure.

## Current limitations

- Yahoo Finance access through `yfinance` is unofficial and can occasionally be unavailable.
- The Radar scans configured universes, not every listed security worldwide.
- Deep official ETF adapters are issuer/product specific; the current full example is Vanguard FTSE All-World UCITS ETF.
- Valuation scenarios, live news, portfolio overlap and broker synchronisation remain future work.
- XTB no longer provides a public trading API; IBKR integration would require a separately authenticated local gateway and is intentionally outside this Replit prototype.

## Disclaimer

This project is an educational decision-support prototype and not financial advice. Scores are partial models based on available data and must not be interpreted as automatic buy or sell recommendations.
