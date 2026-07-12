# stock-optimizer

Tax-aware daily portfolio analysis for an Interactive Brokers account, built
for the specific situation of a **US citizen tax-resident in Spain**.

**Phase 1 (this repo): read-only.** It pulls your positions at tax-lot level
via the IBKR Flex Web Service, stores them in SQLite, and produces a daily
markdown report with:

- Total valuation in EUR and allocation drift vs. your target allocation
  (threshold bands, so it only tells you to act when it matters)
- Per-lot unrealized P&L with estimated tax cost of selling under **both**
  regimes: Spain savings-income brackets (FIFO) and US LTCG/STCG (+NIIT),
  combined with a simplified max(ES, US) foreign-tax-credit model
- Tax-loss harvesting candidates, checked against **both** wash-sale rules
  (US 30 days, Spain 2 months)
- Warnings for likely **PFICs** (non-US-domiciled funds/ETFs — punitive for
  US citizens) and unclassified tickers

Phase 2 (planned): concrete rebalance/harvest trade recommendations with
after-tax math. Phase 3 (optional): approval-gated execution via IB Gateway.

## Setup

### 1. Create the IBKR Flex Query

In IBKR Client Portal → **Performance & Reports → Flex Queries** → create a
new **Activity Flex Query**:

- **Sections** (enable these three):
  - **Open Positions** — Options: select **Lot** level of detail. Fields: at
    minimum Account ID, Currency, FX Rate to Base, Asset Class, Symbol,
    Description, ISIN, Report Date, Position, Mark Price, Position Value,
    Cost Basis Money, Cost Basis Price, Open Date/Time, Level of Detail.
  - **Trades** — Options: Execution. Fields: Trade ID, Account ID, Currency,
    FX Rate to Base, Asset Class, Symbol, ISIN, Trade Date, Buy/Sell,
    Quantity, Trade Price, FIFO P/L Realized.
  - **Cash Report** — Fields: Account ID, Currency, Ending Cash, FX Rate to Base.
- **Delivery configuration**: Period = *Last Business Day* is fine for the
  daily run (Trades benefit from *Last 365 Days* on the first run so the
  wash-sale check has history — run once with that, then switch, or keep 365
  days; trades are upserted by ID so duplicates are harmless).
- **Format**: XML.

Note the **Query ID** it shows after saving.

### 2. Enable the Flex Web Service

**Performance & Reports → Flex Queries → Flex Web Service Configuration** →
enable, and copy the **token** (it expires — max 1 year — set a reminder).

### 3. Install and run

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env                                    # paste token + query ID
cp config/settings.example.yaml config/settings.yaml   # your tax rates
cp config/targets.example.yaml config/targets.yaml     # your allocation
portfolio-optimizer run
```

`.env`, `config/settings.yaml`, `config/targets.yaml`, `data/` and
`reports/` are gitignored — credentials, personal tax rates, holdings and
statements never land in the repo.

The report prints to stdout and is saved to `reports/YYYY-MM-DD.md`.
Try it without credentials using the bundled sample data:

```bash
portfolio-optimizer ingest tests/fixtures/sample_flex.xml
portfolio-optimizer report
```

### 4. Configure your targets

Edit [config/targets.yaml](config/targets.yaml) — define your asset classes,
target weights, and which tickers belong to each. Edit
[config/settings.yaml](config/settings.yaml) with your actual US marginal
rates and Spain YTD realized gains.

### 5. (Optional) Run daily via GitHub Actions

In a **private** GitHub repo (fork/mirror this one), add `IBKR_FLEX_TOKEN`
and `IBKR_FLEX_QUERY_ID` as Actions secrets, and the included workflow
(`.github/workflows/daily.yml`) runs each weekday morning and uploads the
report as an artifact. The job deliberately refuses to run on public repos —
artifacts on a public repo are downloadable by anyone, and the report is
your full portfolio.

## Known simplifications (Phase 1)

- **Cost-basis FX**: cost is converted to EUR at *today's* rate. Spain
  requires purchase-date FX for the cost leg; historical FX is a planned
  enhancement.
- **FTC model**: combined tax ≈ max(Spain, US). Real credits depend on
  income baskets and treaty resourcing.
- Spain's loss-offset ordering rules (savings base vs. capital gains base,
  4-year carryforward, 25% cap against other savings income) are not modeled.
- Wash-sale checks look **backward** only; after harvesting, don't rebuy for
  30 days (US) / 2 months (Spain).

**Nothing here is tax advice.** Verify anything material with your asesor
fiscal / US CPA — the dual-status situation has traps (PFIC, Modelo 720/721,
D-6, exit tax) that a script can flag but not resolve.
