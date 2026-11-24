#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

SKIP_DIRS = {
    ".git", ".idea", ".vs", ".vscode", "__pycache__", "node_modules",
    "bin", "obj", ".python", ".emit_check", ".report", "dist", "build",
}
DEFAULT_SUFFIXES = {
    ".cs", ".csproj", ".json", ".md", ".sh", ".sql", ".txt", ".props",
    ".targets", ".config", ".xml", ".yml", ".yaml", ".py", ".ts", ".js",
    ".html", ".css",
}


@dataclass
class SourceFile:
    rel: str
    text: str
    lines: list[str]
    norm: list[str]
    numbers: list[int]
    digest: str


def strip_comments(lines: list[str]) -> list[str]:
    result_lines = []
    in_block = False
    for line in lines:
        out = []
        i = 0
        while i < len(line):
            if in_block:
                end = line.find("*/", i)
                if end < 0:
                    break
                in_block = False
                i = end + 2
                continue
            if line.startswith("/*", i):
                in_block = True
                i += 2
                continue
            if line.startswith("//", i):
                break
            ch = line[i]
            if ch in ("'", '"'):
                quote = ch
                out.append(ch)
                i += 1
                while i < len(line):
                    out.append(line[i])
                    if line[i] == "\\" and i + 1 < len(line):
                        out.append(line[i + 1])
                        i += 2
                        continue
                    if line[i] == quote:
                        i += 1
                        break
                    i += 1
                continue
            out.append(ch)
            i += 1
        result_lines.append("".join(out))
    return result_lines


def read_source(path: Path, root: Path, args: argparse.Namespace) -> SourceFile | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError, ValueError):
        return None
    if "\x00" in text:
        return None
    lines = text.splitlines()
    numbered = [(number, line.rstrip("\r")) for number, line in enumerate(lines, 1)]
    if args.ignore_comments:
        numbered = [(number, stripped) for (number, _), stripped in zip(numbered, strip_comments([line for _, line in numbered]))]
    if args.ignore_blank:
        numbered = [(number, line) for number, line in numbered if line.strip()]
    if args.ignore_whitespace:
        numbered = [(number, line.strip()) for number, line in numbered]
    return SourceFile(
        rel=path.relative_to(root).as_posix(),
        text=text,
        lines=lines,
        norm=[line for _, line in numbered],
        numbers=[number for number, _ in numbered],
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
    )


def collect(root: Path, args: argparse.Namespace) -> list[SourceFile]:
    files = []
    skip = set(args.skip_dir) | SKIP_DIRS
    suffixes = set(args.ext) if args.ext else DEFAULT_SUFFIXES
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        parts = path.relative_to(root).parts
        if any(part in skip for part in parts[:-1]):
            continue
        if not args.include_backups and (path.name.endswith("~") or ".bak" in path.name):
            continue
        if path.suffix.lower() not in suffixes:
            continue
        source = read_source(path, root, args)
        if source is not None:
            files.append(source)
    return files


def group_by_name(files: list[SourceFile], args: argparse.Namespace) -> dict[str, list[SourceFile]]:
    groups = defaultdict(list)
    for source in files:
        name = source.rel.rsplit("/", 1)[-1]
        if args.case_insensitive:
            name = name.lower()
        groups[name].append(source)
    return groups


def similarity(a: SourceFile, b: SourceFile) -> float:
    if not a.norm and not b.norm:
        return 1.0
    return difflib.SequenceMatcher(None, a.norm, b.norm, autojunk=False).ratio()


def range_text(numbers: list[int], limit: int) -> tuple[str, bool]:
    if not numbers:
        return "", False
    truncated = len(numbers) > limit
    numbers = numbers[:limit]
    spans = []
    start = previous = numbers[0]
    for number in numbers[1:]:
        if number == previous + 1:
            previous = number
            continue
        spans.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = number
    spans.append(str(start) if start == previous else f"{start}-{previous}")
    return ", ".join(spans), truncated


