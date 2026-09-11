#!/usr/bin/env python3
import ast
import fcntl
import json
import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path


class TestSourceSizes:
    def __init__(self, root):
        self.root = Path(root)
        self._by_file = {}

    def size_of(self, node_id):
        file_part, name_path = self._split(node_id)
        return (self._sizes(file_part) or {}).get(name_path, 0)

    def still_exists(self, node_id):
        file_part, name_path = self._split(node_id)
        sizes = self._sizes(file_part)
        return True if sizes is None else name_path in sizes

    def _split(self, node_id):
        parts = node_id.split("::")
        return parts[0], tuple(part.split("[")[0] for part in parts[1:])

    def _sizes(self, file_part):
        if file_part not in self._by_file:
            self._by_file[file_part] = self._read_sizes(self.root / file_part)
        return self._by_file[file_part]

    def _read_sizes(self, path):
        if not path.is_file():
            return {}
        try:
            lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
            tree = ast.parse("".join(lines))
        except Exception:
            return None
        sizes = {}
        self._collect(tree, (), lines, sizes)
        return sizes

    def _collect(self, node, prefix, lines, sizes):
        for child in getattr(node, "body", []):
            name = getattr(child, "name", None)
            if name is None:
                continue
            key = prefix + (name,)
            start = child.lineno - 1
            end = getattr(child, "end_lineno", child.lineno)
            sizes[key] = sum(len(line) for line in lines[start:end])
            self._collect(child, key, lines, sizes)


def parse_iso_datetime(dt_str):
    if not dt_str:
        return datetime.min
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean_str)
    except Exception:
        return datetime.min

def collect_node_ids(root):
    command = [sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:randomly"]
    try:
        result = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=600)
    except Exception as e:
        print(f"Could not run pytest to collect the tests in {root}: {e}", file=sys.stderr)
        sys.exit(1)
    node_ids = {line.strip() for line in result.stdout.splitlines() if "::" in line}
    if not node_ids:
        print(
            f"pytest collected nothing in {root} (exit {result.returncode}). "
            f"Run this tool from a checkout where the suite collects, with the environment that has pytest.\n"
            f"{result.stderr[-2000:]}",
            file=sys.stderr,
        )
        sys.exit(1)
    return node_ids


def duration_of(stats):
    seconds = stats.get("last_seconds")
    if seconds is not None:
        return seconds
    runs = stats.get("runs", 0)
    seconds = stats.get("seconds", 0.0)
    return seconds / runs if runs else seconds


def warn_if_unreliable(data):
    stamps = {entry.get("last_run") for entry in data.values()}
    if len(stamps) > 1:
        print(
            f"Warning: the last run was not a full run — {plural(len(data), 'entry', 'entries')} carry "
            f"{plural(len(stamps), 'different last_run timestamp')}. A full run stamps them all with the same "
            f"time and drops whatever did not run, so these durations were measured at different moments.",
            file=sys.stderr,
        )
    unmeasured = [name for name, entry in data.items() if entry.get("last_seconds") is None]
    if unmeasured:
        estimated = sum(duration_of(data[name]) for name in unmeasured)
        total = sum(duration_of(entry) for entry in data.values())
        share = estimated / total if total else 0
        print(
            f"Warning: {plural(len(unmeasured), 'test')} have never been timed by a full run — "
            f"last_seconds is missing, so their duration is the average of their recorded runs "
            f"({estimated:.1f} s, {share:.0%} of the total).",
            file=sys.stderr,
        )
    if len(stamps) > 1 or unmeasured:
        print(
            "The numbers below are the best this file supports, not a measurement. "
            "Do a full run on a quiet tree before deciding anything on them.",
            file=sys.stderr,
        )


def plural(count, singular, plural_form=None):
    return f"{count} {singular if count == 1 else plural_form or singular + 's'}"


