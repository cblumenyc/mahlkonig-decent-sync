# Contributing

Read `AGENTS.md`, the architecture, controller contract and acceptance procedure. Keep changes scoped to a supported responsibility. New adapters need explicit capabilities, measurement provenance, immutable queue/ACK behavior and evidence-based rejection tests.

Use the README development commands and run relevant regressions, then the full Python/Tcl/JavaScript suite and portable smoke before a release. Packaging is allowlisted and source-immutable. Update the source inventory and test responsibility map when adding/removing files. No formatter/linter pass is currently required or claimed.

Use fake devices for development. Never send fabricated results to a real grinder. Hardware observations must identify the tested versions; a unit test cannot establish motion, prompt clearance, wake behavior or actual tablet appearance. Keep operational material out of patches and issues.

Contributions are provided under GPL-3.0-or-later. Retain separate asset notices. Use concise UI labels and descriptive document titles; avoid promotional headings and repeated explanatory text in normal operation. Keep current status, targets, latest result and required action visible; move detailed instructions to setup or documentation.
