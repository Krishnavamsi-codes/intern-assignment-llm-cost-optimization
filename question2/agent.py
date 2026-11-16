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

TOP_K = 3

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


class SupportPipeline:
    """Same product as Question 1 — triage, retrieve, compose, audit."""

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)
        self.help_center = expanded_help_center_blob()
        self.articles = _article_index(self.help_center)

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        thread = format_thread(ticket)
        snapshot = json.dumps(order, indent=2)

        triage = self._triage(ticket_id, thread, snapshot)
        retrieved = self._retrieve(thread, order)
        draft = self._compose(ticket_id, thread, snapshot, triage, retrieved)
        final = self._audit(ticket_id, thread, snapshot, triage, retrieved, draft)
        parsed = parse_resolution(final)
        return TicketResolution(parsed.action, parsed.kb_article)

    def _triage(self, ticket_id: str, thread: str, snapshot: str) -> str:
        messages = [
            ChatMessage(
                "system",
                "Triage node: list candidate kb articles for this ticket. JSON only.",
            ),
            ChatMessage(
                "user",
                f"TICKET {ticket_id}\nTHREAD:\n{thread}\nORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
            ),
        ]
        return self.llm.chat("triage", messages)

    def _retrieve(self, thread: str, order: dict) -> str:
        """Keyword search over the parsed articles (no LLM call), top-k as text."""
        signals = [str(order.get("title", "")), str(order.get("product_type", ""))]
        signals += [k.replace("_", " ") for k, v in order.items() if v is True]
        query = _terms(thread + " " + " ".join(signals))
        ranked = sorted(
            self.articles.items(),
            key=lambda kv: (-len(query & _terms(kv[0].replace("-", " ") + " " + kv[1])), kv[0]),
        )
        return "\n".join(f"{aid}: {text}" for aid, text in ranked[:TOP_K])

    def _compose(
        self,
        ticket_id: str,
        thread: str,
        snapshot: str,
        triage: str,
        retrieved: str,
    ) -> str:
        messages = [
            ChatMessage(
                "system",
                "Compose node: JSON only with action, kb_article, customer_reply (one sentence).",
            ),
            ChatMessage(
                "user",
                "\n\n".join(
                    [
                        f"TICKET {ticket_id}",
                        f"THREAD:\n{thread}",
                        f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
                        f"TRIAGE:\n{triage}",
                        f"ARTICLES:\n{retrieved}",
                        f"TRIAGE AGAIN:\n{triage}",
                    ]
                ),
            ),
        ]
        return self.llm.chat("compose", messages)

    def _audit(
        self,
        ticket_id: str,
        thread: str,
        snapshot: str,
        triage: str,
        retrieved: str,
        draft: str,
    ) -> str:
        messages = [
            ChatMessage(
                "system",
                "Audit node: check the draft against policy and output the final JSON only.",
            ),
            ChatMessage(
                "user",
                "\n\n".join(
                    [
                        f"TICKET {ticket_id}",
                        f"THREAD:\n{thread}",
                        f"ORDER_SNAPSHOT:\n{snapshot}\nEND_SNAPSHOT",
                        f"DRAFT:\n{draft}",
                        f"TRIAGE:\n{triage}",
                        f"ARTICLES:\n{retrieved}",
                    ]
                ),
            ),
        ]
        return self.llm.chat("audit", messages)
