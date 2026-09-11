# ICSE 2027 tool demonstration paper

Working title: *arcade-agent: Architecture Recovery and Conformance Inside the
Coding Agent's Loop*

| File | What it is |
|---|---|
| `OUTLINE.md` | The plan: claim, section-by-section argument, page budget, cut list, risks, schedule. **Read this first.** |
| `paper.tex` | IEEEtran skeleton. Section scaffolding and figure stubs; prose not written. Tables I and II are complete. |
| `refs.bib` | Bibliography. Self-citations verified; the rest are stubs that must be checked against publisher records. |
| `Makefile` | Build, page count, and a pre-submission check. |

## Build

There is no LaTeX toolchain in the environment this skeleton was authored in, so
**it has never been compiled.** Expect to fix small syntax errors on the first
local build.

```sh
make          # build paper.pdf with draft scaffolding visible
make pages    # report the page count
make check    # list every TODO, STUB and placeholder still present
make submission   # build with scaffolding off and report the real page count
```

You need `IEEEtran.cls` (Debian/Ubuntu: `texlive-publishers`; or from
[CTAN](https://ctan.org/pkg/ieeetran)).

## Draft scaffolding

`paper.tex` defines `\ifdraftmode`, set true. While true, `\todo{...}` renders
in red and `\secbudget{...}` prints each section's column allowance. Set
`\draftmodetrue` to `\draftmodefalse` (or run `make submission`) to make both
vanish — only then is the page count meaningful.

## Before you draft

1. **Verify the CFP.** Deadline, page limit, video requirement, and format were
   taken from search snippets because `conf.researchr.org` was unreachable.
   Confirm all four at
   <https://conf.researchr.org/track/icse-2027/icse-2027-demonstrations>.
   Four pages inclusive of references is the assumption the whole structure
   rests on.
2. **Never restate a number from memory.** Every factual claim traces to
   `arcade-agent-site/docs/content-facts.md`, which is verified against
   `origin/main` with per-line citations. Its own rule applies here: read the
   released state via `git show origin/main:<path>`, never a feature branch.
3. **Replace every `[STUB]` in `refs.bib`.** These are the algorithms this work
   reimplements; a wrong citation there is the most damaging error available.
4. **The video and the usage-instructions link are submission requirements**,
   not polish. The video URL goes at the end of the abstract, so it must exist
   before the abstract is final.
