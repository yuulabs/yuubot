# Issue Notes

## Possible timing-sensitive E2E concurrency issue

Date: 2026-07-06

Snapshot commit:
`a39ce413e546c4333f66f62f30d9c2aa73396ef8`

Observed while creating a clean benchmark snapshot from `git archive HEAD`.

Scenario trace:

```text
Clean archive snapshot
  -> `uv run pytest -q`
    -> shared E2E server and async conversation tests run as a full suite
      -> `test_http_tasks_docs_in_prompt_enable_submit_call` sends a websocket command
        -> test waits for conversation history to reach `gen_text`
          -> history did not reach `gen_text` within 200 polling attempts
            -> full suite reported 1 failed, 126 passed in 41.48s
```

Follow-up observations:

- The failed test passed when rerun alone:
  `uv run pytest tests/test_llm_conditioned_e2e.py::test_http_tasks_docs_in_prompt_enable_submit_call -q`
  -> `1 passed in 3.69s`.
- A subsequent full-suite rerun on the same clean archive passed:
  `uv run pytest -q`
  -> `127 passed in 32.64s`.
- This was not observed from the in-progress optimization working tree; it was
  observed from the committed snapshot tree.

Current interpretation:

- This is likely a timing-sensitive async E2E issue rather than a deterministic
  correctness failure.
- The failure path involves websocket command submission, actor/task execution,
  conversation history persistence, and polling for the final `gen_text` event.
- Unknown: whether the actor failed to emit `gen_text`, emitted it after the
  polling window, persisted it in an unexpected order, or was delayed by shared
  server state from earlier tests.

Why this matters for the benchmark:

- The benchmark is intended to measure optimization quality while preserving E2E
  accuracy.
- Agents can accidentally "optimize" by weakening synchronization, shortening
  behavior paths, or reusing shared state incorrectly.
- A flaky baseline edge makes it important to distinguish true performance
  wins from hidden correctness or scheduling regressions.

Potential probes:

- On failure, dump the full conversation history for the affected
  `conversation_id`, not just the final kind.
- Record actor status and last error after timeout.
- Log whether the tool call for the background shell task was submitted,
  completed, and followed by a final LLM turn.
- Run the full suite repeatedly from a clean archive and track whether the same
  test is the only intermittent failure.
