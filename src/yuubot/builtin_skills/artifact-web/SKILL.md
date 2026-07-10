---
name: artifact-web
description: Build polished web artifacts and small sites. Use for one-off HTML deliverables, interactive reports, dashboards, visualizations, and maintainable web projects that need clear structure, responsive behavior, accessibility, and a verified entry point.
---

# Artifact Web

Classify the deliverable before writing files:

- Put a one-time report, visualization, export, or shareable page in `artifacts/<slug>/`.
- Put a site or application with an ongoing source lifecycle in `projects/<slug>/`.

Use one HTML file for a genuinely simple, self-contained page. Split HTML, CSS, JavaScript, data, and assets when separate files make the result easier to inspect or maintain.

Build the actual usable experience. Establish clear hierarchy, responsive layout, keyboard access, semantic markup, loading/empty/error states, and readable contrast. Keep controls stable across viewport sizes.

Before delivery, open the entry point, verify asset paths, exercise the primary interaction, check mobile and desktop layouts, and resolve visible console errors.
