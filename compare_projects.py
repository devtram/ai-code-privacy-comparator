#!/usr/bin/env python3
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SNAPSHOT = "before-update-1.1.md"
SNAPSHOT_MARKER = "<!-- compare_projects snapshot v1 -->"
SNAPSHOT_ROW = re.compile(
    r"^- `(.+?)` \| ([0-9a-f]+) \| ([0-9a-f]+) \| ([0-9a-f]+) \| (\d+)"
    r"(?: \| ([0-9a-f]+) \| ([0-9a-f]+))?$"
)
CS_USING_DIRECTIVE = re.compile(
    r"^(?:global\s+)?using\s+(?:static\s+)?[A-Za-z_][\w.]*\s*(?:=\s*[^;]+)?;\s*$"
)
CS_NAMESPACE_DIRECTIVE = re.compile(
    r"^namespace\s+[A-Za-z_][\w.]*\s*(?:;|\{)?\s*$"
)

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
    norm_digest: str = ""
    norm_noblank_digest: str = ""
    norm_header_digest: str = ""
    norm_header_noblank_digest: str = ""
    line_count: int = 0
    from_snapshot: bool = False
    mtime: float = 0.0


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


def is_cs_header_line(line: str) -> bool:
    value = line.strip()
    return bool(CS_USING_DIRECTIVE.match(value) or CS_NAMESPACE_DIRECTIVE.match(value))


def drop_cs_header_lines(lines: list[str]) -> list[str]:
    kept = [line for line in lines if not is_cs_header_line(line)]
    while kept and not kept[0].strip():
        kept.pop(0)
    return kept


def drop_cs_header_numbered(numbered: list[tuple[int, str]]) -> list[tuple[int, str]]:
    kept = [(number, line) for number, line in numbered if not is_cs_header_line(line)]
    while kept and not kept[0][1].strip():
        kept.pop(0)
    return kept


def line_digest(lines: list[str]) -> str:
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()[:12]


def digest_field(args: argparse.Namespace, header: bool = False) -> str:
    if not args.ignore_comments or not args.ignore_whitespace:
        return "digest"
    if header and getattr(args, "ignore_cs_header", True):
        return "norm_header_noblank_digest" if args.ignore_blank else "norm_header_digest"
    return "norm_noblank_digest" if args.ignore_blank else "norm_digest"


def pair_digest_field(a: SourceFile, b: SourceFile, args: argparse.Namespace) -> str:
    if (
        getattr(args, "ignore_cs_header", True)
        and a.rel.lower().endswith(".cs")
        and b.rel.lower().endswith(".cs")
    ):
        key = digest_field(args, header=True)
        if getattr(a, key) and getattr(b, key):
            return key
    return digest_field(args)


def read_source(path: Path, root: Path, args: argparse.Namespace) -> SourceFile | None:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError, ValueError):
        return None
    if "\x00" in text:
        return None
    lines = text.splitlines()
    numbered = [(number, line.rstrip("\r")) for number, line in enumerate(lines, 1)]
    default_lines = [line.strip() for line in strip_comments([line for _, line in numbered])]
    noblank_lines = [line for line in default_lines if line]
    is_cs = path.suffix.lower() == ".cs"
    ignore_cs_header = getattr(args, "ignore_cs_header", True) and is_cs
    header_default_lines = drop_cs_header_lines(default_lines) if is_cs else default_lines
    header_noblank_lines = [line for line in header_default_lines if line]
    if ignore_cs_header:
        default_lines = header_default_lines
        noblank_lines = header_noblank_lines
    if args.ignore_comments:
        numbered = [(number, stripped) for (number, _), stripped in zip(numbered, strip_comments([line for _, line in numbered]))]
    if ignore_cs_header:
        numbered = drop_cs_header_numbered(numbered)
    if args.ignore_blank:
        numbered = [(number, line) for number, line in numbered if line.strip()]
    if args.ignore_whitespace:
        numbered = [(number, line.strip()) for number, line in numbered]
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return SourceFile(
        rel=path.relative_to(root).as_posix(),
        text=text,
        lines=lines,
        norm=[line for _, line in numbered],
        numbers=[number for number, _ in numbered],
        digest=hashlib.sha256(text.encode("utf-8")).hexdigest()[:12],
        norm_digest=line_digest(default_lines),
        norm_noblank_digest=line_digest(noblank_lines),
        norm_header_digest=line_digest(header_default_lines) if is_cs else "",
        norm_header_noblank_digest=line_digest(header_noblank_lines) if is_cs else "",
        line_count=len(lines),
        mtime=mtime,
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
        snapshot_path = getattr(args, "snapshot_path", None)
        if snapshot_path is not None and path.resolve() == snapshot_path:
            continue
        if path.suffix.lower() not in suffixes:
            continue
        source = read_source(path, root, args)
        if source is not None:
            files.append(source)
    return files


def write_snapshot(path: Path, root: Path, files: list[SourceFile]) -> None:
    rows = [
        f"- `{source.rel.replace('`', '&#96;')}` | {source.digest} | "
        f"{source.norm_digest} | {source.norm_noblank_digest} | {source.line_count} | "
        f"{source.norm_header_digest} | {source.norm_header_noblank_digest}"
        for source in sorted(files, key=lambda source: source.rel)
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        "# Before-update file snapshot",
        "",
        SNAPSHOT_MARKER,
        f"<!-- root: {root} -->",
        f"<!-- files: {len(rows)} -->",
        "",
        *rows,
        "",
    ]), encoding="utf-8")


