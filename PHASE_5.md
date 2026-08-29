# Phase 5 — What-if scenarios and comparisons

Phase 5 adds a scenario layer on top of the Phase 4 saved payment plans. A scenario changes forecast assumptions only; it never edits the immutable ledger, contractual interest-rate history or the saved baseline plan.

## Accounting boundary

The application now has three deliberately separate layers:

1. **Accounting truth** — immutable ledger transactions, reversals/corrections, audit history and contractual rate periods.
2. **Baseline repayment plan** — saved priority, base payments, first payment date and monthly budget.
3. **Scenario** — temporary hypothetical changes compared against that baseline.

A scenario may be edited because it is planning metadata. Every create/update is still written to the audit log.

## Supported scenario changes

- `budget_delta` — increase or reduce the monthly budget from an effective date, optionally until an end date.
- `budget_override` — replace the monthly budget for a date range.
- `lump_sum` — one exact-date additional payment to a selected account.
- `payment_holiday` — skip all plan payments or one selected account for a date range.
- `base_payment_override` — temporarily/permanently replace one account's base payment.
- `priority_override` — change the rollover priority from an effective date.
- `interest_rate` — assume a future rate for one account without creating a contractual rate row.

## Deterministic replay

Scenario calculations reuse the Phase 3 interest engine and Phase 4 rollover engine. The application does not ask an LLM to calculate balances.

- Lump sums use their exact calendar date, including dates between normal monthly plan dates.
- Future rate assumptions use the same Actual/365, Actual/366, Actual/Actual and 30/360 conventions as contractual rates.
- Payments are capped at the calculated payoff balance.
- A completed account releases unused budget to the next priority account.
- Backdated real ledger changes alter both baseline and scenario results automatically.

## Comparison outputs

Each scenario can be compared with its saved baseline plan to show:

- baseline and scenario payoff dates
- months saved or lost
- baseline and scenario projected interest
- interest saved or added
- forecast-payment difference
- remaining balance difference at the selected horizon
- per-account payoff dates and projected interest
- exact scenario events such as lump sums
- period-by-period active assumptions

Multiple saved scenarios using the same baseline plan can also be compared side by side.

## API

- `GET /api/scenarios`
- `POST /api/scenarios`
- `GET /api/scenarios/compare?scenario_ids=...`
- `GET /api/scenarios/{scenario_id}`
- `PUT /api/scenarios/{scenario_id}`
- `GET /api/scenarios/{scenario_id}/forecast`
- `GET /api/scenarios/{scenario_id}/comparison`

## UI

The dedicated `/scenarios` workspace follows the Context Studio visual language and includes:

- saved baseline-plan selector
- saved scenario selector
- typed change editor
- quick-add buttons for common changes
- baseline-vs-scenario KPI cards
- detailed metric comparison
- per-account outcome comparison
- scenario schedule with active-change indicators
- multi-scenario side-by-side comparison
- a persistent shortcut from the main application shell

## Validation and safety

Scenario validation rejects:

- changes before the baseline plan's first payment date
- account-specific changes for accounts not in the baseline plan
- invalid/negative values where inappropriate
- invalid priorities
- future rates above 100%
- end dates on exact-date changes such as lump sums and future rates

Regression tests cover additional monthly budget, exact-date lump sums, payment holidays, future-rate assumptions and non-mutation of accounting tables.

## Phase 6 readiness

Phase 5 provides deterministic tools that the AI layer can safely call in Phase 6. The LLM can interpret questions such as “what if I add £100 per month?” or “what if the rate rises in January?”, while the Python accounting and scenario engines remain responsible for all calculations.
