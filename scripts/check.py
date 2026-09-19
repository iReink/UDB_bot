"""Run the repository's local, offline Python checks."""

from __future__ import annotations

import argparse
import ast
import os
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = (ROOT, ROOT / "web", ROOT / "tests", ROOT / "scripts", ROOT / "deploy")


def check_syntax() -> bool:
    files = sorted(path for directory in SOURCE_DIRS for path in directory.glob("*.py"))
    failures = 0
    for path in files:
        try:
            ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (SyntaxError, UnicodeError) as exc:
            print(f"Syntax error: {path.relative_to(ROOT)}: {exc}", file=sys.stderr)
            failures += 1
    print(f"Syntax: {len(files) - failures}/{len(files)} files passed", flush=True)
    return failures == 0


def check_tests(pattern: str = "test_*.py") -> bool:
    # db.py otherwise initializes the repository's stats.db during import.
    os.environ["UDB_SKIP_DB_INIT"] = "1"
    sys.path.insert(0, str(ROOT))
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), pattern=pattern)
    if suite.countTestCases() == 0:
        print(f"No tests matched {pattern!r}", file=sys.stderr)
        return False
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return result.wasSuccessful()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--syntax", action="store_true", help="Check syntax only")
    group.add_argument("--tests", action="store_true", help="Run unit tests only")
    parser.add_argument("--pattern", default="test_*.py", help="unittest discovery filename pattern")
    args = parser.parse_args()
    if args.syntax:
        return 0 if check_syntax() else 1
    if args.tests:
        return 0 if check_tests(args.pattern) else 1
    syntax_ok = check_syntax()
    tests_ok = check_tests(args.pattern) if syntax_ok else False
    return 0 if syntax_ok and tests_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
