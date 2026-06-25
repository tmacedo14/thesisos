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
- Functional local Portfolio Manager with transactions, current prices, average monitoring cost, P&L, target weights, contribution optimisation and CSV import/export.
- Functional Portfolio Construction & Stress Test Engine using the saved Investment Policy, current transactions, funding gaps, scenario shocks and position-limit checks.
- Functional Decision Journal with thesis versioning, snapshots, entry zones, review dates and CSV export.
- Functional monitoring alerts for price, scores, RSI, pullback and thesis review dates.
- Evidence & Events Engine with SEC submissions, recent company news, source hierarchy, materiality and thesis-review gates.
- Stock-to-stock and ETF-to-ETF comparison.
- Printable analysis that can be saved as PDF from the browser.

- Live operational dashboard combining portfolio, Radar, alerts, thesis reviews, evidence and API health.
- Investor Policy Engine with risk-capacity score, personal limits, strategic allocation, portfolio compliance and policy-aware position sizing.

## Data sources

- **Finnhub:** US quotes and company profiles.
- **Finnhub Company News:** recent North American company news used as a secondary evidence layer.
- **OpenFIGI:** instrument identification and security type.
- **EODHD:** European ticker/ISIN listing discovery and previous close.
- **SEC EDGAR Company Facts:** official US financial statements.
- **SEC submissions history:** recent 10-K, 10-Q, 8-K, proxy, insider and capital-market filings.
- **ECB Data Portal:** official EUR reference exchange rates.
- **Yahoo Finance via yfinance:** historical daily price and volume data.
- **Official ETF issuers:** product pages, factsheets and KIID documents. The current official adapter covers Vanguard FTSE All-World UCITS ETF (VWCE/VWRP/VWRA share class).

## Opportunity Radar methodology

The Radar is a screening funnel, not an order generator. It now supports both static universes and automatic candidate discovery.

### Automatic discovery universes

- **US Multi-Factor Discovery:** alternates quality, profitable growth, value-quality and financial-quality candidates.
- **US Quality Discovery:** combines non-financial quality, financial quality and value-quality sources while limiting financial-only concentration in the shortlist.
- **US Profitable Growth Discovery:** requires candidates to appear in a profitable-growth or technology-growth source, preventing broad quality alone from qualifying.
- **US ETF Discovery:** prioritises liquid, sufficiently large, non-leveraged and non-inverse US-listed ETFs.

### Discovery funnel

1. Yahoo Finance screeners and bounded `EquityQuery` filters generate candidate pools.
2. The backend validates asset type, price, market capitalisation or net assets, and average liquidity.
3. Duplicate share classes or repeated companies are removed where the available name data permits.
4. Universe-specific selection policies create a diversified shortlist from the eligible pool.
5. Only the shortlist enters the complete fundamental, structural, valuation, technical, event and data-quality framework.

Discovery responses expose the candidate-pool size, eligible count, selection policy, shortlist size, provider errors and whether a static fallback was required. Results are cached in memory for 30 minutes to reduce repeated provider calls.

The final Radar composite score uses:

- 70% fundamental or ETF structural score;
- 20% Technical Engine score;
- 10% data completeness.

A high result means **candidate for deeper analysis**. It does not mean “buy”. Event gates, valuation, current information, portfolio fit, concentration limits, position size and entry planning can still block or alter the final decision.

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

1. Configure **Investor Policy Engine** and save personal limits.
2. Open the live Dashboard to show portfolio, Radar, alerts, reviews, evidence and API status.
3. Search `AAPL` to demonstrate stock fundamentals, valuation, technical analysis and policy-aware position sizing.
3. Search `VWCE`, select XETRA, and demonstrate official ETF structure, costs, tracking and holdings.
4. Open **Opportunity Radar** and execute **US Multi-Factor Discovery** to demonstrate automatic discovery, pre-filtering, shortlist construction and full framework analysis.
5. Add an asset to **Watchlist**.
6. Compare `MSFT` with `AAPL`.
7. Add AAPL or VWCE to **My Portfolio**, refresh prices and calculate the next contribution.
7. Open **Notícias & Alertas**, load AAPL and inspect SEC filings and recent news.
8. Save the current analysis in **Investment Journal**, create monitoring alerts and update them.
9. In an analysis, choose **Imprimir / PDF**.

