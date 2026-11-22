# Runbook: enabling AI draft preparation in production

Status: proposal for owner review, 2026-11-21. Nothing in this file has been executed. Prod runs with the
flag OFF (handoff `.claude/handoffs/2026-11-21-1830.md` section 3). Every step below that spends money, changes
a secret or deploys needs the owner's explicit yes at the time it is run.

## 1. Scope

`MIRUM_ENGINEERING_AI_DRAFTS` (`backend/config.py:82`, default off) turns on `POST /engineering/v1/drafts`
with `mode: "ai"` for four capabilities:

| Capability | AI proposer | Source |
|---|---|---|
| rectangular_cantilever (beam, B13) | `engineering.proposer` (`make_proposer`) | `backend/engineering/api_v1.py:181`, `:315` |
| co2_dragster | `engineering.dragster_proposer:AiProposer` | `backend/engineering/capabilities.py:504-505` |
| regen_chamber | `engineering.regen_proposer:AiProposer` | `backend/engineering/capabilities.py:546` |
| glider_cem | `engineering.glider_proposer:AiProposer` | `backend/engineering/capabilities.py:588` |

rectangular_plate has no AI proposer (`capabilities.py:624`, `proposers={}`); its AI request is refused as
unsupported before any budget check (`api_v1.py:266-269`). The flag does not change form/rules drafts, review,
jobs or acceptance. An AI proposal is only a draft: it still goes through the reviewed brief.

With the flag off no provider object is built and no key is read for this feature (`config.py:78-80`,
`api_v1.py:157-158`, `proposal_contract.py:143-150`).

## 2. Preconditions (all must hold before any step)

- Prod image pinned by digest; rollback digests known (handoff 1830 section 3: current `mirum-api` v10 /
  `mirum` v8; rollback v9 / v7).
- The provider key exists on `mirum-api` as the secret `GEMINI_API_KEY_HEAVY` (name only; the beam
  proposer reads it at `backend/engineering/proposer.py:167,182`; the regen and glider proposers reuse
  `GeminiProposalProvider` from `proposer.py` (`regen_proposer.py:26`, `glider_proposer.py:29`)).
  UNVERIFIED in this runbook: that the secret is currently set on `mirum-api` (check with
  `fly secrets list -a mirum-api`, which shows names and digests, never values).
- Lifetime live budget so far: B13 91 calls; E05 67 of an authorized 90 (23 unspent, tags spent) per
  handoff `2026-11-21-0455.md`. A prod live check is a new authorization with its own budget.
- Provider-unavailable policy from E05: HTTP 429/503/504 mean "unavailable", not a failure of the proposer.

## 3. Spend caps that exist today

| Cap | Env var | Default | Enforced at |
|---|---|---|---|
| Provider calls per rolling 24 h, process-wide, all four capabilities, repair calls included | `MIRUM_ENGINEERING_AI_DAILY_CALLS` | 200 | `config.py:87`; `limits.AiCallBudget` (`limits.py:8-24`); seeded from the ledger after restart (`api_v1.py:175-178`) |
| Provider calls in flight | `MIRUM_ENGINEERING_AI_CONCURRENCY` | 2 | `config.py:89` |
| Generator spend written to the ledger before each call | (none; uses the shared budget) | n/a | `store.GeneratorSpendBudget` (`store.py:348-363`), `api_v1.py:272-278` |
| Unfinalized drafts total / per client | `MIRUM_ENGINEERING_MAX_DRAFTS`, `..._MAX_DRAFTS_PER_CLIENT` | 500 / 20 | `config.py:101-103`; `PreparationQuota.admit_draft` |
| AI audit records | `MIRUM_ENGINEERING_MAX_AUDITS`, `..._MAX_AUDIT_BYTES` | 1000 / 32 MiB | `config.py:104-105` |
| Prompt length (generators) | (constant) | 2000 characters (`proposal_contract.py:41`) | `api_v1.py:249` |
| Request body | `MIRUM_ENGINEERING_MAX_BODY_BYTES` | 64 KiB | `config.py:110` |
| Dragster free-text interpreter (separate, un-ledgered route) | `MIRUM_DRAGSTER_INTERPRET_DAILY_CAP` | 200 | `config.py:69` |

Gaps (proposed follow-ups; NOT implemented):

1. Per-client AI request cap: implemented in PR #PRNUM (`store.ClientAiRequestCap`, env
   `MIRUM_ENGINEERING_AI_REQUESTS_PER_CLIENT_DAY`, rolling 24 h, SQL-derived so it survives restart; unset
   or empty = disabled, invalid = 0 = fail closed). Counts AI draft requests (one request can cost up to 2
   provider calls), never refunded.
