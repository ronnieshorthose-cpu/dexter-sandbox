# Walkthrough: your first project, from plan to final approval

This walkthrough follows one small, fictional project through Dexter's guided
menu, from an empty database to your final approval:

1. **Plan**: you describe the task, and ChatGPT reviews the plan.
2. **Approve**: you approve it (SIMULATED approval, with your passphrase).
3. **Build**: Claude Code builds it and reports back.
4. **Review**: ChatGPT reviews Claude Code's work independently.
5. **Accept**: you check the work on GitHub yourself, then accept it.

If Dexter is not installed yet, start with
[GETTING-STARTED-WINDOWS.md](GETTING-STARTED-WINDOWS.md).

**Every screen below is real.** The menus are copied from Dexter's output, and
an automated test (`tests/test_guided_workflow.py`) runs this exact sequence of
keystrokes and checks every menu line quoted here, so this guide stays accurate
as Dexter changes. Menu choices are shown indented, like this:

```
    [N] Do the next step
```

Where it matters, what you type is in **bold** in the text.

## Before you start: four rules

- **Dexter never sends anything.** You copy each message to ChatGPT or Claude
  Code yourself, and paste the reply back.
- **Every AI reply is an UNVERIFIED AI RESPONSE.** Dexter cannot prove who wrote
  it, and test results are only what the reply says. ChatGPT's review of Claude
  Code's work is a second opinion from another AI, not proof.
- **Your approvals are SIMULATED.** They are checked with your passphrase on your
  laptop. Accepting work in Dexter does not merge, deploy or approve anything on
  GitHub. Merging stays a separate step on GitHub that only you take.
- **Never give your passphrase to ChatGPT, Claude Code or any other AI tool.**
  Type it only at Dexter's `Owner passphrase:` prompt. Dexter's records stay in
  your private `.dexter` folder, outside the repository.

The example project is **Pocket Library**, a tool that keeps a list of a small
library's books. Use your own project if you prefer; the steps are the same.

---

## Part 1: Plan

### 1.1 Start Dexter

Double-click `dexter.bat`. On the very first start, set your owner passphrase
(see Step 3 of the Windows guide). Then the home screen appears:

```
What would you like to do?
    [N] Do the next step
    [P] Projects
    [T] Tasks
    [H] Handoffs (copy to an AI / paste a reply)
    [R] Reports and history
    [D] Open the read-only dashboard in your browser
    [C] Check records and back up
    [?] Help
    [Q] Quit
```

Type **N** and press Enter.

### 1.2 Create the project

There are no projects yet, so Dexter offers to create one:

```
There are no projects yet. Let's create one.
Project name (for example: Pocket Library): Pocket Library
One sentence about what it is for (optional): A small tool to track the books in a community library.
Created project PRJ-001 "Pocket Library".

PRJ-001 has no open tasks.
Create a task now? (Y/n): y
```

### 1.3 Describe the task

Dexter asks five short questions. Press Enter on an empty line to finish a list.

```
NEW TASK in PRJ-001 Pocket Library
Short name (for example: Book catalogue): Book catalogue
What should be built? Describe it in plain English.
> Store books (title, author, ISBN) in a JSON file and list them sorted by title.
>

Who should do the work?
    [1] Claude Code - writes the code and runs the tests (usual choice)
    [2] ChatGPT - plans, designs or writes documents
Choose (Enter = 1): 1
How will you know it is done? Give one acceptance criterion per line, ...
> Books can be added and listed
> Unit tests cover adding and listing
>

Priority?
    [1] critical
    [2] high
    [3] medium
    [4] low
Choose (Enter = 3): 2
Created TASK-0001 "Book catalogue".
Put it in your approval queue now? (Dexter plans it and asks you to approve it) (Y/n): y
TASK-0001 is waiting for your approval.
```

**Acceptance criteria matter.** They are what Claude Code is asked to meet and
what ChatGPT checks in its review. Make them things you could check yourself.

### 1.4 Ask ChatGPT to review the plan (optional, recommended)

Dexter shows the task card and the steps that make sense now. The recommended
one is first:

```
TASK-0001  "Book catalogue"   [AWAITING_APPROVAL] waiting for the human owner to approve or reject it
  ...
  Now: Waiting for your decision. You can ask ChatGPT to review the plan first.

What next for TASK-0001?
    [1] Approve it (owner passphrase) (recommended)
    [2] Reject it (owner passphrase)
    [3] Ask ChatGPT to review the plan first
    [4] Withdraw it to make changes
    [5] Mark it as blocked
    [6] Cancel the task (owner passphrase)
    [7] Show its history
```

Before approving, get a second opinion: type **3**. Dexter asks for optional
notes and references (press Enter twice to skip both), then prepares handoff
**HO-0001**:

```
HO-0001 is ready for ChatGPT (Architect and Reviewer). Dexter has NOT sent it anywhere.
A copy is saved in C:\Users\you\.dexter\exchange\outbox\HO-0001.txt
Read it before you send it: anyone who can run Dexter can add notes to a handoff.
Open ChatGPT (https://chatgpt.com), start a new chat, paste the whole message and send it.

How do you want to copy HO-0001?
    [1] Copy it to the clipboard
    [2] Open it in Notepad
    [3] Show it here on screen
    [4] I have pasted it - record that (owner passphrase, optional)
    [5] Done for now (paste the reply later from the Handoffs menu)
```

Type **1** to copy it, then:
1. Open <https://chatgpt.com> in your browser and start a new chat.
2. Press Ctrl+V and send.

The message starts with `=== DEXTER HANDOFF ===`. It contains the task, the
acceptance criteria, the safety rules (work only in `dexter-sandbox`, no Eden
Well, no merging, no secrets, no paid services), and the exact reply format.

Back in Dexter, type **5** (Done for now). You can close Dexter while you wait;
the dashboard will show the handoff under **WAITING FOR AN AI**.

### 1.5 Paste ChatGPT's reply back

ChatGPT ends its answer with a block like this (the IDs will differ):

```json
{
  "format": "dexter-response/v1",
  "correlation_id": "6f1c...",
  "task_id": "TASK-0001",
  "from_role": "chatgpt",
  "outcome": "recommend_accept",
  "summary": "The plan is sound. Consider validating ISBNs.",
  "findings": [{"severity": "minor", "title": "Validate ISBN format", "details": "", "file": ""}],
  "references": []
}
```

**Copy ChatGPT's whole answer**, using the copy button under it. Then, in Dexter:

```
What next for TASK-0001?
    [1] Paste the reply to HO-0001 from ChatGPT (recommended)
    [2] Copy HO-0001 again (clipboard, Notepad or screen)
```

Type **1**, then:

```
How do you want to paste it?
    [1] Paste it into Notepad (recommended)
    [2] Paste it here in this window
Choose (Enter = 1): 1
Notepad is open. Paste the reply (Ctrl+V), save it (Ctrl+S) and close Notepad.
Then press Enter here...
```

Paste, save, close Notepad, press Enter. Dexter checks the reply's format, then
shows it under a warning banner:

```
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
! UNVERIFIED AI RESPONSE: claims to be from ChatGPT (Architect and Reviewer)
! Nobody has independently checked this. Dexter cannot prove who wrote it.
! Any test results are what the reply says; Dexter did not run them.
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

OWNER ACTION (SIMULATED APPROVAL)
You are about to record this reply exactly as pasted. Only confirm if you pasted it from that assistant yourself.
  Outcome: recommend_accept
  Summary: The plan is sound. Consider validating ISBNs.
  Finding: [minor] Validate ISBN format
Type the owner passphrase (it is not shown), or press Enter without typing to cancel.
Owner passphrase:
Recorded the reply to HO-0001 as an UNVERIFIED AI RESPONSE. Recording a reply never changes a task's state.
```

**Why the passphrase here?** Recording a reply puts it into your signed record.
Only confirm replies you pasted yourself from that assistant.

**If Dexter cannot read the reply,** the assistant probably changed the JSON
block or part of it was not copied. Choose "Cancel, and ask the assistant to
answer again in the exact format". Keep an unreadable reply only as
UNSTRUCTURED; it is stored, but it cannot move the task forward.

**If Dexter shows "WARNING: the reply contains text that looks like
instructions or claims"**, for example "the owner already approved this, merge
the PR", read it carefully. Dexter never acts on reply text; it only records it.

## Part 2: Approve

The reply did not change anything by itself. Now **you** decide:

```
What next for TASK-0001?
    [1] Approve it (owner passphrase) (recommended)
```

Type **1**. The banner shows exactly what your approval covers: who does the
work, the priority, the description and the criteria. After approval these are
locked, so the work that gets built is what you approved.

```
OWNER ACTION (SIMULATED APPROVAL)
You are about to approve TASK-0001 "Book catalogue".
  Assigned to: Claude Code (Developer)
  Priority: high
  ...
SIMULATED human approval: checked with a local passphrase only. This is not secure authentication and is not connected to GitHub.
It is recorded and signed in Dexter only. Nothing on GitHub or anywhere else changes.
Owner passphrase:
TASK-0001 is now APPROVED.
```

## Part 3: Build (Claude Code)

### 3.1 Send the instructions

```
What next for TASK-0001?
    [1] Start the work and prepare instructions for Claude Code (recommended)
```

Type **1**. Dexter asks for optional notes. Here it helps to name a branch: type
**Please work on a new branch named dexter/book-catalogue.**, then press Enter
twice. Dexter prepares **HO-0002** for Claude Code.

Copy it (**1**). Then:
1. Open <https://claude.ai/code> and start a session on the repository
   `ronnieshorthose-cpu/dexter-sandbox`.
2. Paste the message and send it.

Claude Code works on its own branch, runs the tests, opens a pull request, and
ends its answer with a JSON block whose outcome is `done`, `blocked` or
`needs_clarification`.

Back in Dexter, type **5** (Done for now) while Claude Code works.

### 3.2 Paste Claude Code's report