## Architecture and safety

- All API keys stay server-side in environment variables.
- The browser never receives broker credentials.
- The browser keeps a local cache in `localStorage`; authenticated users can synchronize the supported namespaces to their own Supabase account.
- No trading orders are created or sent.
- Provider failures produce explicit unavailable states rather than fabricated values.
- In-memory caches reduce repeated calls and rate-limit pressure.

## Current limitations

- Yahoo Finance access through `yfinance` is unofficial and can occasionally be unavailable.
- Automatic discovery uses bounded Yahoo Finance screener result pools. It is broader than the static universes but is not an exhaustive scan of every listed security worldwide.
- Deep official ETF adapters are issuer/product specific; the current full example is Vanguard FTSE All-World UCITS ETF.
- Full tax accounting, historical FX, live news, portfolio overlap look-through and broker synchronisation remain future work.
- XTB no longer provides a public trading API; IBKR integration would require a separately authenticated local gateway and is intentionally outside this Replit prototype.

## Disclaimer

This project is an educational decision-support prototype and not financial advice. Scores are partial models based on available data and must not be interpreted as automatic buy or sell recommendations.


## Valuation Engine

Para ações com dados SEC e capitalização Finnhub, o ThesisOS calcula automaticamente:

- free cash flow yield;
- P/E, P/S e P/B derivados;
- crescimento de FCF implícito num reverse DCF;
- score de exigência relativa com cobertura explícita;
- simulador DCF com pressupostos editáveis.

O modelo é deliberadamente parcial e não substitui múltiplos históricos, comparáveis setoriais, guidance, normalização do FCF, notícias ou contexto da carteira. O Opportunity Radar usa o valuation como uma camada adicional, sem converter o ranking numa recomendação automática.

## Portfolio construction methodology

The Portfolio Construction Engine converts the saved Investment Policy into target category weights, compares them with the transaction-based portfolio, and proposes either contribution-only funding or a full simulated rebalance. Stress tests apply transparent shocks to policy buckets and are not forecasts or Value-at-Risk estimates. Direct currency concentration excludes ETF look-through unless official holdings data is available.

## Supabase Auth e sincronização por utilizador

ThesisOS supports personal accounts through Supabase Auth. Each authenticated
user receives an independent cloud workspace stored in
`public.thesisos_user_state`.

Synchronized namespaces:

- watchlist;
- portfolio transactions and cached quotes;
- Decision Journal;
- monitoring alerts;
- Investor Policy;
- saved Portfolio Construction plan;
- latest Radar result;
- latest Evidence Feed.

### Supabase setup

1. Execute `supabase_auth_migration.sql` once.
2. In **Authentication → URL Configuration**, set the production Site URL to
   the public ThesisOS URL and add the same origin to the allowed redirect
   URLs.
3. Keep Email/Password enabled in **Authentication → Providers**.
4. Add these Replit Secrets:

```text
SUPABASE_URL
SUPABASE_PUBLISHABLE_KEY
```

`SUPABASE_ANON_KEY` pode ser usado como alternativa de compatibilidade à
chave publicável.

A chave publicável é enviada ao navegador para que o cliente oficial do
Supabase possa criar e renovar sessões. A aplicação não necessita de uma chave
`service_role` para guardar ou restaurar os dados dos utilizadores.

### Authorization model

- Supabase Auth manages registration, email confirmation, login, logout and
  password recovery.
- The browser sends the authenticated user's access token to the ThesisOS
  backend.
- The backend forwards that same user token to Supabase PostgREST.
- Row Level Security enforces `auth.uid() = user_id`.
- Utilizadores anónimos não têm acesso à tabela.
- Cada conta acede apenas às linhas associadas ao respetivo `user_id`.

### Conflict strategy

- `localStorage` remains the offline cache and local fallback.
- If the browser is empty and the account contains cloud data, the cloud copy
  is restored.
- If the account is empty and the browser contains data, the local copy is
  associated with the account.
- If both contain data, ThesisOS asks the user to choose which copy should
  prevail before enabling automatic synchronization.

Cloud Sync stores application state only. It does not store broker credentials,
place orders or replace tax records.