2. The budget is per process. One backend machine today; a second machine would double the effective cap.
   Keep one machine while the flag is on, or move the budget to the shared store.
3. No cost cap in currency, only call counts. Pick the daily call cap from the provider price.
4. Legacy `/generate` reads the same key and has no call cap (comment in `e2e/compose/b13-step12.override.yml`).
   That is already true in prod today, flag or not; record it, do not fix it under this runbook.

## 4. Live check plan (owner-authorized, staging first)

- Auth id: `owner-YYYYMMDD-prod-ai-check`; tag namespace `prod-ai-check-YYYYMMDD-*` (new; never reuse B13/E05 tags).
- Budget: OWNER: choose [lead recommends 3 calls per capability, 12 total, and
  `MIRUM_ENGINEERING_AI_DAILY_CALLS=12`, `MIRUM_ENGINEERING_AI_CONCURRENCY=1` on the staging app so the
  backend itself is the hard stop; `MIRUM_DRAGSTER_INTERPRET_DAILY_CAP=0` on staging].
- Target: a throwaway staging pair (`mirum-api-staging` + `mirum-staging`, the existing deploy runbook) from the
  SAME digests as prod, with the flag on and the key set on staging only. Never prod first.
- One reviewed prompt per capability (reuse the E05 qualification prompts for regen/glider/dragster and the B13
  C01 prompt for the beam), sent through the deployed UI or `POST /engineering/v1/drafts`.
- Pass per capability: the response is a prepared draft that reaches review (or a stated clarification); no
  value appears that the prompt did not state; nothing is accepted without review; a ledger row exists for
  every call; no raw provider reply is persisted or returned.
- Stop rules: stop the whole check at the first provider error other than 429/503/504, at any unreviewed or
  unstated value, or when the call budget is used. Unavailable (429/503/504) counts as a spent call, is not
  retried, and that capability is reported unavailable.
- After the check: destroy the staging apps (their secret goes with them).

## 5. Enable steps (owner-run; written out, not executed)

1. Owner yes for this step, with the chosen caps.
2. Set the caps on `mirum-api` (environment, not secrets): `MIRUM_ENGINEERING_AI_DAILY_CALLS`,
   `MIRUM_ENGINEERING_AI_CONCURRENCY` (prod caps decided 2026-11-22: 20 and 1), plus
   `MIRUM_ENGINEERING_AI_REQUESTS_PER_CLIENT_DAY` (decided prod value: 5 requests per client per day). Setting them in `deploy/fly/backend.fly.toml` `[env]` needs a reviewed PR.
3. Add `MIRUM_ENGINEERING_AI_DRAFTS = "1"` to the same `[env]` block in that PR (an env value, not a secret).
4. Deploy the SAME backend digest with the new config:
   `fly deploy -a mirum-api -c deploy/fly/backend.fly.toml --image <current digest> --no-public-ips`.
5. `fly machine start 48e062ec41e268 -a mirum-api`, then the smoke. The current smoke script asserts
   `ai_enabled is False` (`mirum-lead/2026-11-21/smoke.py:28`), so it needs an `ai_enabled` true variant
   (follow-up) that also checks `ai_available` and makes NO provider call.
6. One owner-run prompt per capability on prod inside the authorized budget, same pass criteria as section 4.
7. Watch for a day: ledger call counts versus the cap, 503 rates, draft/audit capacity.

## 6. Disable and rollback

- Disable: remove `MIRUM_ENGINEERING_AI_DRAFTS` (or set `"0"`) and redeploy the same digest. Confirm with
  `GET /engineering/v1/capabilities` (`ai_enabled: false`) and the existing smoke (`SMOKE_OK`).
- Emergency stop without a deploy: `MIRUM_ENGINEERING_AI_DAILY_CALLS=0` stops all provider calls (`config.py:85-87`,
  "0 means no provider calls at all"), but an env change still restarts the machine; unsetting the key secret
  also stops calls and makes the AI path report unavailable.
- Image rollback: the digests in handoff 1830 section 3.

## 7. Owner decisions

1. Approve the staging live check, its auth id and the call budget (section 4).
2. Daily and in-flight call caps for prod (section 5 step 2).
3. Whether follow-up 1 (per-client AI call cap) must land before prod enablement (lead: yes).
4. Whether to keep one backend machine while the flag is on (lead: yes).
5. Whether the beam, dragster, regen and glider all go on together or one at a time (lead: all four, they share
   one budget and all passed qualification: B13 step 12, E05 run 2).
