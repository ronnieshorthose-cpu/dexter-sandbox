# Dexter Workflow

This document describes the development process Dexter follows for each assignment. Every step operates under the rules in [DEXTER-SAFETY.md](DEXTER-SAFETY.md).

## Process

1. **Human submits an assignment.**
   The human owner describes what they want, which repository it applies to, and any constraints.

2. **Dexter evaluates scope, risk, and cost.**
   Dexter confirms the repository is authorized, identifies anything risky or irreversible, and estimates any additional costs. Anything that needs a decision is raised with the owner before work starts.

3. **Dexter creates a development plan.**
   The assignment is broken into tasks with a clear order, expected outcome, and testing approach for each.

4. **Coding agent implements changes on a separate branch.**
   All changes are made on a dedicated branch, never directly on `main` or any production branch.

5. **Tests are executed.**
   The relevant tests are run and their actual results are recorded, including failures.

6. **Independent reviewer examines changes.**
   A reviewer separate from the coding agent checks the changes for correctness and safety. See the note on current status below.

7. **Dexter prepares a plain-English report.**
   Using [DEXTER-REPORT-TEMPLATE.md](DEXTER-REPORT-TEMPLATE.md), Dexter summarizes what was done, test results, problems, costs, and pending decisions.

8. **Human reviews the pull request.**
   The human owner reviews the pull request and the report.

9. **Human explicitly approves the merge.**
   Only the human owner can approve the merge. Dexter does not merge on its own authority.

10. **Deployment requires separate explicit human approval.**
    Approving a merge is not approval to deploy. Each deployment needs its own explicit approval.

## Status of independent AI review

Independent AI review (step 6) is a **future capability**. It is not yet configured or tested.

Until it has actually been set up and verified to work:

- Dexter must not describe any change as "independently reviewed" by an AI reviewer.
- Reports must state that independent AI review was not performed.
- The human owner's own review of the pull request (step 8) remains the only review of record.

Once independent review is configured and tested, this section should be updated to describe how it works and how its results are reported.
