# Question 2 — write-up

## Result

| Ticket | Before (pts) | After (pts) | Graded fields |
|--------|-------------:|------------:|---------------|
| T-1001 | 73,425 | **1,482** | REFUND / returns-within-30-days ✔ |
| T-1002 | ≈ 73,000 | **1,344** | SEND_ARTICLE / digital-license-no-refund ✔ |
| T-1003 | ≈ 73,000 | **1,266** | ESCALATE_TIER2 / custom-items-no-replace ✔ |
| T-1004 | ≈ 73,000 | **1,251** | REPLACE / damage-replacement-7-days ✔ |
| T-1091 (holdout) | — | 1,287 | SEND_ARTICLE / subscription-cooling-off ✔ |

Public total: **293,325 → 5,343 points (−98%)**, at most 1,482 per ticket against the 2,500 target.
(Only T-1001 was measured individually before the change; the other "before" values are the
average of the 293,325 total over four tickets.)

The pipeline keeps its four nodes, **triage → retrieve → compose → audit**, but only compose
calls the model.

### Points after each step (one commit per step)

| Step | T-1001 | T-1002 | T-1003 | T-1004 |
|------|-------:|-------:|-------:|-------:|
| Baseline | 73,425 | | | |
| 1. Remove long-answer trigger phrases and random trace id | 46,971 | 46,341 | 46,221 | 45,996 |
| 2. Retrieve in code from the parsed articles (filler never sent) | 4,692 | 4,221 | 4,014 | 3,909 |
| 3. Triage in code, stop passing earlier answers along | 2,934 | 2,637 | 2,481 | 2,442 |
| 4. Audit as a code check, one corrective call only on failure | **1,482** | **1,344** | **1,266** | **1,251** |

## Per-call breakdown

Final pipeline, one model call per ticket (input 3 pts/token, output 15 pts/token):

| Part of the compose call | T-1001 (refund path) | T-1003 (escalation path) |
|---|---:|---:|
| System: rules + output format | 75 tok | 75 tok |
| System: top-3 retrieved policies | 92 tok | 77 tok |
| User: customer thread | 44 tok | 21 tok |
| User: order snapshot (compact JSON) | 49 tok | 50 tok |
| **Input** | **264 tok = 792 pts** | **227 tok = 681 pts** |
| **Output** (JSON answer) | **46 tok = 690 pts** | **39 tok = 585 pts** |
| **Ticket total** | **1,482** | **1,266** |
| Retrieved | returns-within-30-days, digital-license-no-refund, subscription-cooling-off | custom-items-no-replace, damage-replacement-7-days, digital-license-no-refund |
| Triage / retrieve / audit | 0 pts (code) | 0 pts (code) |

Baseline for T-1001 (four calls, 73,425 pts):

| Call | Input tok | Output tok | Points |
|---|---:|---:|---:|
| triage | 8,456 (120k chars of help center) | 485 | 32,643 |
| retrieve | 593 | 46 | 2,469 |
| compose | 1,189 (triage answer pasted twice) | 487 | 10,872 |
| audit | 6,717 (80k chars of help center) | 486 | 27,441 |

## What changed and why

1. **Removed the trigger phrases and the random trace id.** "step by step", "full reasoning",
   "restate the ticket" and "double-check" make the model pad each answer to ~480 tokens
   (7,300 pts per call at 15 pts/token). Without them every answer is about 40 tokens.
   The uuid made each triage prompt unique, so nothing could ever be cached.

2. **Retrieve is a keyword search over the parsed articles, with no model call.**
   Nearly all of the 210k-token help center is `## Internal routing note` filler that says
   it "does not change customer policy". The real policy is five `--- id ---` articles.
   Parsing out the articles and scoring them against the ticket text and set order flags
   (crudely stemmed word overlap) leaves only the top 3 to send. The filler used to be
   pasted into two calls (≈ 41k points); now it is never sent.

3. **Triage is code too.** It reads the ticket and order and builds the compact snapshot
   and the search terms. Its old output was a ~480-token model answer that was then
   pasted three more times.

4. **Audit is a code check.** It verifies the answer parses, the action is one of the four
   allowed values, and the cited article is one that was retrieved (or the named fallback).
   Only if that check fails does it make one corrective call, which tells the model what was
   wrong. The retry path was exercised with a deliberately bad draft: it recovered and cost
   1,326 pts in total. On the public tickets it never fires, so audit costs 0.

5. **Only the model's own judgement stays in the model.** Compose still decides the action
   from the ticket, the order facts and the policy text. The code never decides the outcome.

## Options considered and rejected

- **Merging all four nodes into one call.** Cheaper to describe, but the assignment asks for
  the four-node pipeline, so the nodes stay; they just don't each need a model.
- **Sending only the top-1 article.** Saves ~60 pts, but one wrong keyword ranking would
  then lose the right article. Top-3 costs about 60–90 tokens and gave the correct
  article in the top 3 on all five tickets.
- **Caching the help center (210k tokens).** Even at the cached rate of 0.3, 210,000 × 0.3 =
  63,000 pts per call. Dropping the filler is the only large saving.
- **A model-based triage or audit.** Each call costs at least ~600 pts of output alone, so
  one more call uses about a quarter of the budget for a check that code can do.

## Limits and risks

- Retrieval is keyword overlap. With five real articles that is plenty, but a help center
  with hundreds of articles needs BM25 or embeddings; the node boundary is where that
  would plug in.
- If two articles tie, the order is alphabetical by id; top-3 makes ties harmless here.
- Building the 210k-token help center in `shared/kb.py` takes 6–11 minutes because it
  recounts the whole text after every chunk. It is shared code, so I left it alone, but it
  makes the Q2 test run slow.

## Test change

`tests/test_question2.py::test_baseline_cost_is_high` asserted that one ticket costs **more**
than 70,000 points. It only guarded the unoptimised baseline, so any real optimisation fails
it. I replaced it with `test_cost_meets_target` (≤ 2,500 points for each public ticket).
The correctness and help-center-size tests are unchanged.
