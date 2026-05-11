"""Node skeletons for the LangGraph workflow.

Each function should be small, testable, and return a partial state update. Avoid mutating the
input state in place.
"""

from __future__ import annotations

from .state import AgentState, ApprovalDecision, Route, make_event


def intake_node(state: AgentState) -> dict:
    """Normalize raw query into state fields.

    Performs query normalization, PII detection, and metadata extraction.
    """
    import re

    query = state.get("query", "").strip()

    # Simple PII redaction: mask email-like patterns and phone numbers
    redacted = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[EMAIL]", query)
    redacted = re.sub(r"\b\d{3}[-.]?\d{3}[-.]?\d{4}\b", "[PHONE]", redacted)

    return {
        "query": redacted if redacted != query else query,
        "messages": [f"intake:{query[:40]}"],
        "events": [make_event("intake", "completed", "query normalized", pii_detected=redacted != query)],
    }


def _word_match(keyword: str, text: str) -> bool:
    """Match keyword as a whole word in text, not as substring.

    Strips punctuation and checks word boundaries to avoid false matches
    like 'it' matching inside 'item' or 'iteration'.
    """
    import re

    return bool(re.search(rf"\b{re.escape(keyword)}\b", text))


def classify_node(state: AgentState) -> dict:
    """Classify the query into a route using keyword-based heuristics.

    Priority order (highest first): risky → tool → missing_info → error → simple.
    This prevents conflicts when a query contains keywords from multiple categories.
    """
    query = state.get("query", "").strip()
    query_lower = query.lower()
    words = query_lower.split()
    clean_words = [w.strip("?!.,;:") for w in words]

    route = Route.SIMPLE
    risk_level = "low"

    # Priority 1: Risky — destructive or high-stakes actions
    risky_keywords = ["refund", "delete", "send", "cancel", "remove", "revoke"]
    if any(_word_match(kw, query_lower) for kw in risky_keywords):
        route = Route.RISKY
        risk_level = "high"

    # Priority 2: Tool — requires lookup or external data
    elif any(_word_match(kw, query_lower) for kw in ["status", "order", "lookup", "check", "track", "find", "search"]):
        route = Route.TOOL

    # Priority 3: Missing info — very short/vague queries with pronouns
    elif len(clean_words) < 5 and any(w in clean_words for w in ["it", "that", "this", "thing"]):
        route = Route.MISSING_INFO

    # Priority 4: Error — system failures and transient issues
    elif any(_word_match(kw, query_lower) for kw in ["timeout", "fail", "failure", "error", "crash", "unavailable"]):
        route = Route.ERROR

    # Priority 5: Simple — default fallback
    return {
        "route": route.value,
        "risk_level": risk_level,
        "events": [make_event("classify", "completed", f"route={route.value}")],
    }


def ask_clarification_node(state: AgentState) -> dict:
    """Ask for missing information instead of hallucinating.

    Generates a context-specific clarification question based on the query.
    """
    query = state.get("query", "")
    question = (
        f'Your request "{query[:60]}" lacks specific details. '
        "Please provide more context such as an order ID, account reference, "
        "or a clearer description of what you need help with."
    )
    return {
        "pending_question": question,
        "final_answer": question,
        "events": [make_event("clarify", "completed", "missing information requested")],
    }


def tool_node(state: AgentState) -> dict:
    """Call a mock tool.

    Simulates transient failures for error-route scenarios to demonstrate retry loops.
    TODO(student): implement idempotent tool execution and structured tool results.
    """
    attempt = int(state.get("attempt", 0))
    if state.get("route") == Route.ERROR.value and attempt < 2:
        result = f"ERROR: transient failure attempt={attempt} scenario={state.get('scenario_id', 'unknown')}"
    else:
        result = f"mock-tool-result for scenario={state.get('scenario_id', 'unknown')}"
    return {
        "tool_results": [result],
        "events": [make_event("tool", "completed", f"tool executed attempt={attempt}")],
    }


