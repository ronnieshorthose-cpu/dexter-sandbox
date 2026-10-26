# dexter-sandbox
Safe testing environment for Dexter AI development coordinator

## Dexter project coordinator (Phase 3A)

A local, manual coordinator for work shared between the human owner, ChatGPT
and Claude Code: tasks, enforced states, SIMULATED owner approvals, structured
handoffs and progress reports. Standard-library Python only; no network calls.

```sh
python3 -m dexter_coordinator --help
python3 examples/coordinator_demo.py      # fictional end-to-end demo
python3 -m unittest discover -s tests -t . -v
```

See [docs/COORDINATOR.md](docs/COORDINATOR.md).