def line_map(a: SourceFile, b: SourceFile, matcher: difflib.SequenceMatcher, limit: int) -> dict:
    entries = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for k in range(i2 - i1):
                entries.append({"status": "same", "a": a.numbers[i1 + k], "b": b.numbers[j1 + k]})
        elif tag == "replace":
            common = min(i2 - i1, j2 - j1)
            for k in range(common):
                entries.append({"status": "changed", "a": a.numbers[i1 + k], "b": b.numbers[j1 + k]})
            for k in range(common, i2 - i1):
                entries.append({"status": "only_a", "a": a.numbers[i1 + k], "b": None})
            for k in range(common, j2 - j1):
                entries.append({"status": "only_b", "a": None, "b": b.numbers[j1 + k]})
        elif tag == "delete":
            for i in range(i1, i2):
                entries.append({"status": "only_a", "a": a.numbers[i], "b": None})
        elif tag == "insert":
            for j in range(j1, j2):
                entries.append({"status": "only_b", "a": None, "b": b.numbers[j]})
    return {"entries": entries[:limit], "total": len(entries), "truncated": len(entries) > limit}


def pair_detail(a: SourceFile, b: SourceFile, args: argparse.Namespace, match: str) -> dict:
    matcher = difflib.SequenceMatcher(None, a.norm, b.norm, autojunk=False)
    counts = {"equal": 0, "replace": 0, "delete": 0, "insert": 0}
    changed_a: list[int] = []
    changed_b: list[int] = []
    snippets: list[dict] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            counts["equal"] += i2 - i1
            continue
        counts[tag] += (i2 - i1) + (j2 - j1)
        changed_a.extend(a.numbers[i1:i2])
        changed_b.extend(b.numbers[j1:j2])
        if args.show_text and len(snippets) < args.max_text_lines:
            snippets.append({
                "tag": tag,
                "a": [f"{a.numbers[i]}: {a.lines[a.numbers[i] - 1]}" for i in range(i1, i2)],
                "b": [f"{b.numbers[j]}: {b.lines[b.numbers[j] - 1]}" for j in range(j1, j2)],
            })
    a_ranges, a_truncated = range_text(changed_a, args.max_ranges)
    b_ranges, b_truncated = range_text(changed_b, args.max_ranges)
    detail = {
        "match": match,
        "name": a.rel.rsplit("/", 1)[-1],
        "a": a.rel,
        "b": b.rel,
        "sha256_12": [a.digest, b.digest],
        "lines": [len(a.lines), len(b.lines)],
        "similarity": round(matcher.ratio(), 4),
        "changes": counts,
        "changed_lines": {"a": a_ranges, "b": b_ranges},
        "truncated": a_truncated or b_truncated,
    }
    if args.line_map:
        detail["line_map"] = line_map(a, b, matcher, args.max_ranges * 2)
    if args.show_text:
        detail["text"] = snippets
    if args.diff:
        detail["diff"] = "".join(difflib.unified_diff(
            a.lines, b.lines,
            fromfile=f"A/{a.rel}", tofile=f"B/{b.rel}", n=args.context,
            lineterm="\n",
        ))
    return detail


def match_pairs(a_files: list[SourceFile], b_files: list[SourceFile], args: argparse.Namespace) -> tuple[list[dict], list[SourceFile], list[SourceFile]]:
    a_by_rel = {source.rel: source for source in a_files}
    groups_a = group_by_name(a_files, args)
    groups_b = group_by_name(b_files, args)
    used_a: set[str] = set()
    used_b: set[str] = set()
    pairs: list[dict] = []

    for name in sorted(set(groups_a) & set(groups_b)):
        candidates_a = groups_a[name]
        candidates_b = groups_b[name]
        for a in candidates_a:
            b = a_by_rel.get(a.rel)
            if b in candidates_b and b.rel not in used_b:
                pairs.append(pair_detail(a, b, args, "name+path"))
                used_a.add(a.rel)
                used_b.add(b.rel)
        scored = []
        for a in candidates_a:
            if a.rel in used_a:
                continue
            for b in candidates_b:
                if b.rel not in used_b:
                    scored.append((similarity(a, b), a.rel, b.rel, a, b))
        scored.sort(key=lambda item: (-item[0], item[1], item[2]))
        for _, _, _, a, b in scored:
            if a.rel in used_a or b.rel in used_b:
                continue
            pairs.append(pair_detail(a, b, args, "name"))
            used_a.add(a.rel)
            used_b.add(b.rel)

    only_a = [source for source in a_files if source.rel not in used_a]
    only_b = [source for source in b_files if source.rel not in used_b]
    return pairs, only_a, only_b


