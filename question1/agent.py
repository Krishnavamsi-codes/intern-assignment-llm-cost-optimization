from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache

from shared.billing import CostSession
from shared.kb import article_ids, load_article
from shared.llm import ChatMessage, LlmClient
from shared.tools import format_thread, get_order, get_ticket


@dataclass
class TicketResolution:
    action: str
    kb_article: str
    customer_reply: str
    raw_completion: str


def parse_resolution(text: str) -> TicketResolution:
    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        raise ValueError(f"No JSON in model output:\n{text[:400]}")
    data = json.loads(m.group())
    return TicketResolution(
        action=str(data["action"]).upper(),
        kb_article=str(data["kb_article"]),
        customer_reply=str(data.get("customer_reply", "")),
        raw_completion=text,
    )


# Order fields that are identifiers only. They carry no policy information, the
# order id is already known from the ticket, and dropping them is also data
# minimisation. Everything else in the order record is sent unchanged.
_ID_FIELDS = {"order_id", "sku"}


@lru_cache
def _policies() -> str:
    """Help-center articles as one line each: `id: rule`.

    Built from data/kb at runtime, so the prompt follows the KB if articles are
    added or edited. Only formatting is removed (the repeated `# id` heading and
    markdown bold); the policy wording itself is kept.
    """
    lines = []
    for aid in article_ids():
        body = load_article(aid)
        body = re.sub(r"^#.*\n", "", body).replace("**", "")
        lines.append(f"{aid}: {' '.join(body.split())}")
    return "\n".join(lines)


@lru_cache
def _system_prompt() -> str:
    # Static text, identical for every ticket (no run id), so it could also be
    # served from a provider-side prompt cache if it ever grows past the
    # 1024-token cache threshold. Wording deliberately avoids phrases that ask
    # for long reasoning: output tokens cost 5x input tokens.
    return (
        "ShopAssist Tier-1 support. Pick one action, cite the deciding policy id.\n"
        "REFUND/REPLACE if a policy allows it; SEND_ARTICLE if one denies it; "
        "ESCALATE_TIER2 if one needs manual review or none applies "
        "(cite returns-within-30-days).\n"
        f"{_policies()}\n"
        'JSON only: {"action","kb_article","customer_reply"(one sentence)}'
    )


class SupportCopilot:
    """
    ShopAssist Tier-1 copilot (cost-optimised).

    Given a support ticket + order snapshot, choose an action, cite the help-center
    article, and draft the customer reply, in one LLM call.
    """

    def __init__(self, session: CostSession | None = None):
        self.session = session or CostSession()
        self.llm = LlmClient(self.session)

    def build_messages(self, ticket_id: str) -> list[ChatMessage]:
        ticket = get_ticket(ticket_id)
        order = get_order(ticket["order_id"])
        facts = {k: v for k, v in order.items() if k not in _ID_FIELDS}
        snapshot = json.dumps(facts, separators=(",", ":"))
        user = f"{format_thread(ticket)}\nORDER_SNAPSHOT:{snapshot}END_SNAPSHOT"
        return [ChatMessage("system", _system_prompt()), ChatMessage("user", user)]

    def handle_ticket(self, ticket_id: str) -> TicketResolution:
        messages = self.build_messages(ticket_id)
        completion = self.llm.chat("handle_ticket", messages)
        return parse_resolution(completion)
