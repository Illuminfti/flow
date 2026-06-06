# Comparison

`flow` is not a replacement for an agent. It is a harness for agents and scripts that need dynamic, concurrent, inspectable work.

| Capability | flow | Embedded agent workflow tools |
| --- | --- | --- |
| Runs outside one vendor app | yes | often no |
| Ordinary Python workflow scripts | yes | varies |
| Per-leaf model routing | yes | varies |
| Leaf-level crash resume across processes | yes | often session-bound |
| Cost prediction and budget gates | yes | varies |
| Custom backends and CLIs | yes | usually limited |
| Native app integrations | no | often yes |
| Built-in human UI | no | often yes |

## When flow wins

- You want a reusable workflow file in a repo.
- You need one leaf on a cheap model, another on a stronger model, and a local reducer after both.
- You need crash-resume and traceable run artifacts.
- You want to expose workflow execution to many agents or CLIs.

## When embedded systems win

- The workflow depends on a proprietary app's native tools or UI.
- You need a human review surface built into the host app.
- You do not need portability or crash-resumable journals.

Competitor behavior changes. Treat this page as a tradeoff map, not a permanent scorecard.
