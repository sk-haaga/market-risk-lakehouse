# Power BI dashboard

The dashboard reads the gold tables (a star schema) from a Databricks SQL warehouse. It has three
pages: **Exposures**, **VaR trend** and **Backtest**. The `.pbix` file is not committed. It's a
binary that embeds data, so build it with the steps below and export page screenshots to
`docs/img/` for the main README.

## 1. Connect

**Databricks (recommended)**

1. Power BI Desktop > **Get data** > **Azure Databricks** (or **Databricks** on AWS/GCP).
2. Use the SQL warehouse's **Server hostname** and **HTTP path** (SQL Warehouses > your warehouse >
   Connection details). Sign in with a personal access token or Microsoft Entra ID.
3. Data connectivity mode: **Import**. The data is a few MB; import keeps visuals fast and
   the dashboard works offline.
4. Navigate to `workspace` > `market_risk` (or your catalog/schema) and select the tables below.

**Offline (no workspace)**

Run `python scripts/run_local.py` and load the CSV files from `local_data/output/` with
**Get data > Text/CSV**. Table and column names are identical, so the model and measures work
unchanged.

## 2. Model

| Power BI table | Source table | Role |
|---|---|---|
| Date | `gold_dim_date` | Date dimension, mark as date table on `calendar_date` |
| Risk Factor | `gold_dim_risk_factor` | Dimension |
| Position | `gold_dim_position` | Dimension |
| Risk Method | `gold_dim_risk_method` | Dimension |
| Exposure | `gold_fact_position_exposure_daily` | Fact (position x day) |
| VaR | `gold_fact_var_daily` | Fact (portfolio x method x confidence x day) |
| Risk Contribution | `gold_fact_risk_contribution` | Fact (risk factor x method x confidence x day) |
| Backtest | `gold_fact_var_backtest` | Fact (portfolio x method x confidence x forecast day) |
| Backtest Summary | `gold_backtest_summary` | Fact (portfolio x method x confidence) |
| Confidence | calculated: `Confidence = DISTINCT ( 'VaR'[confidence_level] )` | Slicer dimension |

Relationships (all many-to-one, single direction, dimension -> fact):

| From (many) | To (one) |
|---|---|
| `Exposure[as_of_date]` | `Date[calendar_date]` |
| `Exposure[position_id]` | `Position[position_id]` |
| `Position[risk_factor_id]` | `Risk Factor[risk_factor_id]` |
| `VaR[as_of_date]` | `Date[calendar_date]` |
| `VaR[method]` | `Risk Method[method]` |
| `VaR[confidence_level]` | `Confidence[confidence_level]` |
| `Risk Contribution[as_of_date]` | `Date[calendar_date]` |
| `Risk Contribution[risk_factor_id]` | `Risk Factor[risk_factor_id]` |
| `Risk Contribution[method]` | `Risk Method[method]` |
| `Risk Contribution[confidence_level]` | `Confidence[confidence_level]` |
| `Backtest[forecast_date]` | `Date[calendar_date]` |
| `Backtest[method]` | `Risk Method[method]` |
| `Backtest[confidence_level]` | `Confidence[confidence_level]` |
| `Backtest Summary[method]` | `Risk Method[method]` |
| `Backtest Summary[confidence_level]` | `Confidence[confidence_level]` |

Then create the measures in [`measures.dax`](measures.dax). Formats: EUR measures `#,0`,
percentages `0.00%`, p-values `0.000`.

## 3. Pages

Every page has a date-range slicer (`Date[calendar_date]`). The VaR and Backtest pages also have
single-select slicers for `Risk Method[method_label]` and `Confidence[confidence_level]`
(defaults: Historical simulation, 0.99).

**Exposures**
- Cards: Net / Gross / Long / Short Exposure EUR (Latest), Exposure Date (Latest).
- Clustered bar: `Net Exposure EUR (Latest)` by `Risk Factor[risk_factor_name]`, legend `Position[book]`.
- Donut: `Gross Exposure EUR (Latest)` by `Risk Factor[asset_class]`.
- Donut: `Gross Exposure EUR (Latest)` by `Position[quote_currency]` (currency exposure).
- Line: `Exposure EUR` by `Date[calendar_date]`, legend `Position[book]` (portfolio value over time).
- Table: positions with quantity, local price, FX rate, exposure EUR, share of gross.

**VaR trend**
- Cards: VaR EUR (Latest), ES EUR (Latest), VaR % of Gross (Latest), VaR Change 1M.
- Line: `VaR EUR` and `ES EUR` by `Date[calendar_date]`.
- Line (method comparison): `VaR EUR` by date with `Risk Method[method_label]` as legend.
  Turn off "single select" interaction for this visual.
- Bar: `Risk Contribution EUR (Latest)` by `Risk Factor[risk_factor_name]`. It shows what drives the risk.
  For historical simulation these are ES contributions; for parametric methods, Euler VaR
  contributions. Both add up to the portfolio total.

**Backtest**
- Cards: Breaches, Expected Breaches, Breach Rate, Breaches Last 250D (Latest),
  Traffic Light (Latest) (conditional formatting: font colour = `Traffic Light Colour`), Model Result.
- Combo chart over `Date[calendar_date]`:
  - Columns: `Realised P&L EUR`
  - Lines: `VaR Threshold (P&L axis)` (and optionally `ES Threshold (P&L axis)`)
  - Breach markers: add `Breach P&L EUR` as a line series with the line turned off and markers
    on (red). Every marker is a day the loss exceeded the VaR forecast.
- Table from `Backtest Summary`: method, confidence, observations, breaches, expected,
  Kupiec / Christoffersen / conditional coverage p-values, ES ratio, traffic light, result.
- Column: `Breaches` by `Date[year_month]`, which makes clustering visible.

## 4. Refresh

Publish to the Power BI service and schedule a refresh after the Databricks job
(the job runs Tue-Sat 07:30 Helsinki, so refresh at 09:00).