def prune_stats(path, gone):
    with open(path, "r+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        try:
            stats = json.load(f)
            removed = [name for name in gone if name in stats]
            for name in removed:
                del stats[name]
            f.seek(0)
            f.truncate()
            json.dump(stats, f, indent=2, sort_keys=True)
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
    return len(removed)


def main():
    parser = argparse.ArgumentParser(
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description="Ranks the tests to delete, from their run statistics, their first-run date and their duration.",
        epilog="""\
Where the numbers come from
  conftest.py writes test_stats.json at the end of a run. last_seconds is
  updated by a full run only, and it is what this tool reads as a test's
  duration. Statistics accumulated from targeted runs give an invented
  total: before trusting one, do a full run on a quiet tree.

How long the suite really takes
  bin/filter_tests.py test_stats.json -d

  Every run starts by collecting the suite (pytest --collect-only, a few
  seconds) and counts only the tests that come back. A test can sit in the
  statistics and never run: here the spawns_a_build ones, which conftest
  deselects unless you ask for them by marker. Counting those would add
  minutes nobody is paying.

Proposing a cut
  bin/filter_tests.py test_stats.json -m 200

  Prints the node ids to delete to bring the suite down to 200 s. The order
  is a heuristic about cost, not value: never-failed, recent, long and large
  first. A test written this morning fits that profile exactly, and so does
  a contract or regression test that never failed because the rule it
  defends is holding. Read the list, do not execute it. Use -x to protect
  whatever is not up for deletion.

Maintenance
  bin/filter_tests.py test_stats.json -d --prune-stats
  drops the entries of tests that are gone from the source (what you want
  after a test file has been renamed).
""",
    )
    parser.add_argument("file_path", help="Path to the JSON file holding the test statistics")
    parser.add_argument(
        "--percentage", "-p", type=float, default=None,
        help="Percentage of the tests to delete (e.g. 30 for 30%%)"
    )
    parser.add_argument(
        "--max-duration", "-m", type=float, default=None,
        help="Target duration for the whole suite"
    )
    parser.add_argument(
        "--unit", choices=["seconds", "minutes"], default="seconds",
        help="Unit for --max-duration (default: seconds)"
    )
    parser.add_argument(
        "--root", "-r", default=None,
        help="Root the test paths resolve against, and where the collection runs (default: the statistics file's own directory)"
    )
    parser.add_argument(
        "--prune-stats", action="store_true",
        help="Rewrites the statistics file without the entries of tests that are gone from the source (a merely deselected test stays: its history is real)"
    )
    parser.add_argument(
        "--exclude", "-x", action="append", default=[], metavar="PATTERN",
        help="Tests to always preserve: a substring of the node id (repeatable, e.g. -x test_a_build_ends -x test_provider_event_loops.py)"
    )
    parser.add_argument(
        "--min-duration", type=float, default=1.0,
        help="Floor below which a test is never a deletion candidate, in seconds (default: 1.0; 0 to consider them all)"
    )
    parser.add_argument(
        "--sweep-duration", "-d", action="store_true",
        help="Prints how long the whole suite takes (the durations of the last full run, summed) and exits"
    )
    
    args = parser.parse_args()
    
    if args.percentage is None and args.max_duration is None and not args.sweep_duration:
        parser.error("At least one of --sweep-duration (-d), --percentage (-p) or --max-duration (-m) is required.")

    if args.percentage is not None and not (0 < args.percentage <= 100):
        print("Error: the percentage must be between 0 and 100.", file=sys.stderr)
        sys.exit(1)
        
    try:
        with open(args.file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print(f"Could not read the statistics file: {e}", file=sys.stderr)
        sys.exit(1)
        
    root = Path(args.root or Path(args.file_path).resolve().parent)
    sizes = TestSourceSizes(root)
    collected = collect_node_ids(root)

    missing = [name for name in data if name not in collected]
    gone = [name for name in missing if not sizes.still_exists(name)]
    deselected = [name for name in missing if sizes.still_exists(name)]
    for name in gone + deselected:
        del data[name]
    if gone:
        print(f"Ignoring {plural(len(gone), 'test')} that are gone from the source", file=sys.stderr)
    if args.prune_stats:
        removed = prune_stats(args.file_path, gone)
        print(f"Removed {plural(removed, 'entry', 'entries')} from {args.file_path}", file=sys.stderr)
    if deselected:
        print(f"Ignoring {plural(len(deselected), 'test')} a real run does not collect", file=sys.stderr)

    warn_if_unreliable(data)

    tests = []
    total_suite_duration = 0.0
    for test_name, stats in data.items():
        runs = stats.get("runs", 0)
        failures = stats.get("failures", 0)
        seconds = duration_of(stats)
        
        # 'first_run' is assumed present, falling back to 'last_run' or min
        first_run_str = stats.get("first_run") or stats.get("last_run") or ""
        first_run_dt = parse_iso_datetime(first_run_str)
        
        total_suite_duration += seconds
        
        tests.append({
            "test": test_name,
            "failures": failures,
            "first_run": first_run_dt,
            "runs": runs,
            "seconds": seconds,
            "size": sizes.size_of(test_name)
        })
        
    def excluded(test):
        return any(pattern in test["test"] for pattern in args.exclude)

    preserved = [t for t in tests if excluded(t)]
    if args.exclude:
        preserved_duration = sum(t["seconds"] for t in preserved)
        print(
            f"Preserved by --exclude: {plural(len(preserved), 'test')}, {preserved_duration:.1f} s",
            file=sys.stderr,
        )

    if args.sweep_duration:
        eligible = [t for t in tests if t["seconds"] >= args.min_duration and not excluded(t)]
        eligible_duration = sum(t["seconds"] for t in eligible)
        print(f"{plural(len(tests), 'test')}, {total_suite_duration:.1f} s ({total_suite_duration / 60:.2f} min)")
        print(f"candidates above {args.min_duration:g} s: {plural(len(eligible), 'test')}, {eligible_duration:.1f} s ({eligible_duration / 60:.2f} min)")
        return

    # Ranking of the candidates for DELETION:
    # 1. failures (ascending -> the ones that never failed first)
    # 2. first_run timestamp (descending -> RECENT ones first, preserving the old ones)
    # 3. runs (descending -> the ones that ran most often first)
    # 4. seconds (descending -> the longest first)
    # 5. size (descending -> the ones whose code costs most tokens to read first)
    tests.sort(key=lambda x: (
        x["failures"],
        -x["first_run"].timestamp(),
        -x["runs"],
        -x["seconds"],
        -x["size"]
    ))
    
    total_tests = len(tests)
    tests = [t for t in tests if t["seconds"] >= args.min_duration and not excluded(t)]
    
    # 1. The percentage limit
    limit_by_percentage = total_tests
    if args.percentage is not None:
        limit_by_percentage = int(round(total_tests * (args.percentage / 100.0)))
        
    # 2. The max-duration limit
    by_cost = sorted(tests, key=lambda x: (x["failures"], -x["seconds"], -x["size"]))
    limit_by_duration = total_tests
    if args.max_duration is not None:
        target_max_sec = args.max_duration * 60.0 if args.unit == "minutes" else args.max_duration
        time_to_cut = total_suite_duration - target_max_sec
        
        if time_to_cut <= 0:
            limit_by_duration = 0
        else:
            cum_time = 0.0
            count = 0
            for t in by_cost:
                cum_time += t["seconds"]
                count += 1
                if cum_time >= time_to_cut:
                    break
            limit_by_duration = count
            if cum_time < time_to_cut:
                print(
                    f"Warning: the tests above {args.min_duration:g} s are worth {cum_time:.1f} s in all, "
                    f"less than the {time_to_cut:.1f} s to cut. Lower --min-duration to consider more of them.",
                    file=sys.stderr,
                )
            
    # Whichever comes first
    if limit_by_duration < limit_by_percentage:
        to_delete = by_cost[:limit_by_duration]
    else:
        to_delete = tests[:limit_by_percentage]
    
    for t in to_delete:
        print(t["test"])

if __name__ == "__main__":
    main()
