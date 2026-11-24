# AI Safe Code Privacy Comparator
#### Keep Your Code Privacy Intact from Agentic CLI With One-Command ProjectComparator

`compare_projects.py` answers one practical question: **what did Agentic AI build that has not yet been integrated into my original build?**

You have an existing or new code build. You first build in Agentic AI, then integrate that work into your original build. During that hand-off it is easy to miss files, or to integrate a file but not all of its changed lines. This comparator makes those gaps explicit without handing over your source code.

## The workflow

1. Build or change code in Agentic AI.
2. Point `A` at that Agentic AI build.
3. Point `B` at your original build, where the work should be integrated.
4. Run one command. Anything in `A` that is missing or different in `B` is reported.

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames
```

`--one-way` means `A -> B`: files that exist only in the original build are ignored, so the report stays focused on what still needs integrating.

If you want the opposite check, swap the folders:

```sh
./.python/python/bin/python3 compare_projects.py ORIGINAL_BUILD AGENTIC_AI_BUILD --one-way --find-renames
```

## Privacy is the default

The report contains metadata only:

- file names and relative paths
- `SAME` / `DIFF` status
- line counts, similarity score, change counts
- changed line numbers for both sides
- short content hashes

No source lines are printed unless you explicitly ask with `--show-text` or `--diff`. Those flags print a warning when used. This lets you audit an integration gap without exposing deployed or original application code to the CLI.

## What "matched" means

Files are paired by name across folders first. With `--find-renames`, unmatched files are also paired by content similarity (default threshold `0.6`). Every pair is then compared line by line with `difflib.SequenceMatcher`.

`SAME` means the lines are equal **after normalization** — whitespace and comments are ignored by default — not that the files are byte-identical. Hashes can differ while status is `SAME`.

To see whitespace or comment differences, opt out:

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --no-ignore-whitespace --no-ignore-comments
```

## See the line-by-line proof

Add `--line-map` to print the per-line pairing as line numbers only:

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --line-map | grep -A4 StartElsaActivitiesSyncCommandConsumer
```

Example:

```text
  DIFF Foo.cs  A:Foo.cs  B:Foo.cs
       name  lines 6/6  similarity 0.8333
       changed A lines: 4
       changed B lines: 4
       line map (6): A1->B1 same, A2->B2 same, A3->B3 same, A4->B4 changed, A5->B5 same, A6->B6 same
```

If a change looks missing, check the `A:` and `B:` paths on the pair line — content-based rename matching can pair a file with a different path than you expect.

## Reading the report

- `Only in A / missing in B` — Agentic AI files that have not been integrated into the original build.
- `Ignored in B / not in A` — original-build-only files, counted but hidden by `--one-way`.
- `Content matches with different names` — likely renames found by content similarity.
- `Summary:` — JSON totals for scripting.

## Other useful commands

```sh
# Structured output
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --json

# Include changed source lines (privacy tradeoff)
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --show-text

# Include unified diffs (privacy tradeoff)
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --diff

# Fail a script when anything is still missing or different
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --fail-on-diff
```

## Options

| Flag | Effect |
|---|---|
| `--one-way` | Ignore files that exist only in `B` |
| `--find-renames` | Pair unmatched files by content similarity |
| `--rename-threshold N` | Minimum rename score, default `0.6` |
| `--line-map` | Show per-line A->B match mapping, line numbers only |
| `--json` | Emit structured JSON |
| `--show-text` | Include changed source lines |
| `--diff` | Include unified diffs |
| `--context N` | Unified diff context, default `2` |
| `--no-ignore-whitespace` | Report leading/trailing whitespace differences |
| `--no-ignore-comments` | Report `//` and `/* */` comment differences |
| `--ignore-blank` | Drop blank lines before comparing |
| `--case-insensitive` | Match file names case-insensitively |
| `--include-backups` | Include `*~` and `*.bak` files |
| `--ext EXT` | Include only this extension; repeatable |
| `--skip-dir NAME` | Skip an extra directory name; repeatable |
| `--max-ranges N` | Max changed line numbers per side, default `40` |
| `--max-text-lines N` | Max snippet groups per file, default `200` |
| `--fail-on-diff` | Exit `1` when any difference is found |

## Limits

- Matching is line-based, not AST/class-aware.
- Duplicate identical lines can shift which occurrence is aligned; changed line numbers may move while the right lines are still reported.
- File size in KB is never used or reported. Renames use only a line-count prefilter plus line similarity.
- Source text is never printed unless `--show-text` or `--diff` is passed.

