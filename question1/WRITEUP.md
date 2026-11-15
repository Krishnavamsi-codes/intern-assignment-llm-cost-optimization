# Question 1 — write-up

## Result

| Ticket | Before (pts) | After (pts) | Input tok | Output tok | Graded fields |
|--------|-------------:|------------:|----------:|-----------:|---------------|
| T-1001 | 8,880 | **1,635** | 315 | 46 | REFUND / returns-within-30-days ✔ |
| T-1002 | 8,808 | **1,497** | 304 | 39 | SEND_ARTICLE / digital-license-no-refund ✔ |
| T-1003 | 8,736 | **1,464** | 293 | 39 | ESCALATE_TIER2 / custom-items-no-replace ✔ |
| T-1004 | 8,880 | **1,425** | 295 | 36 | REPLACE / damage-replacement-7-days ✔ |
| T-1091 (holdout) | — | 1,464 | 298 | 38 | SEND_ARTICLE / subscription-cooling-off ✔ |

Public total: **35,532 → 6,021 points (−83%)**, about 1,505 per ticket against the 3,000 target.
Still one LLM call per ticket, and the output still parses through the unchanged `parse_resolution`.

### Points after each step (one commit per step on this branch)

| Step | T-1001 | T-1002 | T-1003 | T-1004 |
|------|-------:|-------:|-------:|-------:|
| Baseline | 8,880 | 8,808 | 8,736 | 8,880 |
| 1. Drop verbose-reasoning instruction + run id | 2,151 | 2,022 | 1,983 | 1,944 |
| 2. Action guide instead of few-shot, compact policies | 1,848 | 1,719 | 1,680 | 1,641 |
| 3. Compact order snapshot, no identifier fields | 1,698 | 1,560 | 1,527 | 1,488 |
| 4. Tighter system prompt wording | **1,635** | **1,497** | **1,464** | **1,425** |

Reproduce any row with `python scripts/cost_breakdown.py q1 T-1001 --sections`.
(Baseline numbers vary by about ±30 between runs because the random run id changes length.)

## Per-call breakdown — T-1001

| Part | Tokens | Rate | Points |
|------|-------:|-----:|-------:|
| System: role + action guide + output format | 75 | 3 | 225 |
| System: 5 policies, one line each | 143 | 3 | 429 |
| User: customer thread | 44 | 3 | 132 |
| User: order snapshot (compact JSON) | 49 | 3 | 147 |
| Role headers (`SYSTEM:` / `USER:`) | 4 | 3 | 12 |
| **Input total** | **315** | | **945** |
| Output: JSON answer | 46 | 15 | **690** |
| **Call total** | | | **1,635** |

The output (about 40% of the remaining cost) is written by the model in a fixed
format, including `customer_reply`, so it's the floor. What's left to cut is input.

Baseline for comparison: 530 input tokens (1,590 pts) + 486 output tokens (7,290 pts) = 8,880.

## What changed and why

1. **Removed the "think step by step / full reasoning / quote any article" instruction.**
   This was 82% of the bill. Output is 15 pts/token, five times the input price, and that wording
   made the model write about 480 tokens of reasoning that the product throws away (only
   `action` and `kb_article` are used). Asking for "JSON only, one-sentence reply" brings the answer
   to about 40 tokens. **Saves about 6,600 pts per ticket.** Trade-off: no reasoning trace for
   debugging. If that's needed, log it on a sample of tickets, not on every call.

2. **Dropped the random `Run id {uuid}` from the start of the system prompt.**
   It gives the model nothing and makes every prompt unique, so a prompt cache can never hit.
   Removing it saves 20 tokens and leaves a byte-identical static system prompt. At today's
   size (239 tokens) that prompt is below the 1,024-token cache minimum, but if the policy list
   grows past it the cache starts working with no code change.

3. **Replaced the 4 few-shot examples with a two-line action guide.**
   The examples (111 tokens) encoded the rule for choosing an action. The guide says the same
   thing more generally: allowed → REFUND/REPLACE, denied → SEND_ARTICLE, manual review or no
   match → ESCALATE_TIER2 citing `returns-within-30-days`. It's one rule instead of four
   memorised cases, so it also covers tickets the examples never showed (e.g. the subscription
   holdout).

4. **Kept all five policies but stripped their formatting.**
   I deliberately did **not** send only the "right" article: picking it before the call would
   mean deciding the ticket in code, which defeats the point of the LLM call. Instead the
   articles are loaded from `data/kb` at runtime and reduced to `id: rule`, removing the
   duplicated `# id` heading, the `--- id ---` separators and the markdown bold. The policy
   wording is unchanged and new KB articles appear automatically. 195 → 143 tokens.
   A last pass tightened the wording around the rules (same meaning): system prompt 239 → 218 tokens.

5. **Order snapshot as compact JSON, without identifier fields.**
   `json.dumps(..., separators=(",", ":"))` carries the same data without indentation
   whitespace. `order_id` and `sku` are dropped: they're pure identifiers with no policy
   meaning (and less customer data sent to a model is good practice). Every field that could
   affect a decision (type, days, opened, license, customized, damage, subscription age) is
   still sent. 87 → 49 tokens.

6. **Dropped `TICKET_ID` from the prompt.** The model doesn't need it; the code already knows
   which ticket it's handling.

## Options considered and rejected

- **Deciding in Python and skipping the LLM (0 points).** It would beat any target, but the
  assignment is "one LLM call per ticket". It would also put a second copy of the policies in
  code that drifts from the KB. Rejected.
- **Sending only the snapshot.** The local simulator only reads `ORDER_SNAPSHOT`, so this would
  score lower. But a real model can't apply policies it hasn't seen or understand a request it
  hasn't read. That would be optimising against the simulator, not the product. Rejected; the
  thread and policies stay.
- **Padding the static prompt to 1,024 tokens to qualify for caching.** Once cached, a
  1,024-token prefix costs 1,024 × 0.3 ≈ 307 pts per call, less than today's uncached
  system prompt (218 × 3 = 654). But the first call can't be a cache hit, so it pays
  1,024 × 3 = 3,072 for the prefix alone, which breaks the 3,000 target on that ticket.
  Over N tickets the prefix averages (3,072 + 307·(N−1)) / N, which beats 654 only when
  N ≥ 8. Worth it at production volume; not for a per-ticket target checked on 4 tickets.
  (The local simulator applies the cached rate even on the first call if `cached_prefix` is
  passed. Relying on that would score lower here but would not hold on a real API, so I
  didn't use it.)
- **Leaving `false` fields out of the snapshot (≈ −60 pts).** For a real model, a missing
  `opened` isn't the same as `opened: false`, and could lead to refunding an opened item.
- **Dropping agent messages from the thread (−45 pts on T-1001).** The one here is filler, but
  agent messages can carry facts ("photo received"), so filtering them is a bad general rule.
- **Shrinking the output further.** The output schema (including `customer_reply`) comes from the
  model/simulator; about 40 tokens is the floor for a non-verbose answer. Removing
  `customer_reply` from the requested format would also drop a product feature.

## Test change

`tests/test_question1.py::test_baseline_cost_is_high` asserted that total cost is **above**
15,000. It only checked that the unoptimised baseline was expensive, so any real optimisation
fails it. I replaced it with `test_cost_meets_target`, which asserts ≤ 3,000 points for each
public ticket. The correctness tests (public tickets + T-1091 holdout) are unchanged and pass.
