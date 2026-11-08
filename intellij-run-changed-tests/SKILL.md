---
name: intellij-run-changed-tests
description: Run the Java/Kotlin tests that are new or changed since the last commit (staged, unstaged, new, changed), inside the running IntelliJ IDEA instance via its MCP server. Use this whenever the user asks to "run changed tests", "run new tests", "run my staged tests", "test what I'm about to commit", "run the tests I touched", or similar — even if they don't mention IntelliJ explicitly.
---

# Run changed tests in IntelliJ

Find the test files changed since the last commit, run them in the user's IntelliJ (its
run configurations, JDK and build setup), and report pass/fail.
Always run whole test classes.

## 1. Check the IntelliJ MCP server

You need the server's tools `get_run_configurations`, `execute_run_configuration`,
`read_file` and `build_project`. Your client may show them with a server prefix (e.g.
`mcp__idea__get_run_configurations`); if it loads tools on demand, load these first.

If they don't exist or the first call fails with a connection error, **stop** — don't
run the tests another way (e.g. from the command line). Tell the user how to connect:

1. Run IntelliJ IDEA 2025.2 or newer with this project open.
2. *Settings | AI | MCP Server* (older: *Settings | Tools | MCP Server*) → **Enable MCP
   Server** → Apply.
3. *Clients Auto-Configuration* → **Auto-Configure** for their agent, or copy the shown
   server config into the agent's MCP settings; then restart the agent.

## 2. Collect the test files

```bash
python3 <skill-dir>/scripts/changed_tests.py
```
(Where there is no `python3`, e.g. on Windows, use `python`.)

It compares the working tree with `HEAD` (IntelliJ runs what is on disk):
- Staged and unstaged changes both count.
- New, untracked test files count (files ignored by `.gitignore` don't).
- Deleted files are ignored.
- Only Java and Kotlin test files count (`.java`/`.kt` under `src/<…test…>/`, e.g.
  `src/test/` or `src/integrationTest/`, not `src/testFixtures/`); other changes are ignored.
- Renamed or moved files count under their new path.

It prints JSON:
- `repo_root` — use it as `projectPath` for the IntelliJ tools.
- `test_files[]` — the matching files, relative to `repo_root`.
If `test_files` is empty, say so and stop.

## 3. Run each test file, one after another

1. **Find the run points:** `get_run_configurations` with `filePath` = the file. If it
   says `Cannot access file`, IntelliJ hasn't loaded the file yet: call `read_file` on it,
   then retry `get_run_configurations` up to three times (indexing runs in the
   background). If it still fails, report the file as not runnable and go on.
2. **Pick the run points:** the class-level ones (name without parentheses), not single
   methods. A file can hold several test classes — run each of them, but skip classes
   nested in another one (e.g. `@Nested`): they run with their outer class. No
   class-level run points means the file holds no tests IntelliJ can run (a helper or
   base class, or a file outside a test source root IntelliJ knows): list it as skipped.
3. **Run it:** `execute_run_configuration` with `filePath`, the run point's `line`,
   `waitForExit: true` and `timeout: 600000` (milliseconds, i.e. 10 minutes — the build
   runs first and integration tests can take minutes). If the run times out, report the
   file as timed out rather than as passed or failed.
   Continue with remaining test classes after individual test failures.
   Stop only for global build or infrastructure failures.
4. **Summarise:** don't read the raw `##teamcity[...]` output yourself — use the parser
   with `fullOutputPath`, or pipe `output` in via `-` if there is no `fullOutputPath`:

   ```bash
   python3 <skill-dir>/scripts/parse_testresults.py "<fullOutputPath>"
   ```

   It prints `counts` (`passed`, `failed`, `ignored`, and `unfinished` if a test never
   finished), `failed[]` with `name`, `message` and `location` (`File.java:line` of the
   first stack frame in the test class, or `null` — then the failure happened outside
   it, e.g. in setup), and `ignored[]` with `reason`. Long names and messages are cut
   short (`…`); the full text is in the output file. Parameterized invocations count as
   single tests and are named like `isOdd(int) [2] 2`. A non-zero `exitCode` with failed
   tests is normal. All counts 0 means no test ran — look at the raw output.
5. **Build failure:** if the run returns only `Process not started … build process failed`
   (no output), call `build_project` with `filesToRebuild` = all remaining test files.
   Report its `problems[]` as file:line + message — once per line, paths with `/`. If the
   build succeeds, report the original error instead (e.g. missing JDK). A compile error
   blocks every other file too, so stop and list the files that did not run.

## 4. Report
Lead with the verdict: how many files ran, then the test counts across all files (leave
out `ignored` when it is 0). Then one line per file (✗ if any test failed or did not
finish, ✓ otherwise), with each failure and each ignored test indented below it:

```
Ran 3 changed test files in IntelliJ — 7 tests passed, 1 failed, 1 ignored

✗ app/src/test/java/a/FooTest.java — 4 passed, 1 failed
    addedParam(String, String) [2] c, d — expected: "c" but was: "d"  (FooTest.java:14)
✓ app/src/test/java/a/NewIT.java — 1 passed, 1 ignored
    ignored: slowPath() — "needs staging DB"
✓ lib/src/test/kotlin/b/BarTest.kt — 2 passed

Skipped: app/src/test/java/a/Helper.java (no tests)
```

```
Build failed in IntelliJ — no tests ran

✗ app/src/test/java/a/FooTest.java:6 — cannot find symbol: class DoesNotExist
Not run: app/src/test/java/a/NewIT.java, lib/src/test/kotlin/b/BarTest.kt
```

- Don't fix failing tests unless asked; offer to look into them.

## Result integrity

Never report success unless:
- IntelliJ confirmed that the test process completed.
- The test result parser successfully parsed the output.
- At least one test was actually executed.
- No failed or unfinished tests remain.