def risky_action_node(state: AgentState) -> dict:
    """Prepare a risky action for approval.

    Creates a proposed action with risk justification based on query keywords.
    """
    query = state.get("query", "")
    query_lower = query.lower()

    if "refund" in query_lower:
        action_desc = "Process customer refund — financial impact, requires approval"
    elif "delete" in query_lower:
        action_desc = "Delete customer account — irreversible data loss, requires approval"
    elif "cancel" in query_lower:
        action_desc = "Cancel subscription/order — service impact, requires approval"
    elif "send" in query_lower:
        action_desc = "Send external communication — reputation risk, requires approval"
    else:
        action_desc = "High-risk operation — requires supervisory approval"

    return {
        "proposed_action": action_desc,
        "events": [make_event("risky_action", "pending_approval", action_desc)],
    }


def approval_node(state: AgentState) -> dict:
    """Human approval step with optional LangGraph interrupt().

    Set LANGGRAPH_INTERRUPT=true to use real interrupt() for HITL demos.
    Default uses mock decision so tests and CI run offline.

    TODO(student): implement reject/edit decisions and timeout escalation.
    """
    import os

    if os.getenv("LANGGRAPH_INTERRUPT", "").lower() == "true":
        from langgraph.types import interrupt

        value = interrupt({
            "proposed_action": state.get("proposed_action"),
            "risk_level": state.get("risk_level"),
        })
        if isinstance(value, dict):
            decision = ApprovalDecision(**value)
        else:
            decision = ApprovalDecision(approved=bool(value))
    else:
        decision = ApprovalDecision(approved=True, comment="mock approval for lab")
    return {
        "approval": decision.model_dump(),
        "events": [make_event("approval", "completed", f"approved={decision.approved}")],
    }


def retry_or_fallback_node(state: AgentState) -> dict:
    """Record a retry attempt or fallback decision.

    TODO(student): implement bounded retry, exponential backoff metadata, and fallback route.
    """
    attempt = int(state.get("attempt", 0)) + 1
    errors = [f"transient failure attempt={attempt}"]
    return {
        "attempt": attempt,
        "errors": errors,
        "events": [make_event("retry", "completed", "retry attempt recorded", attempt=attempt)],
    }


def answer_node(state: AgentState) -> dict:
    """Produce a final response grounded in tool_results and approval context."""
    tool_results = state.get("tool_results", [])
    approval = state.get("approval")
    route = state.get("route", "")

    if tool_results:
        latest_result = tool_results[-1]
        if approval and approval.get("approved"):
            answer = f"Approved action completed. Result: {latest_result}"
        else:
            answer = f"I found: {latest_result}"
    elif route == Route.SIMPLE.value:
        answer = "Your request has been processed. For further assistance, please provide additional details."
    else:
        answer = "Your request has been handled. Thank you for contacting support."

    return {
        "final_answer": answer,
        "events": [make_event("answer", "completed", "answer generated")],
    }


def evaluate_node(state: AgentState) -> dict:
    """Evaluate tool results — the 'done?' check that enables retry loops.

    TODO(student): replace heuristic with LLM-as-judge or structured validation.
    """
    tool_results = state.get("tool_results", [])
    latest = tool_results[-1] if tool_results else ""
    if "ERROR" in latest:
        return {
            "evaluation_result": "needs_retry",
            "events": [make_event("evaluate", "completed", "tool result indicates failure, retry needed")],
        }
    return {
        "evaluation_result": "success",
        "events": [make_event("evaluate", "completed", "tool result satisfactory")],
    }


def dead_letter_node(state: AgentState) -> dict:
    """Log unresolvable failures for manual review.

    Third layer of error strategy: retry -> fallback -> dead letter.
    TODO(student): persist to dead-letter queue, alert on-call, or create support ticket.
    """
    return {
        "final_answer": "Request could not be completed after maximum retry attempts. Logged for manual review.",
        "events": [make_event("dead_letter", "completed", f"max retries exceeded, attempt={state.get('attempt', 0)}")],
    }


def finalize_node(state: AgentState) -> dict:
    """Finalize the run and emit a final audit event."""
    return {"events": [make_event("finalize", "completed", "workflow finished")]}