def is_snapshot(path: Path) -> bool:
    try:
        return SNAPSHOT_MARKER in path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError, ValueError):
        return False


def load_snapshot(path: Path) -> tuple[list[SourceFile], str]:
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError, ValueError) as exc:
        raise ValueError(f"cannot read snapshot {path}: {exc}") from exc
    if SNAPSHOT_MARKER not in text:
        raise ValueError(f"{path} is not a compare_projects snapshot (missing {SNAPSHOT_MARKER})")
    root_match = re.search(r"^<!-- root: (.*) -->$", text, re.MULTILINE)
    files = []
    for line in text.splitlines():
        match = SNAPSHOT_ROW.match(line)
        if match is None:
            continue
        rel, raw, norm, noblank, count, header_norm, header_noblank = match.groups()
        files.append(SourceFile(
            rel=rel.replace("&#96;", "`"),
            text="",
            lines=[],
            norm=[],
            numbers=[],
            digest=raw,
            norm_digest=norm,
            norm_noblank_digest=noblank,
            norm_header_digest=header_norm or norm,
            norm_header_noblank_digest=header_noblank or noblank,
            line_count=int(count),
            from_snapshot=True,
        ))
    return files, root_match.group(1) if root_match else ""


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


def line_map(a: SourceFile, b: SourceFile, matcher: difflib.SequenceMatcher, limit: int, include_same: bool = True) -> dict:
    entries = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            if include_same:
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


def line_count(source: SourceFile) -> int:
    return source.line_count or len(source.lines)


def likely_updated_in_b(a: SourceFile, b: SourceFile, similarity: float, changes: dict, args: argparse.Namespace) -> bool:
    if not args.detect_updates or b.from_snapshot:
        return False
    changed_lines = sum(changes[tag] for tag in ("replace", "delete", "insert"))
    if changed_lines == 0:
        return False
    if not b.mtime or b.mtime <= a.mtime:
        return False
    if getattr(args, "trust_b_updates", False):
        return True
    if a.from_snapshot:
        return True
    if similarity < args.update_similarity:
        return False
    return changed_lines <= args.update_max_changed_lines