def find_renames(only_a: list[SourceFile], only_b: list[SourceFile], args: argparse.Namespace) -> tuple[list[dict], list[SourceFile], list[SourceFile]]:
    if not args.find_renames:
        return [], only_a, only_b
    buckets = defaultdict(list)
    for source in only_b:
        buckets[source.rel.rsplit(".", 1)[-1].lower()].append(source)
    used_b: set[str] = set()
    renames = []
    remaining_a = []
    for a in only_a:
        best = None
        for b in buckets.get(a.rel.rsplit(".", 1)[-1].lower(), []):
            if b.rel in used_b:
                continue
            small = min(len(a.norm), len(b.norm))
            large = max(len(a.norm), len(b.norm))
            if small == 0 or large / small > 4:
                continue
            score = similarity(a, b)
            if best is None or score > best[0]:
                best = (score, b)
        if best is not None and best[0] >= args.rename_threshold:
            renames.append(pair_detail(a, best[1], args, "content"))
            used_b.add(best[1].rel)
        else:
            remaining_a.append(a)
    remaining_b = [source for source in only_b if source.rel not in used_b]
    return renames, remaining_a, remaining_b


def build_report(a_root: Path, b_root: Path, args: argparse.Namespace) -> dict:
    a_files = collect(a_root, args)
    b_files = collect(b_root, args)
    pairs, only_a, only_b = match_pairs(a_files, b_files, args)
    renames, only_a, only_b = find_renames(only_a, only_b, args)
    ignored_b = 0
    if args.one_way:
        ignored_b = len(only_b)
        only_b = []
    identical = sum(1 for pair in pairs if pair["similarity"] == 1.0 and not any(
        pair["changes"][tag] for tag in ("replace", "delete", "insert")
    ))
    return {
        "a": str(a_root),
        "b": str(b_root),
        "one_way": args.one_way,
        "summary": {
            "files_a": len(a_files),
            "files_b": len(b_files),
            "matched": len(pairs),
            "identical": identical,
            "differing": len(pairs) - identical,
            "only_a": len(only_a),
            "only_b": len(only_b),
            "ignored_b": ignored_b,
            "renames": len(renames),
        },
        "pairs": pairs,
        "only_a": [source.rel for source in only_a],
        "only_b": [source.rel for source in only_b],
        "renames": renames,
    }


