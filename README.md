# dexter-sandbox
Safe testing environment for Dexter AI development coordinator

## Start here (Phase 3B guided menu)

Dexter coordinates work between you (the owner), ChatGPT and Claude Code. It
keeps tasks and approvals, prepares the messages you copy to each assistant, and
records their replies, which are always labelled UNVERIFIED. It runs only on
your computer: no network calls, no paid services. Owner approvals are
SIMULATED (a local passphrase), and nothing is ever merged or deployed.

- **Windows:** double-click `dexter.bat`. Setup guide:
  [docs/GETTING-STARTED-WINDOWS.md](docs/GETTING-STARTED-WINDOWS.md)
- **Your first project, step by step:** [docs/WALKTHROUGH.md](docs/WALKTHROUGH.md)
- **macOS or Linux:** `python3 -m dexter_coordinator start`

```sh
python3 -m dexter_coordinator start              # guided menu (recommended)
python3 -m dexter_coordinator dashboard --html   # read-only status page in your browser
python3 -m dexter_coordinator --help             # every command
python3 examples/coordinator_demo.py             # fictional end-to-end demo
python3 -m unittest discover -s tests -t . -v    # tests
```

Dexter keeps its database, handoff files, replies and backups in `~/.dexter`
(on Windows `C:\Users\you\.dexter`), outside this repository.

## More documentation

- [docs/COORDINATOR.md](docs/COORDINATOR.md): every command, task states and rules
- [docs/COORDINATOR-SECURITY.md](docs/COORDINATOR-SECURITY.md): security design and limits
- [docs/PHASE-3B-REPORT.md](docs/PHASE-3B-REPORT.md): what Phase 3B added, tests and limitations
- [docs/PHASE-3A-REPORT.md](docs/PHASE-3A-REPORT.md): the Phase 3A coordinator
