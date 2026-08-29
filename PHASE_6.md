# Phase 6 — Accounting AI and Ollama settings

Phase 6 adds a Context Studio-style AI layer to Bank of Mum without moving any accounting logic into the language model.

## Core rule

**The model interprets. Bank of Mum calculates.**

The immutable ledger, Phase 3 dated-interest engine, Phase 4 payment-planning engine and Phase 5 scenario engine remain the source of truth.

The AI does not receive tools that can:

- post payments or advances;
- create ledger entries;
- reverse transactions;
- correct transactions;
- edit historical transactions;
- create contractual interest-rate periods; or
- alter the saved baseline payment plan.

## Ollama configuration

AI settings are stored in the existing `application_settings` table and can be changed from `/settings`.

Stored settings:

- `ai.ollama_url`
- `ai.ollama_model`
- `ai.max_tool_calls`
- `ai.timeout_seconds`

Defaults continue to come from the application configuration:

- Ollama: `http://192.168.1.249:11434`
- model: `qwen3:14b`

Every settings update produces an immutable audit event.

### Model discovery

`GET /api/ai/models?base_url=...` calls Ollama `/api/tags` and returns model name, family, parameter size, quantisation, size and modification date.

The Settings workspace can therefore point Bank of Mum at another Ollama server and discover its installed models without editing code or environment files.

## AI tools

The bounded Ollama tool loop exposes only the following tools:

1. `get_portfolio_summary`
2. `list_accounts`
3. `get_account_balance`
4. `get_account_ledger`
5. `list_payment_plans`
6. `forecast_payment_plan`
7. `list_scenarios`
8. `compare_scenario`
9. `compare_scenarios`
10. `get_audit_history`
11. `propose_scenario`

Balance, interest and forecast figures therefore come from deterministic Python services rather than LLM arithmetic.

## Draft scenario proposals

`propose_scenario` is the only AI tool that persists business data.

It is deliberately limited:

- status is always `draft`;
- `created_by` is `ai`;
- the proposal is audited;
- scenario assumptions are validated by the Phase 5 scenario rules;
- no ledger entry is created;
- no contractual rate is created;
- the baseline payment plan is unchanged; and
- the resulting scenario comparison is returned immediately for review.

The chat request also has `allow_scenario_proposals`. When false, even draft scenario creation is blocked.

Opening an AI-created draft in the Scenarios workspace and saving it as an active scenario is the human approval step.

## API

### Settings

- `GET /api/ai/settings`
- `PUT /api/ai/settings`
- `GET /api/ai/models`
- `GET /api/ai/capabilities`

### Chat

- `POST /api/ai/chat`

Example body:

```json
{
  "messages": [
    {"role": "user", "content": "What do we owe today and when will the current plan clear it?"}
  ],
  "allow_scenario_proposals": true
}
```

The response contains:

- final assistant reply;
- model/provider;
- tool activity summaries;
- token usage where supplied by Ollama;
- `accounting_mode: read_only`; and
- `scenario_proposals: draft_only`.

## Audit

Each completed AI query records an `ai_query` audit event containing:

- the latest user question;
- response summary;
- tool activity;
- selected model; and
- Ollama token counts when available.

AI setting changes and AI-created draft scenarios are also audited.

## Frontend

### `/ai`

The AI workspace includes:

- Context Studio-style conversation layout;
- quick accounting questions;
- visible tool activity beneath replies;
- model/server status;
- token usage;
- explicit accounting safety indicators; and
- a per-request toggle for draft scenario proposals.

### `/settings`

The Settings workspace includes:

- editable Ollama URL;
- editable model;
- model discovery;
- maximum tool-call setting;
- request timeout setting; and
- an explanation that changing the LLM never changes the accounting calculation method.

Persistent shortcuts and the existing left-rail AI/Settings controls route into the new workspaces.

## Validation

Phase 6 regression tests verify that:

- the AI tool surface contains no ledger mutation operations;
- AI settings persist and are audited;
- balance tools use the deterministic calculation engine;
- balance queries do not write ledger rows;
- AI scenario proposals are always drafts;
- draft proposals do not create ledger or contractual-rate rows; and
- scenario proposals can be disabled per chat request.

All previous Phase 2–5 accounting, interest, planning and scenario regression tests remain part of the same CI suite.

## Next phase

Phase 7 can focus on reporting and production polish: statements, downloadable exports, annual interest summaries, backup/restore, richer people/account management, deployment documentation and end-to-end migration verification.
