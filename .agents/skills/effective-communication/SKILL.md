---
name: effective-communication
description: The ultimate guide for how to communicate with users. Always load this skill to smooth your development experience. Say NO to dizzy heads. Build legend with sharp brains. 
---

<effective_communication priority="absolute">
  PURPOSE: Ambiguous user requests are the #1 source of wasted development effort.
  The agent's first job is to **sharpen and simplify** — not to enumerate options
  and offload decisions onto the user.

  **Golden Rule**: If the agent does not understand the problem, it cannot solve it.
  When in doubt, ask. But ask with a SCENARIO, not with an options menu.

  ## Interaction Model: Verify-First, Then Execute

  The agent-user interaction is NOT "ask → agent works silently → ask → ...".
  It is a two-phase model:

  ```
  PHASE 1: CLARIFY + QUICK-VERIFY (rapid back-and-forth)
    ├─ User describes problem / gives reproduction steps
    ├─ Agent immediately attempts to verify (run the command, check the file)
    ├─ If verification fails → Agent reports the gap → User clarifies
    ├─ Repeat until agent is confident it fully understands
    └─ Agent presents understanding + exit criteria → requests approval

  USER APPROVES
    ↓
  PHASE 2: AGENT EXECUTES SILENTLY
    ├─ Plan → implement → validate → handoff
    └─ Minimize interruptions: no progress updates, no minor decisions

  EXCEPTION: UNEXPECTED BLOCKER (interrupt user immediately)
    ├─ Third-party dependency behaves differently than documented
    ├─ Internal constraint discovered that invalidates the plan
    └─ Report: "Expected X, got Y. Options: {A, B}. Which direction?"
       (Listing options here is narrowing, not expanding — agent has investigated.)
  ```

  During Phase 1, the agent must be FAST:
  - Run the user's reproduction command immediately — do not investigate first
  - If it doesn't reproduce → tell the user what you saw vs. what you expected
  - Never disappear for minutes of silent "debugging" before reporting back

  ## War Room: Shared Communication Space

  For structured clarifications, use `warroom/` with task-specific filenames:

  ```
  warroom/
  ├── resource-refactor-plan.md    # Agent writes; user can edit in-place
  ├── export-feature-spec.md
  ├── resource-refactor-flow.html  # Optional: visual diagrams
  └── ...
  ```

  - **`.md` files**: Editable by both sides. User can directly modify. Name them
    after the task (e.g. `{feature}-spec.md`), not generic names like `clarify.md`
    — avoids conflicts with concurrent tasks.
  - **`.html` files**: Use ONLY when visualization genuinely helps or user asks.
    Latency is critical — don't make the user wait for code generation.

  ### When to Use HTML/JS

  Two scenarios justify HTML/JS (always optimize for LOW LATENCY):

  1. **Form-filling** — When md is inadequate for structured input (e.g. multi-dimensional
     tables). Prioritize layout clarity over visual polish. MUST include a data export
     mechanism (e.g. "Export as JSON") so the agent can programmatically read user input.
  2. **Visualization** — When explaining architecture, workflows, or relationships.
     Think whiteboard, not illustration. Accuracy over beauty. Quick sketch of the
     relationship graph, not a pixel-perfect diagram.

  ## Pattern 1: Bug Reports → Demand Reproduction, Then Verify Immediately

  A stack trace tells you WHERE the crash happened, not HOW to trigger it.
  Without reproduction steps, the agent cannot verify the bug exists,
  identify the responsible component, or confirm the fix.

  **Ask the user (with a template they can fill in)**:
  ```
  I need reproduction steps. Could you provide:

  1. Exact command you ran:
  2. Expected behavior:
  3. Actual result (full error / output):

  For example:
  "I ran `uv run ybot daemon --config config.yaml`,
   expected it to start on port 8000,
   but got `KeyError: 'master_key'` at bootstrap/config.py:42."
  ```

  **After receiving steps: run them immediately.** If you cannot reproduce:
  - "I ran `{command}` and got `{actual}` instead of `{expected}`."
  - "What's different between your environment and what I tried?"
  - Do NOT speculate. Do NOT start "debugging" without confirming reproduction.

  ## Pattern 2: Feature Requests → One Concrete Use Case, or Don't Build

  Building against an abstraction without a concrete use case produces
  solutions that solve nobody's real problem and fail on the first real input.

  **Do NOT respond with "it could mean A, B, or C."** That expands uncertainty.
  Instead, embed the ambiguity INSIDE a concrete scenario:

  ```
  Let me walk through a scenario to narrow this down.

  "A user opens the web app → clicks 'Export' →
   [HERE IS WHERE I'M LOST: what should happen next?
    Download a file? See a preview? Get an email?]
   Walk me through the rest of this flow."
  ```

  By showing the user EXACTLY where the gap is within a familiar flow,
  they can resolve it with a single answer — no options menu needed.

  If the user cannot walk through ONE complete scenario:
  "I can't build this until we can trace one complete usage end-to-end."

  ## Pattern 3: Refactoring / Optimization → Shared Mental Model First

  Before touching code, agent and user MUST share a mental model of the
  CURRENT system. The agent explains; the user confirms.

  **Step 1: Explain current state (Scenario-First)**
  - Trace ONE real request through the system: actors, data shapes, timing
  - Show what data looks like at each step (DB rows, JSON payloads, etc.)
  - Use HTML visualization only when the architecture is complex enough to warrant it

  **Step 2: Align on what "better" means**
  - "At which SPECIFIC step is the pain?"
  - "What metric should change? From X → to Y?"
  - "Under what conditions? (load, data size, concurrency)"

  **Step 3: Agree on exit criteria BEFORE touching code**
  - No success metric = no way to validate the refactor = don't start

  ## Cross-Cutting: Exit Criteria

  Every task MUST answer: **"How do I know this is done?"**

  Exit criteria require **TWO confirmations**, not one:

  1. **Intent confirmation** (User: "Yes, this is what I want")
  2. **Data confirmation** (Agent: "Yes, this is actually achievable")

  For performance/metric-driven criteria, the agent MUST gather data during
  the clarify cycle — run quick profiles, benchmark the current state, estimate
  the target. Both sides can be wrong: the user may propose an unrealistic target
  ("make it 99% faster"), and the agent may dismiss a target that is actually
  achievable (e.g. O(N²)→O(N) refactoring). Data resolves both.

  Exit criteria are behavioral and verifiable:
  ```
  ✅ Good:
  - [ ] `uv run pytest tests/test_export.py` passes (12 tests)
  - [ ] `curl localhost:8000/api/export` returns a valid PDF, not a JSON error
  - [ ] Export of 10,000 rows completes in < 5s (confirmed by profile: current=32s, O(N²)→O(N log N))

  ❌ Bad:
  - [ ] Export feature is implemented   ← too vague to verify
  - [ ] Performance is improved          ← no baseline, no target
  ```

  Each pattern maps to exit criteria implicitly:
  - Bug fix: the reproduction steps no longer produce the error
  - Feature: the concrete use case works end-to-end
  - Refactoring: the target metric changed from X to Y (with data backing)

  ## Knowledge Archival (Post-Task)

  After task completion, launch a sub-agent to:
  1. **Clean up noise**: Remove rejected intermediate discussions and dead ends
  2. **Distill arguments**: For each rejected approach, summarize WHY it was rejected
     (e.g. "API X has limitation Y → cannot be used for Z"). This is the valuable knowledge.
  3. **Archive confirmed visualizations**: If an HTML diagram reflects confirmed facts,
     archive it so the next session can reference it directly instead of regenerating.
  4. **Preserve profile data**: Benchmarks, timing data, and other measurements that
     informed decisions.

  ## When to Pause: Verification Gates

  Before proceeding past Stage 1 (Discover), verify:

  1. [ ] **Bugs**: Reproduction steps exist AND the agent has reproduced the issue
  2. [ ] **Features**: At least ONE concrete use case is fully articulated
  3. [ ] **Refactoring**: The user has confirmed understanding of the current architecture
  4. [ ] **All tasks**: Exit criteria are defined, intent-confirmed, AND data-confirmed

  If ANY gate fails → return to CLARIFY + QUICK-VERIFY cycle.
  Do NOT write code until all gates pass.
</effective_communication>
