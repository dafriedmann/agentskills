#!/usr/bin/env python3
"""Summarise the test output IntelliJ prints (TeamCity service messages) as JSON.

Usage: python parse_testresults.py <output-file> # fullOutputPath of execute_run_configuration
       python parse_testresults.py -             # output on stdin

Prints JSON to stdout; SKILL.md describes the fields.
"""
import json
import re
import sys
from collections import Counter

MESSAGE = re.compile(r"##teamcity\[(\w+)((?:\s+\w+='(?:\|.|[^'|])*')*)\s*\]")
ATTRIBUTE = re.compile(r"(\w+)='((?:\|.|[^'|])*)'")
ESCAPE = re.compile(r"\|(0x[0-9a-fA-F]{4}|.)")
# Other escapes (|' |[ |] ||) stand for the character itself.
ESCAPED_CHARS = {"n": "\n", "r": "\r", "x": "\u0085", "l": "\u2028", "p": "\u2029"}

ASSERTION_TYPE = re.compile(r"^[\w$.]*(Assertion\w*|ComparisonFailure):\s*")
# Frames may carry a class loader and/or module prefix: "java.base/...", "app//...".
# Method names may contain spaces and parentheses (Kotlin: fun `my test (x)`()), so the
# location is the last parenthesised part of the line, optionally followed by "~[jar]".
STACK_FRAME = re.compile(r"^\s*at\s+(?:[^\s/]*/+)?(.+?)\(([^()]*)\)(?:\s+~?\[[^\]]*\])?\s*$")
# "java:test://com.acme.FooTest/bar", "java:suite://com.acme.FooTest" -> "com.acme.FooTest"
LOCATION_HINT_CLASS = re.compile(r"^\w+:\w+://([\w$.]+)")

# Why cut at all: the agent reads this summary, and it stays in its context for the rest
# of the session. Assertion messages (e.g. AssertJ's recursive comparison) print both
# objects in full and can run past 1000 characters; parameterized test names carry the
# toString() of their arguments. The first 200 characters hold what a report needs —
# the full text stays in the output file. Measured on a real run, cutting halves the
# summary (2696 -> 1508 characters).
NAME_LIMIT = 120
MESSAGE_LIMIT = 200


def clip(text, limit):
    """Cut text to at most limit characters; "…" tells the agent it was cut."""
    return text if len(text) <= limit else text[:limit - 1] + "…"


def unescape(value):
    """Undo TeamCity's escaping of attribute values.

    Each service message must stay on one line inside '…', so IntelliJ escapes special
    characters with "|": |n and |r for line breaks, |' |[ |] || for the character itself,
    |0x00e4 for any Unicode character (here "ä"). Unescaping is needed because the code
    below relies on the real characters: the stack trace in `details` is one line with
    |n until unescaped (failure_location splits it into frames), multi-line assertion
    messages would read "expected: 1|n but was: 0", and names would keep "|'" and "|[".
    """
    def replace(match):
        code = match.group(1)
        if code.startswith("0x"):
            return chr(int(code[2:], 16))
        return ESCAPED_CHARS.get(code, code)
    return ESCAPE.sub(replace, value)


def messages(text):
    """Yield (kind, attributes) for every service message in the output."""
    for match in MESSAGE.finditer(text):
        attributes = {key: unescape(value) for key, value in ATTRIBUTE.findall(match.group(2))}
        yield match.group(1), attributes


def failure_message(message):
    """'org.opentest4j.AssertionFailedError: \\nexpected: 1\\n but was: 0' -> 'expected: 1 but was: 0'

    Only assertion types are dropped; other exceptions keep their type, e.g.
    'java.lang.IllegalStateException: boom'.
    """
    message = ASSERTION_TYPE.sub("", message.strip())
    message = " ".join(line.strip() for line in message.splitlines() if line.strip())
    return clip(message, MESSAGE_LIMIT)


def test_class(location_hint):
    """'java:test://com.acme.FooTest/bar' -> 'com.acme.FooTest'; None without a hint."""
    match = LOCATION_HINT_CLASS.match(location_hint or "")
    return match.group(1) if match else None


def failure_location(stack_trace, test_class):
    """File and line of the first stack frame in the test class (nested classes and
    lambdas included), e.g. 'FooTest.java:17'; None if there is none."""
    if not test_class:
        return None
    for line in stack_trace.splitlines():
        frame = STACK_FRAME.match(line)
        if frame and frame.group(1).startswith((test_class + ".", test_class + "$")):
            return frame.group(2)
    return None


def summarise(text):
    suite_names = {}  # nodeId -> name; parents of parameterized invocations or @Nested tests
    suite_classes = {}  # nodeId -> test class from the suite's locationHint
    tests = {}        # nodeId -> test, in the order the tests ran
    test_classes = {}  # nodeId or name -> test class, for locating failures

    for kind, attributes in messages(text):
        node_id = attributes.get("nodeId")
        parent_id = attributes.get("parentNodeId")
        if kind in ("suiteTreeStarted", "testSuiteStarted"):
            suite_names[node_id] = attributes.get("name")
            suite_classes[node_id] = (test_class(attributes.get("locationHint"))
                                      or suite_classes.get(parent_id))
            continue
        if kind not in ("testStarted", "testFinished", "testFailed", "testIgnored"):
            continue

        name = attributes.get("name", "?")
        key = node_id or name
        parent = suite_names.get(parent_id)
        test = tests.setdefault(key, {
            "name": clip(f"{parent} {name}" if parent else name, NAME_LIMIT),
            "status": "unfinished",
        })
        # The hint comes with testStarted; invocations without one inherit their suite's class.
        test_classes[key] = (test_classes.get(key) or test_class(attributes.get("locationHint"))
                             or suite_classes.get(parent_id))
        if kind == "testFailed":
            test.update(status="failed",
                        message=failure_message(attributes.get("message", "")),
                        location=failure_location(attributes.get("details", ""),
                                                  test_classes[key]))
        elif kind == "testIgnored" and test["status"] != "failed":
            test.update(status="ignored",
                        reason=clip(attributes.get("message", ""), MESSAGE_LIMIT))
        elif kind == "testFinished" and test["status"] == "unfinished":
            test["status"] = "passed"

    def with_status(status, *fields):
        return [{field: test[field] for field in ("name", *fields)}
                for test in tests.values() if test["status"] == status]

    return {
        "counts": {"passed": 0, "failed": 0, "ignored": 0,
                   **Counter(test["status"] for test in tests.values())},
        "failed": with_status("failed", "message", "location"),
        "ignored": with_status("ignored", "reason"),
    }


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    # Test names and messages can hold any character; the console encoding (cp1252 on
    # Windows) can't encode all of them.
    sys.stdout.reconfigure(encoding="utf-8")
    if sys.argv[1] == "-":
        text = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    else:
        with open(sys.argv[1], encoding="utf-8", errors="replace") as file:
            text = file.read()
    # Compact: indentation would cost the agent tokens without telling it anything.
    print(json.dumps(summarise(text), ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