def pair_detail(a: SourceFile, b: SourceFile, args: argparse.Namespace, match: str) -> dict:
    if a.from_snapshot or b.from_snapshot:
        key = pair_digest_field(a, b, args)
        same = bool(getattr(a, key)) and getattr(a, key) == getattr(b, key)
        return {
            "match": match,
            "name": a.rel.rsplit("/", 1)[-1],
            "a": a.rel,
            "b": b.rel,
            "sha256_12": [a.digest, b.digest],
            "norm_sha256_12": [a.norm_digest, b.norm_digest],
            "lines": [line_count(a), line_count(b)],
            "mtimes": [a.mtime, b.mtime],
            "likely_updated_in_b": likely_updated_in_b(
                a,
                b,
                1.0 if same else 0.0,
                {"equal": 0, "replace": 0 if same else 1, "delete": 0, "insert": 0},
                args,
            ),
            "similarity": 1.0 if same else 0.0,
            "changes": {"equal": 0, "replace": 0 if same else 1, "delete": 0, "insert": 0},
            "changed_lines": {"a": "", "b": ""},
            "line_changes": {"entries": [], "total": 0, "truncated": False},
            "truncated": False,
            "digest_only": True,
            "digest_key": key,
        }
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
    line_changes = line_map(a, b, matcher, args.max_ranges * 2, include_same=False)
    detail = {
        "match": match,
        "name": a.rel.rsplit("/", 1)[-1],
        "a": a.rel,
        "b": b.rel,
        "sha256_12": [a.digest, b.digest],
        "lines": [line_count(a), line_count(b)],
        "mtimes": [a.mtime, b.mtime],
        "similarity": round(matcher.ratio(), 4),
        "changes": counts,
        "changed_lines": {"a": a_ranges, "b": b_ranges},
        "line_changes": line_changes,
        "truncated": a_truncated or b_truncated,
        "likely_updated_in_b": likely_updated_in_b(a, b, matcher.ratio(), counts, args),
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
    b_by_rel = {source.rel: source for source in b_files}
    groups_a = group_by_name(a_files, args)
    groups_b = group_by_name(b_files, args)
    used_a: set[str] = set()
    used_b: set[str] = set()
    pairs: list[dict] = []

    for name in sorted(set(groups_a) & set(groups_b)):
        candidates_a = groups_a[name]
        candidates_b = groups_b[name]
        for a in candidates_a:
            b = b_by_rel.get(a.rel)
            if b is not None and b.rel not in used_b:
                pairs.append(pair_detail(a, b, args, "name+path"))
                used_a.add(a.rel)
                used_b.add(b.rel)
        scored = []
        for a in candidates_a:
            if a.rel in used_a:
                continue
            for b in candidates_b:
                if b.rel not in used_b:
                    if a.from_snapshot or b.from_snapshot:
                        key = pair_digest_field(a, b, args)
                        score = float(bool(getattr(a, key)) and getattr(a, key) == getattr(b, key))
                    else:
                        score = similarity(a, b)
                    scored.append((score, a.rel, b.rel, a, b))
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


def find_renames_by_hash(only_a: list[SourceFile], only_b: list[SourceFile], args: argparse.Namespace) -> tuple[list[dict], list[SourceFile], list[SourceFile]]:
    key = pair_digest_field(only_a[0], only_b[0], args) if only_a and only_b else digest_field(args)
    buckets = defaultdict(list)
    for source in only_b:
        digest = getattr(source, key)
        if digest:
            buckets[(digest, source.rel.rsplit(".", 1)[-1].lower())].append(source)
    used_b: set[str] = set()
    renames = []
    remaining_a = []
    for a in sorted(only_a, key=lambda source: source.rel):
        digest = getattr(a, key)
        candidates = sorted(
            [b for b in buckets.get((digest, a.rel.rsplit(".", 1)[-1].lower()), []) if b.rel not in used_b],
            key=lambda source: source.rel,
        ) if digest else []
        if candidates:
            b = candidates[0]
            renames.append(pair_detail(a, b, args, "content-hash"))
            used_b.add(b.rel)
        else:
            remaining_a.append(a)
    remaining_b = [source for source in only_b if source.rel not in used_b]
    return renames, remaining_a, remaining_b


def find_renames(only_a: list[SourceFile], only_b: list[SourceFile], args: argparse.Namespace) -> tuple[list[dict], list[SourceFile], list[SourceFile]]:
    if not args.find_renames:
        return [], only_a, only_b
    if args.from_snapshot:
        return find_renames_by_hash(only_a, only_b, args)
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
    snapshot_root = ""
    snapshot_status = None
    if args.from_snapshot:
        a_files, snapshot_root = load_snapshot(a_root)
    else:
        a_files = collect(a_root, args)
        if args.snapshot_path is not None:
            if args.snapshot_explicit or not args.snapshot_path.exists():
                write_snapshot(args.snapshot_path, a_root, a_files)
                snapshot_status = "written"
            else:
                snapshot_status = "kept"
    b_files = collect(b_root, args)
    pairs, only_a, only_b = match_pairs(a_files, b_files, args)
    renames, only_a, only_b = find_renames(only_a, only_b, args)
    only_a = sorted(only_a, key=lambda source: source.rel)
    only_b = sorted(only_b, key=lambda source: source.rel)
    ignored_b = 0
    if args.one_way and not args.from_snapshot:
        ignored_b = len(only_b)
        only_b = []
    identical = sum(1 for pair in pairs if pair["similarity"] == 1.0 and not any(
        pair["changes"][tag] for tag in ("replace", "delete", "insert")
    ))
    likely_updated = sum(1 for pair in pairs + renames if pair.get("likely_updated_in_b"))
    return {
        "a": str(a_root),
        "b": str(b_root),
        "a_snapshot": str(a_root) if args.from_snapshot else None,
        "a_snapshot_root": snapshot_root if args.from_snapshot else None,
        "snapshot_file": str(args.snapshot_path) if args.snapshot_path is not None else None,
        "snapshot_status": snapshot_status,
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
            "likely_updated_in_b": likely_updated,
        },
        "pairs": pairs,
        "only_a": [source.rel for source in only_a],
        "only_b": [source.rel for source in only_b],
        "renames": renames,
    }


