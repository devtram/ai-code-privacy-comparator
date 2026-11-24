---
title: "Keep Your Code Privacy Intact in Agentic CLI With One-Command ProjectComparator"
description: "Compare an Agentic AI code build with your original or deployed build, find unintegrated files and changed lines, and keep source code private by default."
keywords:
  - Agentic AI
  - Agentic CLI
  - project comparator
  - code privacy
  - deployed source code
  - missing files
  - line-by-line comparison
  - one-command diff
---

# Keep Your Code Privacy Intact in Agentic CLI With One-Command ProjectComparator

ProjectComparator is a local CLI script that compares an **Agentic AI build** with your **original build** and reports files or lines that still need integration. ProjectComparator prints file names, line numbers, counts, and hashes by default. ProjectComparator does **not** print source code unless you explicitly ask.

## Quick answer

- **Question:** How do I find code I built in Agentic AI but forgot to integrate into my original build?
- **Answer:** Run one command with the Agentic AI build as `A` and the original build as `B`. Every `Only in A / missing in B` file is an integration gap.

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames
```

## What problem does ProjectComparator solve?

You have an existing or new code build. You first build changes in Agentic AI, then integrate those changes into your original build. During that hand-off, two mistakes are common:

1. A whole file created by Agentic AI is never copied into the original build.
2. A file is copied, but some changed lines are missed.

ProjectComparator reports both mistakes. ProjectComparator keeps the report metadata-only, so the original or deployed source code does not need to be shared with the CLI.

## Which folder is A and which folder is B?

| Side | Folder in this workflow | Meaning |
|---|---|---|
| `A` | `AGENTIC_AI_BUILD` | Source of new or changed Agentic AI work |
| `B` | `ORIGINAL_BUILD` | Integration target: your original or deployed build |

`--one-way` means `A -> B`. Original-build-only files that remain after rename matching are counted but not listed; files consumed by `--find-renames` are reported as renames. That keeps the report focused on the work that still needs integration.

To check the opposite direction, swap the folders:

```sh
./.python/python/bin/python3 compare_projects.py ORIGINAL_BUILD AGENTIC_AI_BUILD --one-way --find-renames
```

## How do I run ProjectComparator?

Use the bundled Python. No packages need to be installed.

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames
```

Replace `AGENTIC_AI_BUILD` and `ORIGINAL_BUILD` with local folder paths. The same command is stored in `command.text`.

## What does the report tell me?

| Report section | Plain-English meaning |
|---|---|
| `SAME` | The file matched after ProjectComparator normalized whitespace and comments |
| `DIFF` | At least one line changed |
| `changed A lines` / `changed B lines` | Exact line numbers that differ |
| `Only in A / missing in B` | Agentic AI files not integrated into the original build |
| `Ignored in B / not in A` | Original-build-only files left after rename matching, counted but hidden by `--one-way` |
| `Content matches with different names` | Likely renames found by content similarity |
| `Summary:` | JSON totals for scripts |

## Does ProjectComparator expose my source code?

No. ProjectComparator is privacy-first. The default report contains only:

- file names and relative paths
- `SAME` / `DIFF` status
- line counts, similarity score, and change counts
- changed line numbers for both sides
- short content hashes

Source lines appear only when you pass `--show-text` or `--diff`. Both flags print a warning because they disclose code.

## Does ProjectComparator compare files line by line?

Yes. ProjectComparator pairs files by name across folders first. With `--find-renames`, ProjectComparator also pairs unmatched files by content similarity. Each pair is then compared line by line with Python `difflib.SequenceMatcher`.

Add `--line-map` to see the per-line pairing as line numbers only:

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --line-map | grep -A4 StartElsaActivitiesSyncCommandConsumer
```

Example output:

```text
  DIFF Foo.cs  A:Foo.cs  B:Foo.cs
       name  lines 6/6  similarity 0.8333
       changed A lines: 4
       changed B lines: 4
       line map (6): A1->B1 same, A2->B2 same, A3->B3 same, A4->B4 changed, A5->B5 same, A6->B6 same
```

If a change looks missing, check the `A:` and `B:` paths on that pair line. Content-based rename matching can pair a file with a different path than expected.

## Why does a `SAME` file have a different hash?

`SAME` means equal after normalization. ProjectComparator trims leading and trailing whitespace, removes `//` and `/* */` comments, and normalizes `\r` line endings before comparing lines. Byte-level hashes can still differ.

To report whitespace and comment differences, opt out:

```sh
./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames --no-ignore-whitespace --no-ignore-comments
```

## Most useful commands

| Goal | Command |
|---|---|
| Find unintegrated files and lines | `./.python/python/bin/python3 compare_projects.py AGENTIC_AI_BUILD ORIGINAL_BUILD --one-way --find-renames` |
| Prove the line-by-line match | Add `--line-map` |
| Get JSON for a script | Add `--json` |
| Fail a script when gaps remain | Add `--fail-on-diff` |
| Show changed source lines | Add `--show-text` |
| Show unified diffs | Add `--diff` |

## FAQ

### How do I find files I forgot to integrate from Agentic AI into my original build?

Set `A` to the Agentic AI build and `B` to the original build. Read the `Only in A / missing in B` section. Those files have no matching file name in the original build.

### How do I find changed lines inside a file that already exists in both builds?

Look for `DIFF` rows. The `changed A lines` and `changed B lines` fields give the exact line numbers. Add `--line-map` to see how each line paired.

### Can I compare deployed source code without sharing it?

Yes. Keep both folders local and use the default command. ProjectComparator prints metadata only. Do not use `--show-text` or `--diff` if code privacy matters.

### Does ProjectComparator use file size in KB to match files?

No. ProjectComparator matches by file name first, then by line similarity for renames. File size in KB is never used or reported.

### Can ProjectComparator detect renamed files?

Yes. `--find-renames` pairs unmatched files with similar normalized content. The default similarity threshold is `0.6` and can be changed with `--rename-threshold`.

### What happens to original-build-only files when `--find-renames` is on?

A file that becomes a content-based rename pair is reported under `Content matches with different names`. Remaining original-build-only files are counted as ignored by `--one-way`.

### What Python version does ProjectComparator need?

Use `./.python/python/bin/python3` (Python 3.14 in this workspace). ProjectComparator has no third-party dependencies.

## Options reference

| Flag | Effect |
|---|---|
| `--one-way` | Ignore original-build-only files left after rename matching |
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
- File size in KB is never used or reported.
- Source text is never printed unless `--show-text` or `--diff` is passed.
