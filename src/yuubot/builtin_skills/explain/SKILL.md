---
name: explain
description: Explain behavior, bugs, systems, and trade-offs for a specific audience. Use when the user needs a clear mental model, scenario trace, technical walkthrough, comparison, or recommendation grounded in facts and explicit assumptions.
---

# Explain

Identify the audience, their current knowledge, and the decision or understanding they need.

Trace behavior from trigger to observable result:

```text
Trigger -> boundary -> owner/state -> decision -> output
```

Use the smallest sufficient example to make an abstraction concrete. Expand the step where ownership, context, or state changes; compress routine plumbing.

Separate facts, assumptions, trade-offs, unknowns, and recommendations. Use a table, flow, timeline, or hierarchy only when the relationship becomes materially clearer than prose.

End with the implication that matters to the audience: what they can expect, decide, verify, or do next.
