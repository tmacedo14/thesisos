# ThesisOS Alpha — Investment Intelligence

**Release:** `v0.1.0-alpha`
**Stage:** Alpha
**Framework Engine:** `0.9`

ThesisOS Alpha is a functional academic prototype for evidence-based investment analysis. It identifies stocks and ETFs, gathers market and official data, applies transparent rules based on three investment frameworks, and separates facts, calculations, interpretation and missing information.

This Alpha release prioritises a stable, auditable and rules-based decision-support workflow.

> The **AI Investment Brief is not active in this Alpha release**. It is planned for the Beta version.

## Main capabilities

- Search by ticker or ISIN, with listing selection for European instruments.
- Stock analysis using Finnhub, OpenFIGI, SEC EDGAR and ECB data.
- ETF analysis using EODHD listings and official issuer documentation.
- Fundamental and structural Framework Engine with explicit coverage and limitations.
- Cash-flow, debt, dilution and capital-allocation analysis for US stocks.
- ETF costs, tracking error, holdings, countries, sectors and aggregate valuation.
- Technical Engine using Yahoo Finance history through `yfinance`.
- Moving averages, RSI, returns, pullback, volatility and drawdown analysis.
- Automated Opportunity Radar with static and automatically discovered universes.
- Persistent local watchlist.
- Portfolio Manager with transactions, current prices, average cost, P&L, target weights, contribution optimisation and CSV import/export.
- Portfolio Construction and Stress Test Engine based on the saved Investment Policy.
- Decision Journal with thesis versioning, snapshots, entry zones, review dates and CSV export.
- Monitoring alerts for prices, scores, RSI, pullback and thesis review dates.
- Evidence and Events Engine using SEC submissions and recent company news.
- Stock-to-stock and ETF-to-ETF comparison.
- Printable analysis that can be saved as PDF from the browser.
- Live operational dashboard combining portfolio, Radar, alerts, reviews, evidence and API health.
- Investor Policy Engine with risk-capacity score, personal limits, strategic allocation, portfolio compliance and policy-aware position sizing.
- Supabase Auth and isolated cloud synchronisation per authenticated user.

## Data sources

- **Finnhub:** US quotes, company profiles and recent company news.
- **OpenFIGI:** instrument identification and security type.
- **EODHD:** European ticker and ISIN listing discovery and previous close.
- **SEC EDGAR Company Facts:** official US financial statements.
- **SEC submissions history:** recent 10-K, 10-Q, 8-K, proxy, insider and capital-market filings.
- **ECB Data Portal:** official EUR reference exchange rates.
- **Yahoo Finance via yfinance:** historical daily price and volume data.
- **LogoKit:** stock and ETF logos through a server-side proxy.
- **Official ETF issuers:** product pages, factsheets and KIID documents.

The current deep official ETF adapter covers the Vanguard FTSE All-World UCITS ETF share class, including VWCE, VWRP and VWRA listings.

## Opportunity Radar methodology

The Opportunity Radar is a screening funnel and not an order generator.

It supports both static universes and automatic candidate discovery.

### Automatic discovery universes

- **US Multi-Factor Discovery:** alternates quality, profitable growth, value-quality and financial-quality candidates.
- **US Quality Discovery:** combines non-financial quality, financial quality and value-quality sources.
- **US Profitable Growth Discovery:** prioritises profitable-growth and technology-growth candidates.
- **US ETF Discovery:** prioritises liquid, sufficiently large, non-leveraged and non-inverse US-listed ETFs.

### Discovery funnel

1. Yahoo Finance screeners and bounded `EquityQuery` filters generate candidate pools.
2. The backend validates asset type, price, market capitalisation or net assets, and average liquidity.
3. Duplicate share classes and repeated companies are removed where available data permits.
4. Universe-specific policies create a diversified shortlist.
5. Only the shortlist enters the full fundamental, structural, valuation, technical, evidence and data-quality framework.

Discovery responses expose:

- candidate-pool size;
- eligible count;
- selection policy;
- shortlist size;
- provider errors;
- whether a static fallback was required.

Discovery results are cached in memory for 30 minutes.

Radar cards use Finnhub company logos when available. Otherwise, a server-side LogoKit proxy attempts the financial ticker and, for ETFs or funds, a controlled issuer-domain fallback.

Logo images are cached for 24 hours. The LogoKit token is never sent to the browser.

### Composite score

The final Radar composite score uses:

- 70% fundamental or ETF structural score;
- 20% Technical Engine score;
- 10% data completeness.

A high result means **candidate for deeper analysis**. It does not mean “buy”.

Valuation, recent evidence, portfolio fit, concentration limits, position sizing and entry planning can still block or alter a decision.

## Run locally or in Replit

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure environment variables

Use `.env.example` as the configuration reference.

Supported provider variables:

```text
FINNHUB_API_KEY
OPENFIGI_API_KEY
EODHD_API_TOKEN
LOGOKIT_PUBLISHABLE_TOKEN
SEC_USER_AGENT
```

`SEC_USER_AGENT` should identify the application and include a contact email, following SEC access guidance.

Optional Supabase variables:

```text
SUPABASE_URL
SUPABASE_PUBLISHABLE_KEY
```

`SUPABASE_ANON_KEY` remains available as a compatibility alias for older Supabase projects.

### 3. Start the server

```bash
python3 server.py
```

### 4. Open the application

Open port `3000`.

## Tests and validation

### Quick release validation

```bash
python3 test_thesisos_final.py
```

Expected Alpha baseline:

```text
23 passed, 1 warning, 0 failed
```

### Complete validation

This mode includes live providers, integrated AAPL analysis, valuation, evidence and a bounded Opportunity Radar execution.

```bash
python3 test_thesisos_final.py --full
```

Expected Alpha baseline:

```text
28 passed, 1 warning, 0 failed
```

### Supabase Auth validation

The ThesisOS server must be running before this validation is executed.

Start the server in one terminal:

```bash
python3 server.py
```

Run the validation in another terminal:

```bash
python3 test_supabase_auth.py
```

An authenticated login test is optional:

```bash
THESISOS_TEST_EMAIL=test@example.com
THESISOS_TEST_PASSWORD=your_test_password
python3 test_supabase_auth.py --login
```

### Known validation warning

When Node.js is unavailable, the optional JavaScript syntax validation using `node --check` is skipped.

This produces one warning but does not prevent the Python server, API or application validation from completing.

## Useful demo flow

1. Configure the **Investor Policy Engine** and save personal limits.
2. Open the Dashboard to show portfolio, Radar, alerts, reviews, evidence and API status.
3. Search `AAPL` to demonstrate stock fundamentals, valuation, technical analysis and policy-aware position sizing.
4. Search `VWCE`, select XETRA and demonstrate official ETF structure, costs, tracking and holdings.
5. Open **Opportunity Radar** and execute **US Multi-Factor Discovery**.
6. Add an asset to the **Watchlist**.
7. Compare `MSFT` with `AAPL`.
8. Add AAPL or VWCE to **My Portfolio**, refresh prices and calculate the next contribution.
9. Open **Notícias & Alertas**, load AAPL and inspect SEC filings and recent news.
10. Save the current analysis in the **Investment Journal** and create monitoring alerts.
11. In an analysis, choose **Imprimir / PDF**.

## Architecture and safety

- API keys remain server-side in environment variables.
- Provider credentials are not embedded in `index.html`.
- The browser never receives broker credentials.
- The LogoKit publishable token is used through a backend proxy.
- Supabase uses a publishable browser key and authenticated user access tokens.
- No Supabase `service_role` key is required.
- Row Level Security isolates cloud data by authenticated user.
- The browser keeps a local cache in `localStorage`.
- Authenticated users can synchronise supported namespaces with their own Supabase account.
- No trading orders are created or sent.
- Provider failures produce explicit unavailable states instead of fabricated values.
- In-memory caches reduce repeated calls and rate-limit pressure.
- The application is a decision-support tool and not an autonomous investment system.

## Valuation Engine

For stocks with SEC data and Finnhub market capitalisation, ThesisOS calculates:

- free cash flow yield;
- derived P/E, P/S and P/B ratios;
- reverse DCF implied free cash flow growth;
- relative valuation-demand score;
- editable DCF scenarios.

The model is deliberately partial.

It does not replace:

- historical multiple analysis;
- sector comparables;
- company guidance;
- normalisation of free cash flow;
- recent material evidence;
- portfolio context;
- independent investor judgement.

The Opportunity Radar uses valuation as an additional layer and does not convert rankings into automatic recommendations.

## Portfolio construction methodology

The Portfolio Construction Engine converts the saved Investment Policy into target category weights.

It compares those targets with the transaction-based portfolio and proposes either:

- contribution-only funding; or
- a full simulated rebalance.

Stress tests apply transparent shocks to policy buckets.

They are not forecasts and are not Value-at-Risk estimates.

Direct currency concentration excludes ETF look-through unless official holdings data is available.

## Supabase Auth and user synchronisation

ThesisOS supports personal accounts through Supabase Auth.

Each authenticated user receives an independent cloud workspace stored in:

```text
public.thesisos_user_state
```

### Synchronized namespaces

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
2. In **Authentication → URL Configuration**, configure the production Site URL.
3. Add the same origin to the allowed redirect URLs.
4. Keep Email and Password enabled in **Authentication → Providers**.
5. Add these environment variables:

```text
SUPABASE_URL
SUPABASE_PUBLISHABLE_KEY
```

The publishable key is sent to the browser so that the official Supabase client can create and renew authenticated sessions.

The application does not require a `service_role` key.

### Authorization model

- Supabase Auth manages registration, email confirmation, login, logout and password recovery.
- The browser sends the authenticated user's access token to the ThesisOS backend.
- The backend forwards that user token to Supabase PostgREST.
- Row Level Security enforces `auth.uid() = user_id`.
- Anonymous users cannot access the cloud table.
- Each account can only access rows associated with its own `user_id`.

### Conflict strategy

- `localStorage` remains the offline cache and local fallback.
- If the browser is empty and the account contains data, the cloud copy is restored.
- If the account is empty and the browser contains data, the local copy is associated with that account.
- If both contain data, ThesisOS asks which copy should prevail before enabling automatic synchronisation.

Cloud Sync stores application state only.

It does not store broker credentials, place orders or replace official tax records.

## Current Alpha limitations

- The AI Investment Brief is a Beta roadmap item and is not active in Alpha.
- Yahoo Finance access through `yfinance` is unofficial and can occasionally be unavailable.
- Automatic discovery uses bounded Yahoo Finance result pools and is not an exhaustive scan of every listed security worldwide.
- Deep official ETF adapters are issuer and product specific.
- Full tax accounting is not implemented.
- Historical foreign exchange accounting is not implemented.
- Complete ETF portfolio overlap look-through is not implemented.
- Broker synchronisation is not implemented.
- XTB no longer provides a public trading API.
- IBKR integration would require a separately authenticated local gateway.
- `index.html` and `server.py` remain large monolithic files.
- The monoliths were deliberately not refactored immediately before the Alpha release to avoid unnecessary regression risk.
- Validation is currently command-driven.
- Automated GitHub Actions continuous integration is planned for Beta.

## Roadmap

### Beta — `v0.5.0-beta`

Planned Beta work includes:

- AI Investment Brief generated through a server-side AI provider;
- prompts grounded only in selected ThesisOS data and evidence;
- source-aware summaries;
- uncertainty and missing-data disclosure;
- explicit unavailable states;
- no AI API key exposed to the browser;
- modularisation of the frontend and backend;
- GitHub Actions validation for pushes and pull requests;
- stronger request limits and security headers;
- improved logging, observability and provider fallbacks;
- additional official ETF adapters;
- portfolio overlap analysis.

### Final — `v1.0.0`

The Final release is intended to focus on:

- stable API contracts;
- documented data schemas;
- expanded automated and integration tests;
- expanded account-isolation tests;
- performance review;
- accessibility review;
- user-experience review;
- production deployment and recovery documentation;
- consolidated release notes;
- final known-limitations document;
- final security review.

## Release status

The Alpha release is considered ready for academic demonstration when:

- QUICK validation passes;
- FULL validation passes;
- no test failures remain;
- no secrets are committed;
- the public repository is synchronised;
- the production deployment responds successfully.

## Disclaimer

This project is an educational decision-support prototype and not financial advice.

Scores are partial models based on available data and must not be interpreted as automatic buy or sell recommendations.

The system does not execute transactions, manage funds or replace professional financial, legal, accounting or tax advice.

## Beta HTTP Security & CI v1

A branch Beta inclui uma baseline de segurança HTTP aplicada à API e aos
ficheiros estáticos, testes live dedicados e um workflow GitHub Actions
sem segredos ou providers externos.

A Content Security Policy é transitória porque o frontend Alpha ainda
contém JavaScript e CSS inline. A extração desses blocos permitirá remover
`unsafe-inline` numa fase posterior.

Detalhes: `docs/http_security_ci.md`.
