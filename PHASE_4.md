# Phase 4 — Dynamic payment planning and rollover

Phase 4 builds repayment planning on top of the immutable Phase 2 ledger and the deterministic Phase 3 dated-interest engine.

## Core rule

A saved payment plan is planning metadata, not accounting history. Forecast payments are simulated in memory and are never inserted into the immutable ledger.

For each monthly payment date the engine:

1. Replays each account to that exact date using the Phase 3 interest engine.
2. Gives every enabled account its configured base payment, capped at the calculated payoff amount.
3. Keeps the remaining monthly budget available.
4. Rolls that remaining budget to the highest-priority account that is still open.
5. If that account settles with money still available, immediately rolls the unused amount to the next priority in the same month.
6. Repeats until the monthly budget is exhausted or all plan accounts are paid.

This means payment timing, interest-rate changes, backdated corrections and actual ledger payments all automatically alter the next forecast without changing the plan rules.

## Payment plan data

`PaymentPlan`

- name
- first payment date
- monthly budget
- monthly frequency
- strategy (`priority_rollover`)
- active / paused / archived status
- notes
- creator and timestamps

`PaymentPlanAccount`

- account
- priority
- base payment
- enabled flag

Plan configuration can be edited because it is planning metadata. Every create/update is written to the existing append-only audit trail with before/after snapshots.

## Forecast output

The plan forecast returns:

- projected portfolio payoff date
- future projected interest from plan start
- total forecast payments
- remaining balance at the chosen horizon
- per-account payoff dates
- per-account projected interest and forecast payment totals
- monthly schedule
- base vs rollover component for every forecast payment
- interest/principal/fee allocation for every simulated payment
- unused monthly budget, if all debts settle before the budget is exhausted

## Original Bank of Mum rollover case

The regression suite locks the original example into the codebase:

- Car: £1,000 outstanding, £200 base payment
- Fat Bike: £2,500 outstanding, £150 base payment
- Monthly budget: £350
- Priority: Car then Fat Bike
- 0% interest case

Expected behaviour:

- Car settles after month 5.
- Fat Bike receives £350 per month from month 6.
- Both accounts settle in month 10.
- Total forecast payments are £3,500.

A second test verifies same-month spillover when the first account needs less than its normal final payment.

## API

- `GET /api/payment-plans`
- `POST /api/payment-plans`
- `GET /api/payment-plans/{id}`
- `PUT /api/payment-plans/{id}`
- `GET /api/payment-plans/{id}/forecast?horizon_months=240`

## UI

The Forecast workspace now supports:

- creating a plan from existing accounts
- enabling/disabling accounts
- editing priorities
- editing base payments
- editing the total monthly budget
- setting the first payment date
- loading/updating saved plans
- payoff summary cards
- per-account payoff sequence
- month-by-month rollover schedule
- selectable 5/10/20-year forecast horizon

## Boundary with Phase 5

Phase 4 produces the authoritative baseline repayment plan.

Phase 5 can build broader scenario modelling on top of this foundation, for example temporary payment increases, lump sums, payment holidays, rate-change scenarios and side-by-side comparisons, without changing the saved baseline plan.