def print_line_change_list(pairs: list[dict], heading: str, updated_in_b: bool = False) -> None:
    changed = [
        pair for pair in pairs
        if pair.get("line_changes", {}).get("entries")
        and bool(pair.get("likely_updated_in_b")) == updated_in_b
    ]
    changed.sort(key=lambda pair: (pair["a"], pair["b"]))
    if not changed:
        return
    print()
    print(f"{heading} - changed detected in this files ({len(changed)}):")
    for pair in changed:
        print(f"  {pair['a']}")


def print_report(report: dict) -> None:
    summary = report["summary"]
    snapshot_mode = bool(report.get("a_snapshot"))
    a_label = f"{report['a']} (snapshot)" if snapshot_mode else report["a"]
    print(f"A: {a_label} ({summary['files_a']} files)")
    print(f"B: {report['b']} ({summary['files_b']} files)")
    if snapshot_mode:
        print(f"Snapshot root: {report.get('a_snapshot_root') or '-'}")
        print("Snapshot comparison is hash-only: changed line numbers are unavailable.")
    if report.get("snapshot_status"):
        state = report["snapshot_status"]
        detail = "written" if state == "written" else "kept (already exists; pass --snapshot PATH to overwrite)"
        print(f"Pre-update snapshot: {report['snapshot_file']} - {detail}")
    print()
    print(f"Matched by name: {summary['matched']} (identical {summary['identical']}, differing {summary['differing']})")
    for pair in report["pairs"]:
        status = "SAME" if pair["similarity"] == 1.0 and not any(
            pair["changes"][tag] for tag in ("replace", "delete", "insert")
        ) else "DIFF"
        print(f"  {status} {pair['name']}  {pair['a']}")
        print(f"       {pair['match']}  lines {pair['lines'][0]}/{pair['lines'][1]}  similarity {pair['similarity']:.4f}")
        if status == "DIFF":
            print(f"       changed A lines: {pair['changed_lines']['a'] or '-'}")
            print(f"       changed B lines: {pair['changed_lines']['b'] or '-'}")
        if pair.get("digest_only"):
            print(f"       digest-only ({pair['digest_key']}); no line numbers from snapshot")
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
    print_line_change_list(report["pairs"] + report["renames"], "Newer in A / Older in B")
    print_line_change_list(
        report["pairs"] + report["renames"],
        "Newer in B / Older in A - likely updated in B",
        updated_in_b=True,
    )
    print()
    if snapshot_mode:
        print(f"Only in A / missing in B - ADD TO B ({summary['only_a']}):")
    elif report.get("one_way"):
        print(f"Only in A / missing in B ({summary['only_a']}):")
    else:
        print(f"Only in A ({summary['only_a']}):")
    for rel in report["only_a"]:
        print(f"  {rel}")
    if snapshot_mode:
        print(f"Only in B / not in A - DELETE FROM B ({summary['only_b']}):")
        for rel in report["only_b"]:
            print(f"  {rel}")
    elif report.get("one_way"):
        print(f"Ignored in B / not in A ({summary['ignored_b']}):")
    else:
        print(f"Only in B ({summary['only_b']}):")
        for rel in report["only_b"]:
            print(f"  {rel}")
    if report["renames"]:
        print()
        heading = "Content matches with different names - RENAME IN B" if snapshot_mode else "Content matches with different names"
        print(f"{heading} ({summary['renames']}):")
        for pair in report["renames"]:
            print(f"  {pair['a']}  similarity {pair['similarity']:.4f}")
            print(f"       changed A lines: {pair['changed_lines']['a'] or '-'}")
            print(f"       changed B lines: {pair['changed_lines']['b'] or '-'}")
            if pair.get("digest_only"):
                print(f"       digest-only ({pair['digest_key']}); rename by identical content")
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
        description="Compare two project trees by file name across folders; report line-level differences without printing source by default. A may also be a snapshot written by --snapshot."
    )
    parser.add_argument("a", type=Path, help="source project root (A), or a snapshot .md file")
    parser.add_argument("b", type=Path, help="target project root (B)")
    parser.add_argument("--snapshot", nargs="?", const=Path(DEFAULT_SNAPSHOT), type=Path, default=None, metavar="PATH", help=f"overwrite the pre-update snapshot of A (default ./{DEFAULT_SNAPSHOT}); one is generated automatically on the first run and kept on later runs")
    parser.add_argument("--one-way", action="store_true", help="compare A -> B only and ignore files that exist only in B")
    parser.add_argument("--json", action="store_true", help="emit structured JSON instead of text")
    parser.add_argument("--show-text", action="store_true", help="include changed source lines (opt-in, may disclose code)")
    parser.add_argument("--diff", action="store_true", help="include unified diffs (opt-in, may disclose code)")
    parser.add_argument("--context", type=int, default=2, help="unified diff context lines")
    parser.add_argument("--no-ignore-whitespace", action="store_false", dest="ignore_whitespace", help="disable default whitespace trimming")
    parser.add_argument("--ignore-blank", action="store_true", help="drop blank lines before comparing")
    parser.add_argument("--no-ignore-comments", action="store_false", dest="ignore_comments", help="disable default comment removal")
    parser.add_argument("--no-ignore-cs-header", action="store_false", dest="ignore_cs_header", help="disable default C# using/namespace directive removal")
    parser.set_defaults(
        ignore_whitespace=True,
        ignore_comments=True,
        ignore_cs_header=True,
        from_snapshot=False,
        snapshot_path=None,
        snapshot_explicit=False,
        detect_updates=True,
        trust_b_updates=False,
        find_renames=None,
    )
    parser.add_argument("--case-insensitive", action="store_true", help="match file names case-insensitively")
    parser.add_argument("--include-backups", action="store_true", help="include *~ and *.bak files")
    parser.add_argument("--ext", action="append", help="file extension to include (repeatable; default: common text/code files)")
    parser.add_argument("--skip-dir", action="append", default=[], help="additional directory name to skip")
    parser.add_argument("--find-renames", action="store_true", dest="find_renames", help="pair unmatched files by content similarity (implicit with --one-way)")
    parser.add_argument("--no-find-renames", action="store_false", dest="find_renames", help="disable rename pairing, including the implicit --one-way default")
    parser.add_argument("--rename-threshold", type=float, default=0.6, help="minimum similarity for content matches")
    parser.add_argument("--no-detect-updates", action="store_false", dest="detect_updates", help="disable the B update detection")
    parser.add_argument("--trust-b-updates", action="store_true", help="treat every changed pair with a newer B mtime as a B update, bypassing similarity and changed-line limits")
    parser.add_argument("--update-similarity", type=float, default=0.9, help="minimum similarity for a B file to count as an update (default 0.9)")
    parser.add_argument("--update-max-changed-lines", type=int, default=10, help="maximum changed lines for a B file to count as an update (default 10)")
    parser.add_argument("--line-map", action="store_true", help="show per-line A->B match mapping (line numbers only)")
    parser.add_argument("--max-ranges", type=int, default=40, help="max changed line numbers per side before truncation")
    parser.add_argument("--max-text-lines", type=int, default=200, help="max changed snippet groups per file")
    parser.add_argument("--fail-on-diff", action="store_true", help="exit 1 when any difference is found")
    args = parser.parse_args()
    if args.find_renames is None:
        args.find_renames = args.one_way
    if args.ext:
        args.ext = {ext if ext.startswith(".") else f".{ext}" for ext in args.ext}
        args.ext = {ext.lower() for ext in args.ext}
    return args


