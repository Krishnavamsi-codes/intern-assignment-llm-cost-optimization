from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from shared.billing import CostSession
from shared.kb import expanded_help_center_blob
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket

from question1.agent import parse_resolution

# Cited when no policy applies (named in the action guide, so always allowed).
FALLBACK_ARTICLE = "returns-within-30-days"
TOP_K = 3
# Identifier fields: no policy meaning, so they are not sent to the model.
_ID_FIELDS = {"order_id", "sku"}

# One help-center article: `--- id ---` up to the next article or `## ` section.
_ARTICLE = re.compile(r"^--- ([a-z0-9-]+) ---\n(.*?)(?=^--- |^## |\Z)", re.M | re.S)
_STOP = set(
    "a an the and or of to for in on is are be been has have i my me you your it its "
    "this that with was were not no if after any can".split()
)


def _terms(text: str) -> set[str]:
    """Lower-case content words, crudely stemmed to 5 letters (refund/refunded)."""
    return {
        w[:5]
        for w in re.findall(r"[a-z]+", text.lower())
        if len(w) >= 3 and w not in _STOP
    }


@lru_cache
def _article_index(help_center: str) -> dict[str, str]:
    """Parse the help center into {article_id: policy text}.

    Only sections with an article id are kept. The `## Internal routing note`
    batches (about 210k tokens of the blob) say themselves that they do not change
    customer policy, so they never reach a prompt.
    """
    index = {}
    for aid, body in _ARTICLE.findall(help_center):
        body = re.sub(r"^#.*\n", "", body).replace("**", "")
        index[aid] = " ".join(body.split())
    return index


@dataclass
class TicketResolution:
    action: str
    kb_article: str


@dataclass
class Triage:
    thread: str
    snapshot: str
    query: set[str]


class SupportPipeline:
    """Same product as Question 1: triage, retrieve, compose, audit.

    Triage and retrieve are deterministic (parsing and keyword search, like a
    search index in production); compose and audit call the LLM.
    """

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)
        self.help_center = expanded_help_center_blob()
        self.articles = _article_index(self.help_center)

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        triage = self._triage(ticket_id)
        retrieved = self._retrieve(triage)
        draft = self._compose(triage, retrieved)
        final = self._audit(triage, retrieved, draft)
        return TicketResolution(final.action, final.kb_article)

    def _triage(self, ticket_id: str) -> Triage:
        """Load the ticket and order, build the compact snapshot and search terms."""
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        facts = {k: v for k, v in order.items() if k not in _ID_FIELDS}
        thread = format_thread(ticket)
        # Search on what the customer wrote plus the order facts that are set
        # (product type, title, and true flags such as "license revealed").
        signals = [str(order.get("title", "")), str(order.get("product_type", ""))]
        signals += [k.replace("_", " ") for k, v in order.items() if v is True]
        return Triage(
            thread=thread,
            snapshot=json.dumps(facts, separators=(",", ":")),
            query=_terms(thread + " " + " ".join(signals)),
        )

    def _retrieve(self, triage: Triage) -> dict[str, str]:
        """Top-k articles by term overlap with the ticket, ties broken by id."""
        ranked = sorted(
            self.articles.items(),
            key=lambda kv: (-len(triage.query & _terms(kv[0].replace("-", " ") + " " + kv[1])), kv[0]),
        )
        return dict(ranked[:TOP_K])

    def _compose(self, triage: Triage, retrieved: dict[str, str]) -> str:
        policies = "\n".join(f"{aid}: {text}" for aid, text in retrieved.items())
        system = (
            "ShopAssist Tier-1 support. Pick one action, cite the deciding policy id.\n"
            "REFUND/REPLACE if a policy allows it; SEND_ARTICLE if one denies it; "
            "ESCALATE_TIER2 if one needs manual review or none applies "
            f"(cite {FALLBACK_ARTICLE}).\n"
            f"{policies}\n"
            'JSON only: {"action","kb_article","customer_reply"(one sentence)}'
        )
        user = f"{triage.thread}\nORDER_SNAPSHOT:{triage.snapshot}END_SNAPSHOT"
        return self.llm.chat("compose", [ChatMessage("system", system), ChatMessage("user", user)])

    def _audit(self, triage: Triage, retrieved: dict[str, str], draft: str):
        policies = "\n".join(f"{aid}: {text}" for aid, text in retrieved.items())
        messages = [
            ChatMessage(
                "system",
                "Audit node: check the draft against the policies; output the final JSON only.\n"
                + policies,
            ),
            ChatMessage(
                "user",
                f"{triage.thread}\nORDER_SNAPSHOT:{triage.snapshot}END_SNAPSHOT\nDRAFT:{draft}",
            ),
        ]
        return parse_resolution(self.llm.chat("audit", messages))
