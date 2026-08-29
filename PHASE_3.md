# Phase 3 — Date-sensitive interest engine

Phase 3 makes Bank of Mum balances calculation-driven rather than nominal-ledger-only.

## Calculation rules

- The immutable ledger remains the source of dated financial events.
- Interest is derived deterministically between events; it is not silently written back into historical ledger rows.
- Default method: daily simple interest on outstanding principal.
- Default payment waterfall: fees → accrued interest → principal.
- Every backdated payment, correction or reversal replays the account from the beginning through the requested as-at date.
- Calculations use Decimal arithmetic and round displayed money to pennies.

## Day-count conventions

Rate periods can use:

- Actual / 365
- Actual / 366
- Actual / Actual
- 30 / 360 (US convention)

## Rate periods

Interest rates are append-only contractual records with an effective-from date. Multiple periods can therefore model variable-rate lending. Rate rows are protected by SQLite triggers against UPDATE and DELETE; a new rate period supersedes the previous effective rate.

Existing Phase 1/2 accounts receive an initial rate period migrated from `annual_interest_rate`. New legacy imports create their rate period during import.

## Payment allocation

For each payment the engine exposes:

- interest accrued since the previous dated event
- amount allocated to fees
- amount allocated to accrued interest
- amount allocated to principal
- remaining principal
- remaining accrued interest
- calculated balance after the event

Reversals undo the original transaction's calculated bucket allocation, so reversing a payment restores the exact principal/interest split rather than treating the reversal as an arbitrary new advance.

## API

- `GET /api/accounts/{id}/calculation?as_of=YYYY-MM-DD`
- `GET /api/accounts/{id}/rates`
- `POST /api/accounts/{id}/rates`
- `PUT /api/accounts/{id}/interest-settings`

Transaction create/reverse/correct responses now include the recalculated current account result.

## UI

The Accounts workspace now provides:

- account selector
- balance-as-at date
- principal / accrued interest / total balance cards
- effective rate history
- append-only rate creation
- full payment allocation timeline

The dashboard now distinguishes outstanding principal from accrued interest.

## Phase 4 handoff

Phase 4 can build payment plans and debt-snowball rules on this calculation engine, including automatically rolling a cleared account's regular payment onto the next account without changing historical accounting data.
