# Changelog

All notable changes to the book will be documented here.

## Unreleased

- Edition metadata aligned on 2026-10-12: one passport for HTML, PDF and the web landing page, referenced by the repository README; the working revision is distinct from the first v1.0.0 release.
- Print and web contents share numbered chapter titles, sections and separate A–C appendix links. Version signatures use eight-character SHA labels and a single profile map; source review is not runtime verification.
- Restored the canonical first-edition cover and social-preview artwork.
- Rebuilt the technical diagrams with collision-free Graphviz layout.
- Preserved edge labels semantically instead of inferring them geometrically.
- Removed obsolete regenerated cover/social-preview SVG assets.
- Print pagination is generated from the current source; 44 numbered chapters (including chapter 0), three appendices and 14 labs. Historical page counts below describe their original editions, not the current PDF.

## v1.0.0 — 2026-10-03

Initial public edition.

- 100 A4 pages.
- 44 chapters.
- 33 technical diagrams and infographics.
- 14 practical labs.
- Google AX baseline: `v0.3.1` / `e70162a3`.
- Agent Substrate baseline: `v0.3.0` / `ccecc788`.
- Covers agent fundamentals, harness architecture, context/state/memory, MCP,
  Skills, multi-agent systems, sandboxing, Kubernetes essentials, distributed
  systems, Agent Substrate, Google AX, local inference, security, observability,
  production engineering, performance, disaster recovery and troubleshooting.

## Publication policy

Upstream behavior is classified as:

1. **IMPLEMENTED** — found in the pinned implementation or documentation;
   not a claim of completed runtime testing.
2. **MAIN DELTA** — post-release behavior at an explicit fixed commit,
   not the moving state of main.
3. **ROADMAP** — upstream direction, not treated as an available feature.
4. **BACKGROUND** — a general engineering pattern, not an AX guarantee.

The canonical definitions and profile map are in chapter 0. Release tags,
commit identities and evidence labels answer different questions.