def main() -> int:
    args = parse_args()
    a_root = args.a.resolve()
    b_root = args.b.resolve()
    if not b_root.is_dir():
        print("B must be an existing directory", file=sys.stderr)
        return 2
    if a_root.is_file():
        if not is_snapshot(a_root):
            print("A file must be a compare_projects snapshot (pass a directory otherwise)", file=sys.stderr)
            return 2
        args.from_snapshot = True
        args.snapshot_path = a_root
        args.snapshot_explicit = False
        if args.snapshot is not None:
            print("warning: --snapshot is ignored when A is a snapshot", file=sys.stderr)
    elif a_root.is_dir():
        args.snapshot_explicit = args.snapshot is not None
        args.snapshot_path = (args.snapshot if args.snapshot is not None else Path(DEFAULT_SNAPSHOT)).resolve()
    else:
        print("A must be an existing directory or a compare_projects snapshot file", file=sys.stderr)
        return 2
    if args.from_snapshot:
        if args.show_text or args.diff:
            print("warning: snapshot has no source text; text and diffs are unavailable", file=sys.stderr)
        if args.one_way:
            print("note: --one-way ignored in snapshot mode so DELETE FROM B is visible", file=sys.stderr)
    elif args.show_text or args.diff:
        print("warning: source text will be included in the output", file=sys.stderr)
    try:
        report = build_report(a_root, b_root, args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
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
