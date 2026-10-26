"""The fixed warning wording used by every screen: one place, so no screen can skip it.

  - Every AI reply is UNVERIFIED. Its sender is a claim, its test results are
    what it says, and an independent AI review is another AI's opinion, not a
    check. Nothing an AI writes is ever labelled verified.
  - Every owner decision is a SIMULATED approval checked with a local
    passphrase. It is not connected to GitHub and does not merge or deploy.
"""

from .models import COMPLETED, IMPLEMENT, REVIEW, role_title

UNVERIFIED = "UNVERIFIED AI RESPONSE"

SIMULATED_NOTICE = (
    "Owner approvals in Dexter are SIMULATED (checked with a local passphrase only). Nothing is sent to "
    "ChatGPT or Claude Code, and nothing on GitHub is approved, merged or deployed."
)

FINAL_CHECK_REMINDER = (
    "Before you accept: open the pull request on GitHub yourself, read the change and check that the "
    "tests passed there. Dexter only knows what the AI replies claimed."
)


def reply_label(handoff, task, handoffs=()):
    """The one-line status of a recorded reply, always starting with UNVERIFIED AI RESPONSE.

    handoff: a handoff dict (Coordinator.get_handoff) with a recorded response.
    task:    the task dict (Coordinator.get_task) the handoff belongs to.
    handoffs: the task's handoffs, used to see whether a later independent review exists.
    """
    response = handoff["response"]
    sender = role_title(response["claimed_from"])
    head = f"{UNVERIFIED} (claims to be from {sender})"
    if not response["structured"]:
        return f"{head}. Unstructured: Dexter could not check its format, and it does not count as evidence."
    if task["state"] == COMPLETED:
        return (f"{head}. The owner accepted this task in Dexter (SIMULATED approval), but the reply text is "
                "still only what the AI claimed. Check the pull request and CI on GitHub yourself.")
    if handoff["action"] == IMPLEMENT:
        reviews = [h for h in handoffs if h["action"] == REVIEW and h["response"] is not None
                   and h["response"]["structured"] and _number(h["id"]) > _number(handoff["id"])]
        if reviews:
            reviewer = role_title(reviews[-1]["response"]["claimed_from"])
            return (f"{head}. Independently reviewed by {reviewer}, whose review is also an unverified AI "
                    "opinion. Nobody has checked the work itself yet.")
    return f"{head}. Nobody has independently checked it."


def unverified_banner(sender_role):
    """The block shown above any AI reply before and after it is recorded."""
    return [
        "!" * 78,
        f"! {UNVERIFIED}: claims to be from {role_title(sender_role)}",
        "! Nobody has independently checked this. Dexter cannot prove who wrote it.",
        "! Any test results are what the reply says; Dexter did not run them.",
        "!" * 78,
    ]


def _number(handoff_id):
    return int(handoff_id.split("-")[1])