def print_report(report: dict) -> None:
    summary = report["summary"]
    print(f"A: {report['a']} ({summary['files_a']} files)")
    print(f"B: {report['b']} ({summary['files_b']} files)")
    print()
    print(f"Matched by name: {summary['matched']} (identical {summary['identical']}, differing {summary['differing']})")
    for pair in report["pairs"]:
        status = "SAME" if pair["similarity"] == 1.0 and not any(
            pair["changes"][tag] for tag in ("replace", "delete", "insert")
        ) else "DIFF"
        print(f"  {status} {pair['name']}  A:{pair['a']}  B:{pair['b']}")
        print(f"       {pair['match']}  lines {pair['lines'][0]}/{pair['lines'][1]}  similarity {pair['similarity']:.4f}")
        if status == "DIFF":
            print(f"       changed A lines: {pair['changed_lines']['a'] or '-'}")
            print(f"       changed B lines: {pair['changed_lines']['b'] or '-'}")
        if "line_map" in pair:
            mapping = pair["line_map"]
            rendered = []
            for entry in mapping["entries"]:
                a_line = f"A{entry['a']}" if entry["a"] is not None else "A-"
                b_line = f"B{entry['b']}" if entry["b"] is not None else "B-"
                rendered.append(f"{a_line}->{b_line} {entry['status']}")
            suffix = " ..." if mapping["truncated"] else ""
            print(f"       line map ({mapping['total']}): {', '.join(rendered)}{suffix}")
        if "text" in pair:
            for snippet in pair["text"]:
                print(f"       [{snippet['tag']}]")
                for line in snippet["a"]:
                    print(f"         A {line}")
                for line in snippet["b"]:
                    print(f"         B {line}")
        if "diff" in pair:
            print(pair["diff"])
    print()
    if report.get("one_way"):
        print(f"Only in A / missing in B ({summary['only_a']}):")
    else:
        print(f"Only in A ({summary['only_a']}):")
    for rel in report["only_a"]:
        print(f"  {rel}")
    if report.get("one_way"):
        print(f"Ignored in B / not in A ({summary['ignored_b']}):")
    else:
        print(f"Only in B ({summary['only_b']}):")
        for rel in report["only_b"]:
            print(f"  {rel}")
    if report["renames"]:
        print()
        print(f"Content matches with different names ({summary['renames']}):")
        for pair in report["renames"]:
            print(f"  {pair['a']} -> {pair['b']}  similarity {pair['similarity']:.4f}")
            print(f"       changed A lines: {pair['changed_lines']['a'] or '-'}")
            print(f"       changed B lines: {pair['changed_lines']['b'] or '-'}")
            if "line_map" in pair:
                mapping = pair["line_map"]
                rendered = []
                for entry in mapping["entries"]:
                    a_line = f"A{entry['a']}" if entry["a"] is not None else "A-"
                    b_line = f"B{entry['b']}" if entry["b"] is not None else "B-"
                    rendered.append(f"{a_line}->{b_line} {entry['status']}")
                suffix = " ..." if mapping["truncated"] else ""
                print(f"       line map ({mapping['total']}): {', '.join(rendered)}{suffix}")
            if "text" in pair:
                for snippet in pair["text"]:
                    for line in snippet["a"]:
                        print(f"         A {line}")
                    for line in snippet["b"]:
                        print(f"         B {line}")
            if "diff" in pair:
                print(pair["diff"])
    print()
    print("Summary:", json.dumps(summary, sort_keys=True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare two project trees by file name across folders; report line-level differences without printing source by default."
    )
    parser.add_argument("a", type=Path, help="source project root (A)")
    parser.add_argument("b", type=Path, help="target project root (B)")
    parser.add_argument("--one-way", action="store_true", help="compare A -> B only and ignore files that exist only in B")
    parser.add_argument("--json", action="store_true", help="emit structured JSON instead of text")
    parser.add_argument("--show-text", action="store_true", help="include changed source lines (opt-in, may disclose code)")
    parser.add_argument("--diff", action="store_true", help="include unified diffs (opt-in, may disclose code)")
    parser.add_argument("--context", type=int, default=2, help="unified diff context lines")
    parser.add_argument("--no-ignore-whitespace", action="store_false", dest="ignore_whitespace", help="disable default whitespace trimming")
    parser.add_argument("--ignore-blank", action="store_true", help="drop blank lines before comparing")
    parser.add_argument("--no-ignore-comments", action="store_false", dest="ignore_comments", help="disable default comment removal")
    parser.set_defaults(ignore_whitespace=True, ignore_comments=True)
    parser.add_argument("--case-insensitive", action="store_true", help="match file names case-insensitively")
    parser.add_argument("--include-backups", action="store_true", help="include *~ and *.bak files")
    parser.add_argument("--ext", action="append", help="file extension to include (repeatable; default: common text/code files)")
    parser.add_argument("--skip-dir", action="append", default=[], help="additional directory name to skip")
    parser.add_argument("--find-renames", action="store_true", help="pair unmatched files by content similarity")
    parser.add_argument("--rename-threshold", type=float, default=0.6, help="minimum similarity for content matches")
    parser.add_argument("--line-map", action="store_true", help="show per-line A->B match mapping (line numbers only)")
    parser.add_argument("--max-ranges", type=int, default=40, help="max changed line numbers per side before truncation")
    parser.add_argument("--max-text-lines", type=int, default=200, help="max changed snippet groups per file")
    parser.add_argument("--fail-on-diff", action="store_true", help="exit 1 when any difference is found")
    args = parser.parse_args()
    if args.ext:
        args.ext = {ext if ext.startswith(".") else f".{ext}" for ext in args.ext}
        args.ext = {ext.lower() for ext in args.ext}
    return args


def main() -> int:
    args = parse_args()
    a_root = args.a.resolve()
    b_root = args.b.resolve()
    if not a_root.is_dir() or not b_root.is_dir():
        print("both arguments must be existing directories", file=sys.stderr)
        return 2
    if args.show_text or args.diff:
        print("warning: source text will be included in the output", file=sys.stderr)
    report = build_report(a_root, b_root, args)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_report(report)
    if args.fail_on_diff:
        summary = report["summary"]
        if summary["differing"] or summary["only_a"] or summary["only_b"] or summary["renames"]:
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
