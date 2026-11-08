#!/usr/bin/env python3
"""List the JVM test files that are new or changed since HEAD.

Staged, unstaged and untracked files all count: IntelliJ runs what is on disk. Deleted
files are left out. Which classes in a file are runnable tests is left to IntelliJ.

Usage: python changed_tests.py [--repo PATH]

Prints JSON to stdout; SKILL.md describes the fields.
"""
import argparse
import json
import re
import subprocess
import sys

# src/test/, src/integrationTest/, ... but not src/testFixtures/, which holds no runnable tests.
TEST_SOURCE_DIR = re.compile(r"(^|/)src/(?!testFixtures/)\w*[Tt]est\w*/")


def git(repo, *args, check=True):
    result = subprocess.run(
        ["git", "-C", repo, *args],
        input="", capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result


def git_paths(repo, *args):
    return [path for path in git(repo, *args, "-z").stdout.split("\0") if path]


def is_test_file(path):
    return path.endswith((".java", ".kt")) and TEST_SOURCE_DIR.search(path) is not None


def base_revision(repo):
    """HEAD, or the empty tree in a repository without commits (then every file is new)."""
    if git(repo, "rev-parse", "--verify", "--quiet", "HEAD", check=False).returncode == 0:
        return "HEAD"
    return git(repo, "hash-object", "-t", "tree", "--stdin").stdout.strip()


def changed_files(repo):
    """Paths (relative to repo) that differ from HEAD on disk, untracked files included."""
    changed = git_paths(repo, "diff", base_revision(repo), "--name-only", "--diff-filter=d")
    untracked = git_paths(repo, "ls-files", "--others", "--exclude-standard")
    return list(dict.fromkeys(changed + untracked))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=".")
    args = parser.parse_args()
    try:
        repo = git(args.repo, "rev-parse", "--show-toplevel").stdout.strip()
        result = {"repo_root": repo,
                  "test_files": [path for path in changed_files(repo) if is_test_file(path)]}
    except (RuntimeError, OSError) as error:  # OSError: git not installed
        result = {"error": str(error)}
    print(json.dumps(result, indent=2))
    sys.exit(1 if "error" in result else 0)


if __name__ == "__main__":
    main()
