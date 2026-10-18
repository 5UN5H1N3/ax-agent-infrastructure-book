# Contributing

Corrections and technical reviews are welcome.

## What to report

Please open an Issue for:

- factual errors;
- behavior that changed in newer AX or Agent Substrate releases;
- broken commands or laboratory steps;
- security caveats that should be made more explicit;
- diagrams that are ambiguous;
- terminology problems;
- missing primary sources.

When reporting version-dependent behavior, include:

- AX version/commit;
- Agent Substrate version/commit;
- Kubernetes/runtime version where relevant;
- the exact command or manifest;
- observed output;
- expected behavior and source.

## Scope

The book is primarily an operations/platform guide. Pull requests that turn it
into a Go internals walkthrough or a general-purpose Kubernetes textbook are
unlikely to fit the project scope.

## Source policy

Prefer primary sources: upstream repositories, release notes, specifications
and official documentation. Distinguish implemented behavior from roadmap or
design proposals.

## Edition metadata and navigation

Edit the passport (`edition-passport`), version map (`version-profiles`) and
chapter titles in the canonical book HTML. Do not maintain a second version
table or page count in a landing page. The repository README links to the
passport; the web converter copies it from HTML without modifying that README.
The first release in CHANGELOG is historical, not the current revision.

Print and web navigation share `scripts/book_structure.py`. If chapters or
appendices change, update its order and groups, the HTML contents and the
consistency tests. Rebuild both editions and review the affected PDF pages.

Technical diagram labels must remain at least 8 pt in the final A4 PDF. This
is an editorial criterion for this edition, not an external standard. The
diagram builder first preserves a readable composition, then tries stronger
type or a compact reflow. Run `scripts/test_diagram_readability.py` and, after
building the PDF, `scripts/check_pdf_diagrams.py PDF REBUILT_PRINT_HTML`.
Keep the PDF measurement tests and visual review: correct font size alone does
not prove that arrows, labels and boxes are laid out clearly.

## Language

The main edition is currently Russian. Technical identifiers and API names are
kept in their original form where translation would make the material less
precise.
