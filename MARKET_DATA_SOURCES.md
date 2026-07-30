# Futures and crack-spread data sourcing

## Decision

Use delayed Yahoo Finance chart responses for explicit NYMEX delivery-month
contracts behind the `QuoteAdapter` interface. Treat CME Group as the
authoritative source for product codes, units, and contract definitions, not as
an automated quote source.

The public CME quote endpoint rejected automated access from the development
environment and explicitly directed automated/commercial users to CME market
data delivery. Scraping that endpoint is therefore not an acceptable fallback.

## Contract mapping

| Product | CME Globex code | Yahoo explicit-month pattern | Quote unit | Conversion |
| --- | --- | --- | --- | --- |
| WTI crude oil | CL | `CL{month-code}{yy}.NYM` | USD/barrel | none |
| RBOB gasoline | RB | `RB{month-code}{yy}.NYM` | USD/gallon | multiply by 42 |
| NY Harbor ULSD | HO | `HO{month-code}{yy}.NYM` | USD/gallon | multiply by 42 |

CME contract references:

- WTI: https://www.cmegroup.com/markets/energy/crude-oil/light-sweet-crude.contractSpecs.html
- RBOB: https://www.cmegroup.com/markets/energy/refined-products/rbob-gasoline.contractSpecs.html
- ULSD: https://www.cmegroup.com/markets/energy/refined-products/heating-oil.contractSpecs.html

## Runtime validation

Every quote must pass all of these checks before display or calculation:

1. The returned symbol exactly matches the requested explicit contract.
2. Instrument type is `FUTURE`, exchange is `NYM`, and currency is USD.
3. The returned name matches the expected product and delivery month/year.
4. Price is finite and positive; volume is absent or non-negative.
5. The contract is still active under its CME rule-derived last trading day.
   CL terminates three business days before the 25th of the month before
   delivery (or before the preceding business day when the 25th is closed).
   RB and HO terminate on the last business day before the delivery month. The
   product rules and derived date are carried with every snapshot and quote.
6. Timestamp is valid. During an active session, a quote older than 30 minutes
   is retained but visibly marked stale (important for thin back months). The
   absolute maximum accepted age is four days.
7. Crack legs use the same delivery month and trading date. During an active
   session their timestamps may differ by no more than 30 minutes.

The application discloses that Yahoo is delayed and that the endpoint does not
publish an exact delay. It shows explicit symbols, prices, volumes, UTC quote
times, last-trade dates, and source URLs beside the charts. Each snapshot also
exposes retrieval time, `live_fetch` / `fresh_cache` / `stale_fallback` status,
visible cache age, and requested-versus-returned month coverage. Live values are
cached for five minutes; failed refreshes can use a marked snapshot for six hours.

Every skipped delivery month is retained as a structured omission containing
the exact missing contract symbols and fetch or join-validation reason. The UI
keeps the summary compact while making all warnings and omissions expandable.

The rule-derived expiration calendar covers NYMEX business-day weekends, New
Year's Day, Martin Luther King Jr. Day, Presidents Day, Good Friday, Memorial
Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, and Christmas.
CME can amend the trading or holiday schedule for an already-listed contract;
a licensed feed exposing authoritative per-contract expiration metadata remains
the production preference for handling those exceptional notices automatically.

## Calculations

- Gasoline crack: `RB × 42 − CL`
- Distillate crack: `HO × 42 − CL`
- 3-2-1 crack: `((2 × RB × 42) + (HO × 42)) / 3 − CL`
- Adjacent WTI calendar spread: `front-month CL − next-month CL`

A positive adjacent calendar spread is labeled backwardation; a negative value
is labeled contango. These are gross implied processing margins, not refinery
profit: they exclude crude-quality/location differences, yields, fuel, labor,
transport, renewable credits, and hedging costs.

## Replacement path

For production-grade entitlements, implement the same `QuoteAdapter` protocol
against a licensed CME or broker feed. The calculation and terminal rendering
layers require no change.
