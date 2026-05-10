# Thesis Writing Style Guide

## Context
UET-VNU Hanoi graduation thesis: "Deployment and Performance Evaluation of the Canvas LMS on Kubernetes"
Author writes in English. Style should be formal academic but natural and readable.

---

## First-Person Voice ("I")

**Rule: use sparingly — roughly 1–2 instances per subsection at most.**

Use "I" only for:
- **Key methodological decisions** — where the author made a deliberate, personal choice that shaped the study
  - ✅ *"For this thesis, I took a deliberately different approach…"*
  - ✅ *"I avoided this by isolating k6 on a dedicated instance…"*
- **Actions that are clearly the author's own** and where passive voice would sound awkward or evasive

Do NOT use "I" for:
- Describing technical facts, configurations, or system behaviour
  - ❌ *"I placed both instances in the same VPC subnet"* → *"Both instances were placed in the same VPC subnet"*
- Stating what the system does or what a tool produces
  - ❌ *"I chose the m6a family because it is…"* → *"The m6a family is a general-purpose instance type…"*
- Listing steps or procedures
- Sentences where third-person or passive reads just as naturally

**Pattern that works well:**
> Open a paragraph with one "I" sentence to signal a deliberate choice, then immediately hand back to impersonal prose for the technical explanation that follows.

Example:
> *"For this thesis, I took a deliberately different approach: the hardware was chosen not to match a specific business workload, but to create a controlled environment… An over-provisioned node would never saturate… The instance types below were selected to sit in the productive middle ground."*

---

## General Tone

- **Formal but not stiff.** Avoid overly bureaucratic constructions.
- **Explain the "why", not just the "what".** Every configuration choice should have a rationale sentence.
- **Acknowledge limitations honestly** — state them as known constraints, not apologies.
- Use **bold** for paragraph headers within a subsection (e.g., `\textbf{SUT — m6a.2xlarge.}`) to improve scannability without adding a new subsection level.
- Forward-reference later chapters when detail is deliberately deferred: *"Pod-level resource allocations are detailed in Chapter~\ref{ch:methodology}."*

## What to Avoid

- Overusing "I" — if every sentence starts with "I", it feels like a lab diary, not a thesis.
- Pure passive voice throughout — some "I" makes the author's agency visible.
- Stating numbers without context — always pair a metric with its significance.
- Cross-referencing old stage numbers (Stage 3, Stage 4, Stage 5 from old 5-stage narrative) — the thesis uses a **4-stage causal chain**.