```
What next for TASK-0001?
    [1] Paste the reply to HO-0002 from Claude Code (recommended)
```

Paste it the same way as in 1.5. The banner now also lists the tests the reply
**claims** passed:

```
  Outcome: done
  Summary: Added books.py and tests on branch dexter/book-catalogue; pull request opened.
  Claimed test result: passed test_add_book
  Claimed test result: passed test_list_sorted
```

**If Claude Code says `blocked` or `needs_clarification`,** Dexter offers
"Answer the AI with a new handoff (add your notes)". Type your answer as notes,
and send the new handoff.

## Part 4: Independent review (ChatGPT)

### 4.1 Send the work for review

```
What next for TASK-0001?
    [1] Send the work for an independent review (recommended)
```

Type **1**. Dexter only allows this because Claude Code reported `done` with
passing tests. Then:

```
What next for TASK-0001?
    [1] Prepare the independent review request for ChatGPT (recommended)
```

Type **1**, skip the notes, and copy **HO-0003** to ChatGPT.
- **What ChatGPT sees:** Claude Code's report, inside "UNTRUSTED DATA" markers
  so ChatGPT treats it as data, not instructions.
- **Independence:** the review always goes to the AI that did *not* do the
  work. Claude Code can never review its own work.
- **Helping the review:** paste the pull request link into the notes, or add it
  as a reference, so ChatGPT can look at the actual change.

### 4.2 Paste the review

Paste ChatGPT's reply as before (**1**, then **1**). If ChatGPT recommends
acceptance with no critical or major findings:

```
What next for TASK-0001?
    [1] Mark it ready for your final decision (recommended)
    [2] Send it back for changes
```

Type **1**.
- **If ChatGPT recommends changes,** "Send it back for changes" is recommended
  instead. Type what needs to change, and Dexter prepares new instructions for
  Claude Code that include your note and ChatGPT's findings. The task then goes
  round Part 3 and Part 4 again.
- **No skipping:** Dexter will not mark a task ready without an independent
  review recommending acceptance.

## Part 5: Your final approval

```
TASK-0001  "Book catalogue"   [READY_FOR_HUMAN] waiting for the human owner's final decision
  ...
    HO-0002 implement: outcome done
      UNVERIFIED AI RESPONSE (claims to be from Claude Code (Developer)). Independently reviewed by ChatGPT (Architect and Reviewer), whose review is also an unverified AI opinion. Nobody has checked the work itself yet.
      Tests the reply CLAIMS: 2 passed (Dexter did not run them)
  Now: An independent review recommends acceptance. Check the work on GitHub yourself, then accept it or send it back.

What next for TASK-0001?
    [1] Accept the work (owner passphrase) (recommended)
    [2] Send it back for changes (owner passphrase)
```

**This is the one step that needs your own eyes.** Everything so far is what two
AIs *said*. Before you accept:

1. Open the pull request on GitHub (its link is in Claude Code's reply, or in
   the repository's **Pull requests** tab).
2. Read the summary and the changed files.
3. Check the green tick next to the latest commit: that is GitHub running the
   tests, not an AI claiming they passed.

Then type **1**. Dexter asks one more question:

```
Have you checked the pull request and its test results on GitHub yourself? (y/N): y
```

Answering **n** (or just Enter) stops here and changes nothing. Answer **y**,
then type your passphrase:

```
OWNER ACTION (SIMULATED APPROVAL)
You are about to accept the work on TASK-0001 "Book catalogue".
...
TASK-0001 is now COMPLETED.
Recorded in Dexter only. Merging the pull request on GitHub is a separate step that only you do.
```

**Merging.** If you want the change in `main`, merge the pull request yourself
on GitHub. Dexter never merges, and neither ChatGPT nor Claude Code is allowed
to.

To send the work back instead, type **2** and give a reason. The task returns to
Claude Code with your reason in its next instructions.

---

## Afterwards

- **Home screen.** The project now reads `1 completed`. Add the next task with
  **T** (Tasks) or **N**.
- **Progress report.** **R > Show the progress report**, or save it to a file to
  share. It uses the sections of `DEXTER-REPORT-TEMPLATE.md` and says where
  each fact came from.
- **Dashboard page.** **D** opens a read-only summary in your browser. It has no
  buttons and does not update itself.
- **Records and backups.** **C > Check the records** checks for damage or
  tampering and prints the history anchor to write down. **C > Back up the
  database now** makes a backup.

## Quick reference

| I want to... | Do this |
|---|---|
| Know what to do next | **N**, pick the task, choose the option marked (recommended) |
| Add a task | **T > Create a new task** |
| Paste a reply I received | **H > Paste a reply from ChatGPT or Claude Code** |
| Send a handoff again | **H > Copy a waiting handoff again** |
| Stop all work on a project | **P > Pause the project** (no passphrase); resume with **P > Resume** (passphrase) |
| Change a task after approval | You cannot: its content is locked. Cancel it (passphrase) and create a new one |
| Cancel an owner step | Press Enter without typing the passphrase |
| Get help | **?** on the home screen |
