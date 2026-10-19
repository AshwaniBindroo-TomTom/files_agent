# Plan and status

This file holds these parts of the old README, from before the split:

- the front matter, with its status lines
- the Tests section
- section 6, without the ground rules of 6.1
- section 10
- section 12.

The ids, code, numbers and dates are from the old README. [The docs index](README.md#corrections-after-the-split) lists the facts that changed.

---

## Old README front matter (before the split)

This text was at the top of the old README, under its title. The title is the same at the top of [README.md](../README.md).

This repo holds an AI agent for the **Files** seat of the AgentSwitch platform. It also holds the **harness** (a test bench) that proves that the agent works.

> Seat 20's graded request: *"Find the drawing for part J-BRKT-04, and tidy the incoming folder."*
> The scenario data is on **Keystone** (`class.agentswitch.theschoolofai.in`).

- **Standard library only.** The code needs Python 3.11+. You install nothing: no agent framework and no SDK.
- **The agent uses the platform through MCP** (JSON-RPC 2.0, with a hand-written client). It uses your own model key.
- **The harness writes every run to disk before it scores the run.** It scores the writes and the final state from the database. It scores the answer content from the **model's own text** and its decision records. It never uses text that the code adds.

**Status (4 Oct 2026):**

- Phases 1–4 of [the Step 4 plan](#6-the-step-4-plan-tasks-status-and-open-questions) are complete, and they had a review (see [Changes after review](history.md#15-changes-after-review)). There are 2 exceptions:
  - T3.5 (the record of goals) is not complete. It waits for the answer to staff question Q5.
  - T3.2 (the answer writer) is only partly complete.
- The platform data changed on 23 Sept. PR #3 (26 Sept) and a follow-up (27 Sept) derived the expectations again and added 2 rules. See the note at the top of [section 5](background.md#5-background-the-platform-the-scenario-and-the-research) and [Round 5](history.md#15-changes-after-review).
- **Phase 5:** PR #4 (2 Oct) added the team's first 78 hand-written tests, and all of them passed. PR #4 also made pre-flight stricter. It confirmed decisions A and B and the ambiguity rule (see [Tests](#tests)).
- **Security:** a STRIDE review (2–3 Oct) found 54 threats. It fixed 49 of them in code and reduced 4 more. It documented the 1 threat that it kept open on purpose. The review is in the tree since the merge with PR #4 on 3 Oct. The review did not use the live platform (see [Security review (STRIDE)](safety.md#security-review-stride)).
- **Duplicates (decision C, 4 Oct):** the agent never archives a file. If a file looks like a copy of another file, the agent does not write to it. It asks a person instead. The rule is step 3 of [triage_folder](architecture.md#triage_folder-evidence-scored-filing). [Round 7](history.md#15-changes-after-review) gives the reason. New task **TI7** tests the rule.
- Offline, 24 of 24 tasks pass ×5 (scripted model; TI8 and TI9 added 19 Oct).
- Restore (T2.9) and the escalation check (T2.10) have not run live yet. They run only in the single live write run, task **TI2L**. That run has not happened.
- From Phase 0, the team still has no staff answers (T0.1).
- PR #1 merged on 22 Sept. Because of this, run manifests now record the git commit (T0.2).

More points:

- **The team writes the Phase 5 tests by hand.** The brief says *"a test written by Claude or Codex scores zero"*. So a team member must write and commit every `tests/test_*.py` file. There are 296 tests in 48 files so far (4 Oct 2026). `tests/README.md` is the guide. It tells what the tests cover and what tests are still to write.
- **Some files are team-owned drafts.** [Files you own](#12-files-you-own) lists them. Review them. Then change them.
- **Staff question Q3 is still open.** Its answer decides if AI-assisted agent and harness code is acceptable. The code assumes that it is acceptable. It also assumes that the team must write only the tests by hand. See [Questions for staff](#66-questions-for-staff).

---

## Tests

The team writes the Phase 5 tests **by hand**. PR #4 (2 Oct 2026) added 78 tests, and PR #5 (3 Oct 2026) added 79 more. On 4 Oct 2026, the `duplicate_rule_fix` branch (PR #6) added 28 more test files. It also replaced `FollowOriginalTests` in `test_escalate.py` with `PossibleCopyTests`. Now there are 296 tests in 48 files.

They are plain `unittest` tests. They use only the standard library. They run offline, against the fake server (made from the captured fixture) or against small substitutes. They never call the live platform, the model or the network.

**Run them** from the repo root:

```bash
python -m unittest discover -s tests -v
```

**On 4 Oct 2026:** the output was `Ran 296 tests … OK` in 3 runs in a row, under Python 3.14.4. On 3 Oct, it was `Ran 157 tests … OK`, also in 3 runs in a row. PR #4's 78 tests also ran under 3.11. They ran on PR #4 alone, and on PR #4 merged with the STRIDE fixes. The machine that ran PR #5's tests did not have 3.11.

A review of PR #4 broke the code on purpose for each sabotage in [7.4](checking.md#74-are-your-own-tests-any-good-phase-5). It also broke the decision rules and the pre-flight rules. On PR #4 alone, each break made at least 1 of these tests fail.

👤 **PR #5's 79 tests still need the same check, where a person breaks the code on purpose.** On 3 Oct, there were only 3 spot checks. Each one broke the code in one of these ways, and each one made a PR #5 test fail:

- The scorer gives a pass to a half-written run file.
- The agent gives the model the raw row instead of the placeholder.
- When 2 parts share a code, the agent picks the first match.

On 4 Oct, the sabotage for each STRIDE fix in `tests/README.md` made at least 1 test fail.

The review of PR #4 found 2 gaps, and they are **still open**. On 4 Oct, both sabotages ran again with all 48 test files, and all 296 tests still passed.

**Decision C (4 Oct 2026)** changed the duplicate rule (see step 3 of [triage_folder](architecture.md#triage_folder-evidence-scored-filing)). The 4 tests of `FollowOriginalTests` in `test_escalate.py` (PR #5) asserted the old rule. In that rule, a trusted copy follows its original, and the agent archives the copy. These tests are gone. A commit deleted 2 of them and changed the other 2 into `PossibleCopyTests`, which asserts the new rule. All 296 tests pass.

[`tests/README.md`](../tests/README.md#decision-c-scenarios-that-are-still-open) lists the open decision C scenarios.

| File | Tests | What it covers | Plan task | Code under test |
|---|---|---|---|---|
| `test_revisions.py` | 8 | Letter revisions and number revisions, with `AA > Z` and `Rev10 > Rev2`. A name with no marker. The parser flags several markers, and the letters `I` and `O`. Mixed letter and number schemes pick nothing. `KJ-…` does not have the `J` prefix. | T5.1 | `agent/skills/revisions.py` |
| `test_triage.py` | 8 | Score (5 tests): the mill certificate moves to Quality (score 5), and the timesheet moves to HR (score 4). The agent refuses a file with no evidence. A description alone moves nothing. A description that does not agree is a conflict. Second tidy (3 tests): pass 1 makes 5 moves, 13 escalations and 1 session. Pass 2 writes nothing, and all its escalations are `already_escalated`. | T5.2, T5.8 | `agent/skills/triage.py`, `agent/skills/escalate.py`, `agent/filing_rules.toml` |
| `test_duplicates.py` | 5 | If different files share a hash, the agent does not trust that hash. The group then uses name + size (suspected). A true copy groups on hash + size + name. The plain name is the original, also when it is younger. The answer never says "byte-verified". | T5.3 | `agent/skills/duplicates.py` |
| `test_guards.py` | 12 | Write guard (8 tests): plan mode sends nothing. The guard blocks an id outside the allow-list, a write with no write permission, and a read-only field. It skips a changed row. A failed confirm still records the write. The guard does not confirm an overwritten write. A good write is get, update, get.<br>Restore (4 tests): restore undoes the change of the agent. It does not change the change of another team. Without a journal, only the rows that the agent wrote last count. Restore refuses a snapshot with an outside id. | T5.4 | `agent/guards.py`, `agent/snapshot.py` |
| `test_mcp_client.py` | 8 | These all raise `McpError`: an error inside HTTP 200, a bare-string error, HTTP 500, a reply that is not JSON, and `isError`. The client sends a write as not idempotent. The first `401` gives 1 new login. The client returns a second `401`, and it does not try again. | T5.5 | `agent/mcp_client.py`, `agent/auth.py` |
| `test_safe_reads.py` | 4 | The code reads 1,200 rows, page by page. Only safe filters go to the server. `not_equal` keeps empty values. A filename matches only as a whole name, never as a part of a longer name. | T5.6 | `agent/safe_reads.py` |
| `test_verifiers.py` | 7 | 1 broken run and 1 fixed run for each of these checks: `writes_in_allowlist`, `read_before_write`, `write_tools_allowed`, `claims_vs_state` (a wrong folder, and an absent record), `read_only_state` and a required citation. | T5.7 | `harness/verifiers.py` |
| `test_redact.py` | 4 | After a login and a call, the password and the token are not on disk or in memory. The code masks sensitive keys, whatever the value. Short strings are not secrets. | T5.9 | `agent/redact.py`, `agent/trace.py` |
| `test_budget.py` | 5 | The turn cap, the call cap and the $ cap. `reserve` refuses a write that it cannot finish. The guard checks the budget before it sends anything. | T5.10 | `agent/budget.py`, `agent/guards.py` |
| `test_decisions.py` | 12 | Decision A: a live-capped tidy writes only within the 9 (5 updates, 4 escalations, 9 `out_of_scope` records).<br>Pre-flight (5 tests): unchanged data gives no problem and 9 warnings. A new file of the same kind is a problem. New drawings with the same code prefix are a problem. An unrelated file is not a problem. A renamed folder is a problem.<br>Decision B: if HR already has a file of the same name, the agent does not move the timesheet to HR.<br>Ambiguity rule (5 tests): a shared name, or "the duplicate PO file", returns every match. An id picks exactly 1 file. "the copy of `<id>`" picks nothing. The agent refuses a delete by id. | Decisions A and B, the ambiguity rule, T4.9 | `agent/skills/triage.py`, `harness/preflight.py`, `agent/skills/access.py` |
| `test_tasks_loader.py` | 5 | The loader refuses an unknown `[expect]` key or a bad mode. Only 1 task can be `live_write`, and that task needs `live_allowlist`. A good file loads. | T4.1 | `harness/tasks.py` |
| `test_find_drawing.py` | 14 | J-BRKT-04 resolves to RevC. RevB counts as superseded, KJ-BRKT-04 shows as a look-alike, and there are no writes. A lower-case code finds the same drawing. The agent refuses an unknown code, and 2 parts that share a code. 4 spellings of `Rev B`.<br>A tag must match as a whole (`unreleased` is not `released`). A drawing in Superseded counts as superseded. Each of these cases names **no** current drawing: 2 revisions that are not superseded, swapped archive flags, mixed `Rev1`/`RevC`, and a superseded newest revision. | T2.3 (FD-1–FD-11) | `agent/skills/find_drawing.py`, `agent/skills/revisions.py` |
| `test_triage_scoring.py` | 18 | The pure `_score`: a description plus a sender signal scores 0 and picks no folder. If a filename and a linked record do not agree, the result is a conflict that names both folders. The threshold is inclusive: score 2 escalates, and exactly 3 moves. Files that the agent moved are not evidence. Files that people moved are evidence.<br>All 18 Incoming items give 5 moves, 13 not filed, and no writes. The planted W-9 description is a conflict, and the file stays. A moved file keeps its description, and the agent adds its note at the end. The agent does not change an archived file in Incoming. | T5.2 (SCORE-1–SCORE-9) | `agent/skills/triage.py`, `agent/skills/profiles.py`, `agent/filing_rules.toml` |
| `test_escalate.py` | 11 | Same-name rank: the agent moves the file with the higher score. It escalates the lower one as a possible copy. If the scores are equal, the agent moves neither file. The 880-byte copies escalate with the basis "same filename in the destination", and they point at their originals.<br>Decision C (`PossibleCopyTests`, 2 tests): a trusted copy stays in Incoming. The agent does not archive it or change it. It makes exactly 1 escalation, which names the original and asks a person to compare the 2 files.<br>The live cap updates only the 5 originals, and escalates 4. It records the 9 copies as out of scope. In plan mode, the Escalator records "planned" and creates nothing. A second tidy writes nothing. All 13 escalations are `already_escalated`, with the ids of pass 1. | T5.8, decisions A, B and C | `agent/skills/escalate.py`, `agent/skills/triage.py`, `agent/guards.py`, `agent/runtime.py` |
| `test_runner.py` | 13 | The runner refuses 2 `live_write` tasks. In the real task folder, only TI2L is `live_write`. The runner refuses 4 live misuses of `planned_runs`. It refuses the last one because the shell-only `AS_ALLOW_WRITES` is not set. It plans 5, 2 and 1 runs.<br>Pre-flight: no problem and 9 warnings, and each warning names 1 copy. The pre-flight tests also include a same-name row, and a copy that has left Incoming.<br>Each of these counts as 1 failed run: a failed set-up, a crash, an absent run file and a half-written run file. `score`/`rescore` survive them. Calibration catches 18 of 18 planted mistakes on a D1 run. | T4.1, T4.2, T4.6, T4.8, T4.9 | `harness/tasks.py`, `harness/runner.py`, `harness/preflight.py`, `harness/score.py`, `harness/calibrate.py` |
| `test_privacy.py` | 7 | An e-sign row becomes a placeholder that keeps the id, size and folder. The input row does not change. Open rows return with no change. A planted canary reaches `ctx.files()`, but `list_files` shows 30 rows and withholds 84. The model's own `FileAttachment.get`, `FileAttachment.list` and `DriveAccessLog.list` never show the title of the canary. | T2.11 (PRIV-1–PRIV-4) | `agent/privacy.py`, `agent/loop.py`, `agent/skills/access.py` |
| `test_loop.py` | 7 | If 1 model reply has 2 `tool_use` blocks, both skills run in 1 turn (RevC, 5 planned moves, and no writes in plan mode). The loop offers the model exactly 8 skills and 8 read-only MCP tools, and no write tool. It drops a tool when the read-only hint of that tool is false. `compose` flags an id that no tool showed, whatever its letter case. It counts the record trail, and it keeps look-alikes out of the trail. | T3.0, T3.2 | `agent/loop.py`, `agent/answer.py`, `agent/catalog.py` |
| `test_config.py` | 4 | `.env` parse: the parser removes the single quotes from 4 keys. It skips a whole-line comment and a line with no `=`. The value of `AS_MODEL` in the shell has priority over the value in the file. The code always lets a fake apply and a live plan run. A live apply needs Keystone, apply mode and `AS_ALLOW_WRITES` **from the shell**. The code ignores the same value in `.env`. | T2.2 | `agent/config.py`, `agent/runtime.py` |
| `test_fake_server.py` | 2 | One-shot faults fire once. `http401_once` answers only the first `/api/mcp` call. `error_in_200:Item.list` answers only the first `Item.list` (code `agent_error`). The next call of each one succeeds. A disarmed server does not fire the 401. | T4.5 | `harness/fake_server.py` |
| `test_overview.py` | 1 | The Drive overview shows 15 files, but 30 of the 113 rows are files in folders. The agent reports this as a contradiction. The answer gives the `entity_type 'Drive'` reason, and there is 1 `info` record. | T2.11 | `agent/skills/overview.py` |
| `helpers.py` | — | This is not a test file. It holds the shared set-up that most test files use. The set-up has the pinned 26 Sept fixture, and a fake server with planted rows and faults. It also has a runtime made on that server, with the faults off. Its settings ignore both the shell and `.env`. | — | — |
| 28 more files (4 Oct 2026, `duplicate_rule_fix`) | 141 | Tests for the STRIDE fixes, more unit scenarios and decision C. [`tests/README.md`](../tests/README.md#test-files) gives each file, its count and what it checks. | — | — |
| **48 files** | **296** | | | |

**Notes:**

- The tests pin the numbers of the fixture (5 moves, 13 escalations, 9 warnings, 212 tools, 113 rows, 30 in folders, and some ids). Three files (`test_decisions.py`, `test_duplicates.py` and `test_triage.py`) load the **newest** capture. 31 of the 48 files use `tests/helpers.py`, which pins `harness/fixtures/keystone/2026-09-26` by hand. On 4 Oct 2026, that is the newest capture, so the 2 sets of tests agree. The live write run needs a fresh capture first. After that capture, do the steps below.

  CAUTION: Put the new numbers and the new pin in the same commit. If you do not move the pin, the pinned tests continue to check the 26 Sept data, with no error.

  1. **Move the pin in `helpers.py`.**
  2. Run the tests.
  3. Update each number that changed.
  4. Run the tests again.
  5. Commit all of these changes in one commit.
- The harness tasks, `python -m harness calibrate` and the checks in [section 7](checking.md#7-how-to-check-that-each-part-works) are **not** these tests. AI helped to write them.
- **Still to write by hand:** [`tests/README.md`](../tests/README.md#what-is-not-tested-yet) lists what has no test yet (4 Oct 2026). It gives 10 decision C scenarios and the 2 gaps from the review of PR #4. It also gives 33 unit scenarios that tests cover only in part. Each item has the sabotage that must make its test fail.

  Each STRIDE fix that the guide listed now has a test. Some STRIDE items stay open in part.

---

## 6. The Step 4 plan: tasks, status and open questions

This section holds the Step 4 plan. It has these parts:

- what the course grades
- every plan task
- the milestones
- the risks
- the open questions for staff.

The plan came **before** the code, so parts of it were out of date. On **22 Sept 2026**, a check compared every status below with the code, the fixtures and the run files. The 23 Sept data change, PR #3 and the 27 Sept follow-up changed some rows. A second check examined these rows on **27 Sept 2026**. If the code is different from the old plan, the table says **Changed:**.

**Status key:** ✅ built · 🟡 partly built · 🛠 platform work (staff) · 🐞 platform defect (bug raised) · ⛔ not built · 👤 team's job (hand-written by you)

### 6.1 What's graded, and the ground rules

The Step 4 brief says: build the agent, then build the harness. The agent answers the questions of your seat. The harness proves that the agent does this.

| Graded item | Requirement | Where it is in this repo | Status |
|---|---|---|---|
| **Agent** | It answers the seat's questions about live data that can change. It uses **MCP**, it runs on your machine, and it uses **your own model key**. | `python -m agent ask "<question>"`. Loop: `agent/loop.py`. MCP client: `agent/mcp_client.py`. Real model: `agent/model.py` (`--model anthropic`).<br>It needs `ANTHROPIC_API_KEY`. The default model is `claude-sonnet-5`. Set the model with `AS_MODEL`. | ✅ built · 🟡 so far, live runs used only the offline *scripted* model. The real model has not run yet. |
| **Harness** | **Your own loop.** The checks **read the database, not the agent's prose.** **The harness writes every run to disk before it scores the run.** | `python -m harness run …`. First, `harness/runner.py` writes `runs/<set>/<task>/<n>.jsonl`. Then `harness/score.py` scores the run with the checks in `harness/verifiers.py`. There are 24 tasks in `harness/tasks/*.toml`. | ✅ built · 👤 the task expectations are AI-written drafts. You must review them and own them. |
| **A refusal task** | At least 1 task where the right answer is a refusal | R1 (payslips), R2 (delete, ambiguous), R3 (file contents), R4 (`Untitled.pdf`), R5 (delete by id). TI1–TI3 also refuse or escalate 13 files offline (4 originals and 9 copies). TI2L does this for 4 originals. | ✅ |
| **Tests** | The team writes them **by hand**. "A test written by Claude or Codex scores zero." 10 points per test. | `tests/`: 296 tests in 48 files (4 Oct 2026). See [Tests](#tests). Run: `python -m unittest discover -s tests -v` | ✅ 296 written, and all pass (4 Oct 2026) · 👤 more to write (`tests/README.md`) |
| **Step 3 claims** | Each Q3 claim in the one-page gap report has a harness task that proves it | Claim 1 → C1. The figures in the updated report match C1 and the 26 Sept fixture. The Drive overview shows 15 files / 13 KB, against 30 rows / 7.1 MB in folders. Claim 2 → R1–R5, TI1–TI3 and TI2L. TI2 and TI2L escalate `scan0042.pdf`, the example in the report. Claim 3 → TI4 (a row changed *before* the agent's write) and TI5 (a row changed immediately *after* it). | ✅ offline. C1 and R3 also passed live on 22 Sept, before the data change (scripted model, ×1). |
| **Bugs** | 100 points per real bug | This seat raised 13 bugs (F1–F5, L1–L8) on 22 Sept 2026. By 27 Sept, this seat had 45 (see [5.6](bugs.md#56-bugs-raised)). The gap report is [`docs/gap_report.md`](gap_report.md). | 🐞 raised · ⛔ the re-check (T6.4) is not done |

The **Ground rules** of this section are now in [safety.md](safety.md#ground-rules-old-section-61).

### 6.2 Task list and status

**Time.** The plan estimated **about 15 focused days**:

- Phase 0: 0.5 day
- Phase 1: 2 days
- Phase 2: 4 days
- Phase 3: 1.5 days
- Phase 4: 3 days
- Phase 5 (your tests): 1.5 days
- Phase 6: 1.5 days
- 1 day of contingency for platform changes.

**Owner key:**

- **👤 You**: the team must write it by hand.
- **You+AI**: support code. AI help is acceptable only if staff say yes to Q3.
- **Staff**: a question for the course staff.

#### Phase 0 — Set-up and decisions (0.5 day)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T0.1 | Ask staff the 8 questions in [6.6](#66-questions-for-staff). In the plan, Q2–Q4 blocked Phase 1. Also, no You+AI task was to start before a yes answer to Q3. | Staff | [6.6](#66-questions-for-staff) has the written answers. | 🟡 questions written. **No replies yet** (checked 27 Sept 2026). The code came **before** the answer to Q3. It assumes that staff permit AI help. If staff say no, the team must rewrite the agent and harness code by hand. | [6.6](#66-questions-for-staff) |
| T0.2 | Private repo, `.gitignore`, `.env.example`, docs in `docs/`, no stale token files | You+AI | The repo is on the remote. A secret scan of the repo **and** `runs/` finds nothing. | 🟡 The repo is on GitHub, with `.gitignore` and `.env.example`. **Changed:** the repo is now public, not private (see the [README](../README.md#requirements)). `.gitignore` excludes `.env` files and `runs/`.<br>**Changed since 22 Sept:** PR #1 (the initial design) merged on 22 Sept. Because of this, run manifests now record the git commit (S21 in 7.3) instead of `no-commit`. A grep scan by hand found no secrets (22 Sept).<br>On 2 Oct 2026, a read-only scan of the full git history (every commit of every branch) found nothing. `python scripts/secret_scan.py --history` does the same scan again, and prints only the commit, file and line. After each of you enables it with `git config core.hooksPath .githooks`, `.githooks/pre-commit` runs the same scan on every commit. The repo has no token files.<br>On 7 Oct 2026, **gitleaks** scanned the full history (40 commits) and `runs/` (44 MB) and found no leaks. Its 5 generic-rule hits are all false positives: the dummy values in `tests/test_secrets.py` and `tests/test_redact.py`, and the fixtures' `script_key` field. `.gitleaks.toml` allowlists only these 3 values and skips no file. Git has never tracked `.env`. It tracks only `.env.example`, in which the passwords and the API key are blank. | `.gitignore`, `.env.example`, `.gitleaks.toml`, `git status` |
| T0.3 | Python project set-up and entry points. **Changed:** standard library only. No Anthropic SDK. Nothing to install. | You+AI | `python -m agent --help` runs | ✅ | `pyproject.toml` (no dependencies), `agent/__main__.py`, `harness/__main__.py` |
| T0.4 | If the course gives a tool layer, runner or local app copy, audit it. Then decide: reuse it or rewrite it. | You+AI | A written reuse/rewrite decision | ✅ decided: the code assumes that there is no course tool layer (staff question Q2 is still open). All the code is in this repo, and offline runs use the fake server. | [6.6](#66-questions-for-staff) (Q2), `harness/fake_server.py` |
| T0.5 | *(Optional)* Protect your hand-written paths (`tests/`, `harness/tasks/`, `harness/verifiers.py`) from AI edits. For example, use a deny rule in `.claude/settings.json`. Commit these paths yourself. | 👤 You | An AI edit to `tests/` fails. | ⛔ not done (there is no `.claude/settings.json` in the repo) | — |

#### Phase 1 — Platform access and offline replay (2 days)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T1.1 | `auth`: sign in, handle token expiry, and cache `/api/auth/me` | You+AI | It signs in to both businesses. | ✅ signs in again once after a `401`. The sign-in to both businesses worked on 22 Sept 2026 (the harness captured both fixtures). | `agent/auth.py` |
| T1.2 | MCP client: `initialize`, `tools/list`, `tools/call`. An error inside an HTTP-200 reply raises an error. `401` → sign in again. | You+AI | It calls `FileAttachment.list`. A bad tool name raises a clear error. | ✅ also raises an error (`McpError`) on `isError`, and when it cannot reach the platform. The client never sends a write again after a 5xx or a timeout. | `agent/mcp_client.py`, `agent/http.py` |
| T1.3 | Discover tools at start-up. Warn if a required tool is absent, or if its required args changed. | You+AI | It logs `208 tools; hash …; all required tools present` | ✅ live trace, 22 Sept 2026: `208 tools; hash cc08bae6517ed3cb; all required tools present`. Since 23 Sept (26 Sept fixture, and live on 27 Sept): `212 tools; hash c10a009a80de46c6; all required tools present`. `python -m agent smoke` exits 1 if a required tool is absent. | `agent/catalog.py`, `REQUIRED_TOOLS` in `agent/config.py` |
| T1.4 | Trace: every request and reply goes to JSONL, with secrets removed | You+AI | The Authorization header, the login password, the login token and the model key show as `[REDACTED]` | ✅ **Changed:** the plan said "every `.env` value". The code masks only secret values, and any value under a key like `password` or `token`. It does not mask the e-mail address, for example. | `agent/trace.py`, `agent/redact.py` |
| T1.5 | Safe reads that avoid the API traps. Read all the rows. Then filter them in code. Use exact matches. Sort in code. | You+AI | The "not-Item" count is correct, and it is not the `ne:` result. | 🟡 built, and every skill uses it: `list_all` never sends `ne:` or commas. `not_equal()` exists, but no skill calls it. O4 in [7.2](checking.md#72-ground-truth-ask-the-platform-directly) checks the offline "not-Item" count. It is 107, not 98, on the 26 Sept fixture, and 92, not 83, on 22 Sept. You can see the live `ne:` result only on live. Your T5.6 tests are the real check. | `agent/safe_reads.py` |
| T1.6 | Read-only fixture capture | You+AI | A fresh, dated fixture. A re-capture needs only 1 command. | ✅ `python -m harness capture keystone`. **Changed:** it saves to `harness/fixtures/<business>/<date>/` (`fixture.json` + `manifest.json` with tool and fixture hashes). It saves every file row, and it replaces the e-sign titles *before* it saves. It also saves the folders, parts, parties, the access log, `/api/auth/me`, the tool list, `people_directory`, the Drive overview and the Office view. The first capture of Keystone and Suryodaya was on 22 Sept 2026 (98 Keystone file rows).<br>**New fixture:** a second capture of Keystone on 26 Sept 2026, after the data change. It has 113 file rows, 10 access-log rows and 212 tools. Its `fixture_hash` was `8bf8e438f43d618a`. It is `df2a585221ef9f25` since a scrub of its access-log rows on 2 Oct (STRIDE I2).<br>The harness and the fake server use the newest fixture. The Suryodaya fixture is stale. It says 208 tools, but live Suryodaya had 212 on 27 Sept. | `harness/fixtures.py`, `harness/fixtures/` |
| T1.7 | Fake server: it replays the newest fixture over the same interface. It has a "row moved by someone else" fault. | You+AI | The agent's read path runs offline | ✅ also answers login and `/api/auth/me`. It has 9 faults (for example `moved_row`, `http401_once`, `error_in_200`). It is a simple copy. Its list filters are plain equality, so it does not reproduce the filter traps of the platform. | `harness/fake_server.py` |
| T1.8 | Minimal runner: it writes the run file **before** it scores the run | You+AI | A run file exists, also if the scorer crashes. | ✅ a run with a failed set-up still has a run file | `harness/runner.py` |

#### Phase 2 — Skills (4 days)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T2.1 | Decision record type + JSON form | You+AI | Records round-trip to disk | ✅ | `agent/records.py` |
| T2.2 | Write guard: pre-read, permission and read-only-field check, a read that confirms the write, plan-only default, and row-id allow-list | You+AI | The guard blocks a write to any other id. | ✅ **Changed:** on live, the allow-list is the 9 ids, **and** only the ids that are still in Incoming at the start of the run. The plan allowed the 9 ids in whatever folder they were in at that time. On live, the guard blocks a new file in Incoming.<br>Offline, the allow-list is all the files in Incoming at the start (18 files on the 26 Sept fixture). The exception is a task that sets `live_allowlist = true` (TI2L). This setting applies the live cap.<br>The guard also skips a row if its folder, description or `updated_at` changed. **Since 27 Sept**, triage and the Escalator also skip all files outside the allow-list (`out_of_scope`). So the live write run does not write to the 9 copies, and it does not escalate them. | `agent/guards.py`, `_allowlist` in `agent/runtime.py`, `agent/config.py` |
| T2.3 | **A1** `find_drawing`: exact-code resolver, rank order, and conflict flags | You+AI | Passes your D1–D3 expectations on the fake server | ✅ D1–D4 pass 5/5 offline. D1–D3 also passed live (×1). | `agent/skills/find_drawing.py` |
| T2.4 | **A5** revision parser (letters, numbers, `Rev10 > Rev2`, `AA > Z`, flags unusual names) | You+AI | Passes **your** T5.1 tests | ✅ built · checked by your T5.1 tests (`tests/test_revisions.py`, 8 tests, PR #4) | `agent/skills/revisions.py` |
| T2.5 | **A2/A3** document profiles + evidence score + refusal threshold. A description counts only when another signal agrees. | 👤 You (weights, threshold) + You+AI (support code) | The plan matches `harness/tasks/TI1.toml`, which you wrote | ✅ support code built. TI1 passes (26 Sept data: 5 planned moves, 13 not filed) · 👤 the weights and the threshold (3) in `agent/filing_rules.toml` are AI-written drafts. TI1 itself is also a draft. Review them and own them. The document type comes from the filename only (A2 is only partly complete).<br>**Changed:** files that the agent moved no longer count as similar-file evidence (PR #3). The same-name rule (decision B, confirmed) escalates the lower scorer of 2 same-named files for 1 folder. The agent never moves a possible copy (decision C, 4 Oct). | `agent/skills/triage.py`, `agent/skills/profiles.py`, `agent/filing_rules.toml` |
| T2.6 | **A4** plan, then apply with a re-read. Skip rows that changed. | You+AI | Plan-only makes 0 writes. With the moved-row fault, the agent skips that row and reports it. | 🟡 TI1 (0 writes) and TI4 (`SKIPPED`) pass. **Changed:** there is no separate plan file. Plan-only mode records the plan as decision records (`plan_move`, `plan_escalate`, …). Plan and apply happen in 1 run. Since 27 Sept, a file outside the allow-list has an `out_of_scope` record in both modes (TI2L). | `agent/skills/triage.py`, `agent/guards.py` |
| T2.7 | **A6** duplicates: recorded hash + size + name. Reject shared hashes. Archive + appended pointer. Never "byte-verified". | You+AI | The agent finds the PO pair. It rejects the shared hash of Suryodaya. | ✅ DU1, TI2, TI7. On the Suryodaya fixture, `find_duplicates` says that unrelated files share 1 hash, and that it does not trust this hash (offline check, 22 Sept 2026). **Since 23 Sept**, the 26 Sept Keystone fixture has 14 shared hashes, and the agent trusts none of them. DU1 finds the 2 PO "(1)" files as name + size "suspected" matches.<br>**Changed (4 Oct, decision C):** no archive and no pointer on the file. The agent leaves a possible copy in place, trusted or not, and escalates it once. The pointer is in the escalation ([triage step 3](architecture.md#triage_folder-evidence-scored-filing)). TI7 plants trusted pairs offline to test this. | `agent/skills/duplicates.py`, `agent/skills/triage.py` |
| T2.8 | **A7** provenance notes (append only) + sessions with `actor_label` | You+AI | Every moved file has an appended note | ✅ for example `[Files Agent <date>] Moved Incoming -> HR. Evidence: … Score: 4 (threshold 3).` 1 `AgentSession` per run, with the label `Files Agent (team20)`. The agent leaves `actor_kind` blank until the answer to Q7. | `agent/skills/triage.py`, `agent/skills/escalate.py` |
| T2.9 | **A8** snapshot every writable field of the 9 rows before the first write. Restore in a `finally` block. Restore also runs on its own. | You+AI | A restore returns all 9 rows exactly | ✅ built · 🟡 **not yet run live** (it runs only in a live write run). **Changed:** the files are `runs/<set>/<task>/snapshot-N.json` plus a write journal `writes-N.json`. Restore restores the old values of only the fields that this seat wrote and that still hold the value of this seat. It reports any other change as a conflict. Offline check, 22 Sept 2026: after a tidy on the fake server, restore restored the old values of the 6 changed rows exactly. On 27 Sept (S13, 26 Sept data), restore restored the old values of the 5 changed rows exactly. | `agent/snapshot.py`, `harness/runner.py`. Standalone: `AS_ALLOW_WRITES=1 python -m harness restore runs/<set>/<task>/snapshot-1.json --target live --live-apply` |
| T2.10 | **A9** escalation: session → escalation, with `party_id`, `reason_code` and de-duplication by subject. On the first live run, confirm that the seat can list the escalation that it created. | You+AI | A re-run creates no new escalations | 🟡 TI3 passes offline (second pass: 0 writes, 0 escalations). No check has confirmed yet that the seat can list its own escalations **on live**. That needs the live write run. Escalations have no assignee. The reason names the person to ask. | `agent/skills/escalate.py` |
| T2.11 | **A10/A11/A12** boundary explainer, leak guard, contradiction detector | You+AI | The agent refuses payslips. It withholds and counts e-sign rows. It explains the "Drive shows 0" contradiction. | ✅ R1, C2, C3, C1. **Changed:** the leak guard uses the tool list (no `<Entity>.list` tool = outside the seat), not a 403 probe. The boundary explainer matches keywords. | `agent/skills/access.py`, `agent/privacy.py`, `agent/catalog.py`, `agent/skills/overview.py` |

#### Phase 3 — Agent loop (1.5 days)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T3.0 | The loop: skills + read-only MCP tools. ≤ 12 turns. It handles stop reasons. The loop sends tool errors to the model. Retries with backoff. | You+AI | A read-only question shows ≥ 1 direct MCP read in the trace. A forced tool error reaches the model, and the loop recovers from it. | ✅ built: 8 skills + 8 read-only MCP tools. The loop tries platform reads and model calls again, with backoff. The scripted model calls only skills, so a *direct* MCP read in a real trace needs the real model. Offline check, 22 Sept 2026, with a substitute model: a tool error reached the model, and the loop continued. | `agent/loop.py`, `agent/http.py`, `agent/model.py` |
| T3.1 | Router. You write the questions and the skill that each question must reach. The router is support code. | 👤 You (questions, routes) + You+AI | Each question goes to the route that you specified. | ✅ 10/10 with the **scripted model only** · 👤 an AI-written draft that you must own. The scripted router is specific to these questions, so this result says nothing yet about the real model. **Changed:** the file is `harness/tasks/routes.toml` (TOML, not YAML), with 10 questions (not 8). The real check: `python -m harness routes --model anthropic` | `harness/tasks/routes.toml`, `python -m harness routes` |
| T3.2 | Answer writer: an answer made only from decision records, with ids | You+AI | Every claim in the answer maps to a record | 🟡 **Changed:** the answer is the model's own text plus a "Record trail" of the decision records. The code checks every cited id against the ids that the tools returned. It flags the ids that no tool returned. It does not check filenames and part codes. It does not match claims to records one by one. | `agent/answer.py` |
| T3.3 | Command line. Plan-only is the default. `--apply` writes only on the fake server. | You+AI | Without `--apply`, both graded questions run end to end with 0 writes | ✅ D1 and TI1 passed live on 22 Sept 2026 with 0 writes (scripted model). Real syntax: `python -m agent [--target live\|fake] [--business keystone\|suryodaya] ask "<question>" [--apply] [--model anthropic\|scripted]`. The agent refuses `ask --apply` on live (exit code 3). | `agent/__main__.py` |
| T3.4 | Budget guard and cost ledger | You+AI | The score report shows the cost. An injected endless loop stops at the cap. | ✅ **Changed caps:** 12 model turns, **80** MCP calls (not 40) and $0.50 per question (`AS_MAX_TURNS`, `AS_MAX_MCP_CALLS`, `AS_MAX_USD`). At the turn cap, the run aborts as `max_turns`. At the call cap or the $ cap, it aborts as `budget`. The agent never starts a write without the budget for the read that confirms it.<br>Offline check, 22 Sept 2026, with a substitute endless-loop model: it stopped at 12 turns (`max_turns`), and at a 5-call cap (`budget`). No harness task injects an endless loop. Your T5.10 test is the check. | `agent/budget.py`, `agent/loop.py`, `agent/config.py` |
| T3.5 | Record goal completion in the way that staff answer Q5 | You+AI | The Office view shows it, or the README explains why not | ⛔ not built. It waits for the answer to staff question Q5. The Office view shows both seat 20 goals as `implemented: false` (fixture, 22 Sept 2026). | — |

#### Phase 4 — Harness (3 days)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T4.1 | Task files with **your** database-level expectations for every task | 👤 You | Every task has expectations that you derived from the live data | 👤 22 task files exist (C1–C3, D1–D4, DU1, G1, R1–R5, TI1–TI7, TI2L). But **every one is an AI-written draft**, and the header of each file says so. PR #3 and the 27 Sept follow-up derived C1, R2, R3, R4, TI1, TI2 and TI3 again from the 26 Sept fixture. R5 and TI2L are new (27 Sept). TI7 is new (4 Oct, decision C).<br>The TI2L header records decision A. The TI1, TI2 and TI3 headers record decision B. PR #4 (2 Oct) marked both decisions as confirmed. It did the same for the ambiguity rule in the R2 and R3 headers. The TI7 header records decision C, and the TI1, TI2, TI2L and TI3 headers have a note about it.<br>Check each value against the live data. Change the values that you do not agree with. Commit the files yourselves. **Changed:** TOML, not YAML. | `harness/tasks/*.toml`, `harness/tasks.py` |
| T4.2 | Full runner: N repeats. It writes each run file before it scores the run. | You+AI | Runs exist on disk, also if the scorer crashes. | ✅ `expected.json` records how many run files each task owes. An absent run file counts as a failed run. | `harness/runner.py`, `harness/score.py` |
| T4.3 | Verifiers: read the database after each run | 👤 You (the checks) | Each task's pass/fail comes from the database | ✅ built, but **with AI help**, although the plan marks it as yours. Read it. Change it. Own it. **Changed:** the code finds write tools by their name (`.list`/`.get` = read), not by their `tools.describe` risk. | `harness/verifiers.py` |
| T4.4 | Claims-vs-state check, scoped to the files and the escalations of this seat | 👤 You | It catches an injected fake claim. It ignores changes by other seats. | ✅ built, but **with AI help**, although the plan marks it as yours. Calibration catches every planted fake claim. In TI4 and TI5, the check logs the change of another team as "foreign", and the run does not fail. | `_claims_vs_state` in `harness/verifiers.py`, `harness/calibrate.py` |
| T4.5 | More fault injection: `401` mid-run, an error inside HTTP 200 | You+AI | The agent recovers, or fails cleanly and says so | ✅ D4 (both faults). Also TI4 (`moved_row`), TI5 (`clobber_after_write`) and TI6 (`planted_description`). The code has 4 more faults, but no task uses them. S4, S11 and S22 in 7.3 use 3 of them by hand. TI2L's `live_allowlist` is not a fault. It rehearses the live allow-list cap offline. | `harness/fake_server.py`, `harness/tasks/D4.toml` |
| T4.6 | Score: pass^5 per task, cost per task and per set | You+AI | 1 summary table per run set | ✅ `score.json` + `report.md` in each run set | `harness/score.py`, `python -m harness score` |
| T4.7 | Run manifest (first line of every run file) | You+AI | Runs on different tool catalogues show different hashes | ✅ the manifest has: task, model and temperature, git commit, tool hash, fixture hash, business, user id, allow-list and start time. The git commit was `no-commit` until the merge of PR #1. Now it is the commit, plus `dirty`. Offline check on the 22 Sept fixture: when the check removed `Item.list`, the hash changed (`cc08bae6517ed3cb` → `503838865a92a90a`). On the 26 Sept fixture (27 Sept): without `Item.list`, the hash goes `c10a009a80de46c6` → `890acc665fc97380`. Without `DriveAccessLog.list` (S4 in 7.3), it is `97c1058771f8a313`. | `harness/manifest.py` |
| T4.8 | Rescore: rebuild every score from `runs/`. Do not call the model or the platform. | You+AI | If you delete the score and rescore, the report is identical. | ✅ `python -m harness rescore runs/<set>` said `IDENTICAL` (22 Sept 2026) | `harness/score.py` |
| T4.9 | Pre-flight: the required tools and args have no change. The tool hash matches the fixture. Incoming holds exactly the 9 ids, still with the tag `untriaged` and the fixture's `updated_at`. If not, abort. | You+AI | A changed fixture makes the live run abort before any write | ✅ **Changed:** it runs automatically before every live **write** run (not every live run). It aborts with a list of problems, not a diff. You can also run it by hand.<br>**Changed (27 Sept, decision A):** Incoming does not have to hold *exactly* the 9 now. The 9 must be there, with no change, and still `untriaged`. Any other row that the fixture contains and that has no change is only a **warning**. Pre-flight prints and traces a warning, but a warning does not block the run. An unknown or changed extra row is still a problem.<br>Some rows are new, changed or gone since the fixture, in any folder. Such a row is a problem if it shares a name or a recorded hash with 1 of the 9. A fixture row that has left Incoming is also a problem (added after review, 27 Sept). A fixture without a tool hash is a problem too.<br>Live on 27 Sept, the new check said `pre-flight OK` with 9 warnings, 1 for each copy. The old check failed with 10 problems. Offline checks: on 22 Sept, the check caught a changed `updated_at` and an absent tool. On 27 Sept and 3 Oct: S22 in 7.3.<br>**Changed (2 Oct, PR #4 and the STRIDE fixes):** pre-flight also stops on more items. The first is a related row of the same document kind (document type, or drawing code prefix). It stops on any folder change: a folder added, removed, renamed, moved, archived or updated in a different way. It also stops on 2 folders with 1 name.<br>It stops on a changed tool description or read-only mark. On live, it stops on a fixture older than 24 hours. These checks have not run live yet. | `harness/preflight.py`, `python -m harness preflight` |

**Also built, though not in the plan:**

- calibration, which tests the tester (`python -m harness calibrate`). It caught these planted mistakes:
  - 377 of 377 on the 22 tasks with decision C (4 Oct 2026)
  - 355 of 355 on the 21 tasks after the STRIDE fixes (3 Oct)
  - 295 of 295 on 27 Sept
  - 265 of 265 on the 19 tasks of 22 Sept.
- a one-command offline check (`python -m harness smoke`).

#### Phase 5 — Your tests (hand-written; 1.5 days spread across Phases 1–4)

These tests are **yours**. Keep them offline, fast and repeatable: no live platform and no model calls. The standard library's `unittest` needs no installation: `python -m unittest discover -s tests`. Staff question Q3 decides if you can use `pytest`. `tests/README.md` lists building blocks (the fake server, faults, a leak canary).

For each test, do these steps:

1. Break the code on purpose.
2. Run the test. The test must fail.
3. Undo the change.

The harness tasks, calibration and the offline checks in this section are **not** your tests.

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T5.1 | Revision parser: letters, numbers, `AA > Z`, `Rev10 > Rev2`, ambiguous names | 👤 You | Your tests pass. They fail if the code sorts revisions as plain text. | ✅ 8 tests in `tests/test_revisions.py`, passed on 2 Oct 2026 | `tests/` → `agent/skills/revisions.py` |
| T5.2 | Evidence score: each file → tier. A description alone never files. A description that does not agree → conflict. | 👤 You | …and fail if a description alone can choose the folder | ✅ 5 score tests in `tests/test_triage.py`, passed on 2 Oct 2026 | `agent/skills/triage.py`, `agent/filing_rules.toml` |
| T5.3 | Duplicates: hash match. The agent rejects a hash that unrelated files share. Name + size fallback. Never "byte-verified". | 👤 You | …and fail if the agent trusts a hash that many files share | ✅ 5 tests in `tests/test_duplicates.py`, passed on 2 Oct 2026 | `agent/skills/duplicates.py` |
| T5.4 | Guards: the guard blocks a write outside the allow-list. Plan-only writes nothing. The guard refuses a read-only field. It skips a stale row. A failed confirm still records the write. Restore does not change the change of another team. | 👤 You | …and fail if the guard allows every id | ✅ 12 tests (8 guard + 4 restore) in `tests/test_guards.py`, passed on 2 Oct 2026 | `agent/guards.py`, `agent/snapshot.py` |
| T5.5 | MCP client: an error inside an HTTP-200 reply raises an error. `isError` raises an error. `401` → 1 re-login. | 👤 You | …and fail if HTTP 200 counts as success with no check for an error inside | ✅ 8 tests (6 client + 2 login) in `tests/test_mcp_client.py`, passed on 2 Oct 2026 | `agent/mcp_client.py`, `agent/auth.py` |
| T5.6 | Safe reads: the code avoids the `ne:`, comma and sort-order traps | 👤 You | …and fail if the code sends `ne:` to the server | ✅ 4 tests in `tests/test_safe_reads.py`, passed on 2 Oct 2026 | `agent/safe_reads.py` |
| T5.7 | Verifiers: each check fails on a deliberately broken run | 👤 You | …and fail if a verifier always returns ok | ✅ 7 broken/fixed pairs in `tests/test_verifiers.py`, passed on 2 Oct 2026 | `harness/verifiers.py` |
| T5.8 | Idempotency: a second tidy makes no writes and no new escalations | 👤 You | …and fail if the code skips the "already escalated?" check | ✅ 3 tests (first-pass counts, second-pass silence, de-duplication) in `tests/test_triage.py`, passed on 2 Oct 2026 | `agent/skills/triage.py`, `agent/skills/escalate.py` |
| T5.9 | Redaction: a trace of login + a call contains no secret | 👤 You | …and fail if redaction is off | ✅ 4 tests in `tests/test_redact.py`, passed on 2 Oct 2026 | `agent/redact.py`, `agent/trace.py` |
| T5.10 | Budget: an endless loop stops at the cap. A write never starts without the budget for the read that confirms it. | 👤 You | …and fail if you remove the cap | ✅ 5 tests in `tests/test_budget.py`, passed on 2 Oct 2026 | `agent/budget.py`, `agent/guards.py` |

#### Phase 6 — Evaluate and submit (1.5 days)

| Id | Task | Owner | Done when | Status now | Where |
|---|---|---|---|---|---|
| T6.1 | Offline: every task ×5 → pass^5 | 👤 You | Report saved | 🟡 22/22 pass on every run, ×5, with the **scripted model** (4 Oct 2026, 26 Sept fixture, decision C). Rescore identical. Calibration 377/377. (27 Sept: 21/21 and 295/295. 22 Sept: 19/19 and 265/265.)<br>The real model has not run. Git ignores `runs/`, so copy the report that you submit. | Done (scripted): `python -m harness run all --target fake --model scripted`. To do (real model): `python -m harness run all --target fake --model anthropic` |
| T6.2 | Live on Keystone, after pre-flight: the read-only tasks ×5. The write run once, with snapshot and restore. | 👤 You | Live results recorded. Files restored. | 🟡 **Done:** the 10 read-only tasks (D1–D3, DU1, C1, C2, R1–R3, TI1) ×1 with the scripted model. Result: 10/10, 0 write calls (22 Sept 2026, before the data change). Live read-only checks on 27 Sept: Keystone matches the 26 Sept fixture. `harness preflight` says `pre-flight OK` with 9 warnings.<br>**Not done yet:** the read-only tasks with the real model ×5 (now 11 with R5). Also the **single live write run, TI2L** (not run).<br>**Changed:** the plan said TI2 then TI3. The offline runs prove TI3's "second run does nothing". Since 27 Sept, the live write task is **TI2L** (TI2 is offline-only). TI2L is the only task with `live_write = true`, so the harness refuses TI2, TI3 and R4 on live.<br>This seat cannot remove escalations and sessions. So the write run happens once, on a date that the team chooses. It needs the approval of the repo owner (checklist in [11](live-run.md#11-the-live-write-run)). | Read-only: `python -m harness run D1 D2 D3 DU1 C1 C2 R1 R2 R3 R5 TI1 --target live --model anthropic`. Write: `python -m harness capture keystone`, `python -m harness preflight`, then the live write command in [11](live-run.md#11-the-live-write-run) |
| T6.3 | README: how to run, results, cost, known limits | 👤 You | A newcomer can run it in 10 minutes | ✅ the README · 👤 ask a newcomer to try it. Measure the time. | `README.md` |
| T6.4 | Re-check the 13 bugs. Raise all new bugs that you find during the build. | 👤 You | New bugs raised | ⛔ not done. **Changed:** by 27 Sept, this seat had 45 bugs (see [5.6](bugs.md#56-bugs-raised)). There is no `repro.py` in this repo. So re-check each bug by hand, with read-only requests. | — |

**Cut line.** The plan said: if there are only 11 days, first cut these items:

- G1
- A11/C2
- A5's drive-wide revision report
- the extra fault injection (T4.5).

**Never cut Phase 5.** G1, C2 and T4.5 now exist anyway. The only cut was A5's drive-wide report.

These items are still to do:

- staff answers (T0.1)
- ownership of the drafts, and confirmation of decisions A and B (T2.5, T3.1, T4.1, T4.3, T4.4)
- **more of your tests (Phase 5)**. There are 296 tests in 48 files (4 Oct 2026). `tests/README.md` lists the open scenarios and STRIDE checks.
- the real-model runs (T6.1, T6.2)
- the live write run (TI2L)
- the bug re-check (T6.4)
- T3.5 (it waits for the answer to staff question Q5).

If time is short, cut by tier (6.4): Could first, then Should. Never cut Phase 5.

### 6.3 Milestones

| Milestone | Contents | Target | Status now |
|---|---|---|---|
| M1 | Phases 0–1: MCP client, redacted traces, fixtures, fake server, minimal runner | Day 2.5 | ✅ Phase 1 built · 🟡 Phase 0 open: no staff answers. The gitleaks scan ran on 7 Oct 2026 (T0.2). PR #1 merged, so the team committed and pushed the code. |
| M2 | `find_drawing` passes D1–D3 through the runner on the fake server | Day 4.5 | ✅ D1–D4 pass 5/5 (scripted model) |
| M3 | Triage plan / apply / undo. TI1–TI4 offline. | Day 7 | ✅ TI1–TI7, TI2L and G1 pass offline · 🟡 undo (restore) not yet run live |
| M4 | Loop, answer writer, budget guard. R1–R4 and C1 end to end. | Day 8.5 | ✅ with the scripted model · 🟡 answer writer partly built (T3.2). The real model has not run. |
| M5 | Verifiers, scoped claims-vs-state, manifest, rescore, pre-flight. Offline pass^5. | Day 12 | ✅ 22/22 ×5, rescore identical, calibration 377/377 (scripted model, 4 Oct 2026). On 27 Sept: 21/21 and 295/295 · 👤 the checks and the task expectations are AI-written drafts. You must own them. |
| M6 | Live runs (read-only ×5, and the write run once), README, bug re-check | Day 14 (+1 contingency) | 🟡 live read-only ×1 (scripted): 10/10 on 22 Sept. Live pre-flight OK with 9 warnings on 27 Sept. README done · ⛔ real model ×5, the live write run (TI2L), the bug re-check |

Phase 5 has no milestone of its own: the plan spreads it across M1–M5. PR #4 added the first 78 hand-written tests on 2 Oct 2026. PR #5 added 79 more on 3 Oct 2026 (see [Tests](#tests)).

### 6.4 Must / Should / Could

| Tier | Item | Status now |
|---|---|---|
| **Must** | Phase 0: T0.1–T0.4 | 🟡 T0.3 ✅ and T0.4 decided. T0.1 has no staff replies. For T0.2, the team committed and pushed the code (PR #1), and gitleaks found no leaks on 7 Oct 2026. |
| Must | Phase 1: T1.1–T1.8 | ✅ (T1.5 🟡: `not_equal` is not in use, and only the offline O4 check ran) |
| Must | Phase 2: T2.1–T2.11 | ✅ built · 🟡 T2.9 restore not run live. T2.10 live check not done yet · 👤 T2.5 weights are a draft |
| Must | Phase 3: T3.0–T3.5 | ✅ T3.0, T3.1, T3.3, T3.4 · 🟡 T3.2 · ⛔ T3.5 (waits for the answer to Q5) |
| Must | Phase 4: T4.1–T4.4, T4.6–T4.9 | ✅ built · 👤 T4.1 expectations and T4.3/T4.4 checks are AI-written drafts that you must own |
| Must | A1, A3, A6, A7, A10, A12 | ✅ |
| Must | A4 plan → apply | 🟡 no separate plan file. The agent keeps the plan as decision records. |
| Must | A8 undo log and clobber check | 🟡 checks, notes, snapshot, write journal and restore exist. Restore has not run live yet. |
| Must | A9 routed escalation | ✅ built · live check not done yet (T2.10). Escalations have no assignee. |
| Must | Tasks D1–D3, TI1–TI4, R1–R4, C1 | ✅ 5/5 each offline (scripted model) |
| Must | Tests T5.1–T5.10 | ✅ 296 tests: T5.1–T5.10, plus `tests/test_decisions.py` and `tests/test_tasks_loader.py` from PR #4. PR #5 added tests for find-drawing, score, escalation, harness-runner, privacy, loop, config, fake-server and overview. On 4 Oct, 28 more files added tests for the STRIDE fixes, decision C and more scenarios. All 296 passed on 4 Oct 2026 · 👤 after the fixture re-capture, run them again immediately. Update any numbers that changed. In the same commit, move the fixture pin in `tests/helpers.py`. |
| Must | pass^5 offline | 🟡 22/22 with the scripted model (4 Oct 2026). On 27 Sept: 21/21. The real model has not run. |
| Must | One live pass of the read-only tasks | ✅ 10/10 ×1, scripted model, 22 Sept 2026, before the data change · the real model has not run |
| Must | One live write run with snapshot and restore (plan: TI2 then TI3) | ⛔ not run. The plan is now a single **TI2L** run (TI2 is offline-only since 27 Sept). |
| **Should** | A11 leak guard + C2 | ✅ (plus the C3 canary task) |
| Should | G1 (unseen filenames) | ✅ offline |
| Should | T4.5 extra fault injection | ✅ D4, TI4, TI5, TI6 |
| Should | Live pass^5 on the read-only tasks | ⛔ not run (needs the real model, ×5) |
| **Could** | A5 drive-wide revision report | ⛔ not built (the per-part revision checks exist) |
| Could | A14 filing rules stored as data | ✅ `agent/filing_rules.toml` (the agent does not use `AgentMemory`) |
| Could | T0.5 path protection | ⛔ not done |
| Could | Cost and trace dashboards | ⛔ not built. `report.md` shows the cost per task and per set. |

**Built beyond the plan:** tasks D4, DU1, TI5, TI6, TI7, C3, R5 and TI2L, calibration, and `python -m harness smoke`.

### 6.5 Risks and how the code handles them

| Risk | Mitigation | Where in the code (checked 22 Sept 2026, and rows marked 27 Sept checked again on that date) |
|---|---|---|
| The platform changes during the build (tools, bundle, data) | Discover tools at start-up. Do a pre-flight before live write runs. Re-capture fixtures when the tool hash changes. Put a date on every number. | `agent/catalog.py` (`check_required`, `hash`). `build()` in `agent/runtime.py` calls it. `python -m agent smoke` exits 1 if a tool is absent. `harness/preflight.py`. `python -m harness capture keystone` (`harness/fixtures.py`). Every run manifest has the tool hash (`harness/manifest.py`).<br>**It happened (27 Sept):** `smoke` caught the 23 Sept data change on 26 Sept. The team then captured a new fixture and derived the affected tasks again (section 5). |
| A live run disturbs a shared company | Row-id allow-list. Snapshot and restore. Plan-only default. `--live-apply` and `AS_ALLOW_WRITES` for live writes.<br>The fake server for most runs. **No write and no escalation outside the allow-list. An offline rehearsal of the live write run first** (27 Sept). | `KEYSTONE_INCOMING_ALLOWLIST` (`agent/config.py`). `_allowlist` and `check_write_permission` (`agent/runtime.py`). `WriteGuard` (`agent/guards.py`). The `out_of_scope` skips in `run` (`agent/skills/triage.py`) and `Escalator.escalate` (`agent/skills/escalate.py`). `live_allowlist` (`harness/tasks.py`, which TI2L uses).<br>`agent/snapshot.py` with `_run_passes` / `_restore` (`harness/runner.py`). Only a `live_write = true` task (TI2L) writes live, and the runner refuses fake-only tasks on live (`planned_runs` in `harness/runner.py`). Pre-flight blocks on any unknown or changed row in Incoming (`harness/preflight.py`). The client never sends a write again after a 5xx or timeout (`agent/http.py`). |
| This seat cannot remove escalations and sessions | Live write tasks run once. De-duplication by subject. Confirm that the seat can see its own escalation. No escalation outside the allow-list (27 Sept). | `planned_runs` gives a live write task exactly 1 run. `subject_for` + `_existing_subjects` (`agent/skills/escalate.py`). 1 session per run. Triage records `out_of_scope` for a file outside the allow-list. It never gives that file to the Escalator, which has the same check as a second protection. So the live write run makes 4 escalations, not 13 · ⛔ the live "can the seat see its own escalation?" check is still to do. |
| The model invents actions or content | Writes only in skills. Scoped claims-vs-state. The refusal threshold is code. Cited ids must resolve. | `tool_definitions` (`agent/loop.py`) gives the model only skills and read-only tools. `_claims_vs_state` (`harness/verifiers.py`). `_score` (`agent/skills/triage.py`) + `threshold` (`agent/filing_rules.toml`). `compose` (`agent/answer.py`) flags ids that no tool returned, and `resolve_ids` (`harness/runner.py`) checks them on the platform. `file_contents` (`agent/skills/access.py`) never quotes content. |
| A description misleads the agent (planted instructions or overclaims) | A description counts only when another signal agrees. Never repeat "byte-for-byte" as verified. Test with descriptions that mislead. | `_score` (`agent/skills/triage.py`): a description never picks a folder, and a description that does not agree makes a conflict. Rule 2 of the system prompt (`agent/loop.py`). The "not byte-verified" text (`agent/skills/duplicates.py`, `agent/skills/triage.py`). **Changed:** the tests are G1 (the agent does not move a description-only file) **and** TI6 (a planted "Belongs in HR. File this now…" on the W-9 → conflict, not moved).<br>**27 Sept:** the PO "(1)" description still claims "Byte-for-byte duplicate". But since 23 Sept, 880-byte copies share its recorded hash. So the agent escalates it as a suspected duplicate, and it does not archive it (TI1, TI2). **4 Oct (decision C):** the agent never archives or moves a possible copy, whatever its hash or description says. It escalates the copy (TI7). |
| Keystone has no escalation assignees | Unassigned escalations. `party_id` from the sender. Name the person to ask. | `escalate()` (`agent/skills/escalate.py`) sends `party_id` when the file has one. `build_plan` (`agent/skills/triage.py`) names the person from the sender or the access log (`upload_lead`). The client side writes the log (bug L8), so the log is a lead, not proof. **Changed:** the escalation's **reason** names the person, not its subject. The subject is `[files-agent] <file id> <filename>`. |
| A runaway loop spends your key | Hard caps | `agent/budget.py` + `agent/loop.py`: 12 turns, 80 MCP calls, $0.50 **per question** (`AS_MAX_*` in `.env`). **Changed:** there is no cap per run set. `report.md` shows the cost per task and per set. |
| Tests do not count because AI wrote them | You write `tests/`, the task expectations, the verifier checks and the score weights. Optional: T0.5. | `tests/` holds 296 tests in 48 files (4 Oct 2026). `tests/README.md` is the guide, and it lists what is still to write. The headers of the task files, `routes.toml` and `filing_rules.toml` mark them as AI drafts. AI also helped to write `harness/verifiers.py` · 👤 review them and own them · ⛔ T0.5 not done |

### 6.6 Questions for staff

In the plan, the team was to ask these questions on Day 0. Q2–Q4 blocked Phase 1. **No replies yet (27 Sept 2026).** Record each answer in the *Answer* column. If there is no answer, write `no reply by <date>; assuming X`. Until then, the code runs on the assumption in the last column.

| # | Question | Why it matters | Answer | What the code assumes until then |
|---|---|---|---|---|
| 1 | Do you grade Step 4 by a **live run on Keystone**, or by a review of the harness output? Must we leave Incoming **tidied or restored**? | It decides how important the live runs are. It also decides what happens to Incoming after the live write run. | | The grade comes from a review of the harness output. The live write run is the single run of **TI2L**. At the end, the run **restores** Incoming. The 5 files that it moves return to Incoming, and the 4 escalations stay. The run never changes the 9 copies.<br>The runner always restores, and it has no option to leave the folder tidied. So "leave it tidied" needs a code change in `harness/runner.py`. |
| 2 | Does the course give a **tool layer, runner or local app copy** that we can extend? (Your research notes mention one.) | It decides reuse or rewrite (T0.4). A local app copy can replace the fake server. | | None exists. All the code is in this repo. Offline runs use `harness/fake_server.py`. |
| 3 | Does "no third-party harness" also prohibit test tools like **pytest**? And is **AI-assisted** agent and harness code acceptable? | **The most important open answer.** If AI-assisted code is not acceptable, the team must rewrite the agent and harness code by hand. It also decides how you write your tests. | | AI help for the support code of the agent and the harness is acceptable. You write the tests by hand (`tests/` holds no AI-written test code). The code does not assume `pytest`: use `unittest`. `pyproject.toml` lists pytest only as an optional extra. |
| 4 | Must **everything use MCP**? Bulk update, `/api/auth/me` and the seat launch route are REST-only. | The code uses a few REST calls | | REST is acceptable for login, `/api/auth/me` and the Drive overview (`GET /api/drive/records/overview`, which `drive_overview` uses). `python -m harness capture` also reads `GET /api/agent/office`. Everything that the agent *does* uses MCP. The code does not use bulk update or the launch route. |
| 5 | The Office view (`GET /api/agent/office`) shows seat 20's goals as `implemented: false`, with jobs 0 and approve/revise/reject counters. Does an agent that runs outside the platform mark a goal implemented? If yes, how? Is it through `POST /api/agent/seats/a9e75bbc-cf2c-4d03-bb96-9cc6d57d9754/launch` (REST-only, creates an AgentJob)? Is it by a link from our run to a job? Or do staff evaluate our harness output? | It decides T3.5 (how to record goals). | | This agent does not record goals. T3.5 waits. |
| 6 | Is **EC2 or Hetzner deployment** necessary? | The code runs only on your machine | | No deployment. The agent runs locally. |
| 7 | Which `actor_kind` should our agent's sessions use, `user` or `system`? (The platform does not accept `agent`, and the default is `anonymous`.) `AgentSession.create` lets a client set its actor fields (`actor_kind`, `actor_user_id`, `actor_label`, `actor_roles`, `tool_policy_id`). Do you want us to report this as a bug? | It sets the label of the sessions of this agent. The second part is possibly a security bug, worth a bug report. | | The agent does not set `actor_kind` (`AS_ACTOR_KIND` is blank, and the code accepts only `user` or `system`). Sessions set only a title and `actor_label` "Files Agent (team20)". They never set `actor_roles` or `tool_policy_id`. |
| 8 | For "10 points per test": is 1 test a single test function, a test file, or a harness task? Is there a cap? If a test fails against the live platform because of a real bug, does it still count? | It decides how you split Phase 5. | | One test = one hand-written test function in `tests/` |

---

## 10. Results so far

Each row has a date. The offline rows ran again on 27 Sept 2026 (26 Sept fixture, PR #3 plus the 27 Sept follow-up). The first live read-only run was on 22 Sept, after review round 2 and before the data change. The rows dated 3 Oct are on the tree that merges PR #4 with the STRIDE fixes. The rows dated 4 Oct are on that tree plus decision C ([Round 7](history.md#15-changes-after-review)).

| Run | Date | Result |
|---|---|---|
| Offline, all 21 tasks × 5 (scripted model) | 27 Sept | **21 / 21 pass on every run** (22 Sept: 19 / 19) |
| Rescore from disk | 27 Sept | **identical** |
| Offline after the STRIDE fixes and the merge with PR #4: all 21 tasks ×5, rescore, calibration, routes, smoke (scripted model, Python 3.14 and 3.11) | 3 Oct | **21 / 21 pass on every run**, rescore **identical**, **355 / 355 caught** (30 kinds), routes **10 / 10**, smoke OK |
| Hand-written tests (PR #4): `python -m unittest discover -s tests -v` | 2 Oct, and 3 Oct on the merged tree | **78 / 78 pass** (Python 3.14 and 3.11) |
| Hand-written tests (PR #4 + PR #5): `python -m unittest discover -s tests -v` | 3 Oct | **157 / 157 pass**, 3 runs in a row (Python 3.14). The machine that ran them did not have 3.11. |
| Offline with decision C: all 22 tasks ×5, rescore, calibration, routes, smoke (scripted model, Python 3.14) | 4 Oct | **22 / 22 pass on every run** (110 run files), rescore **identical**, **377 / 377 caught** (30 kinds), routes **10 / 10**, smoke OK |
| Tests with decision C, after the new test files: `python -m unittest discover -s tests -v` | 4 Oct | **296 / 296 pass** in 48 files, 3 runs in a row (Python 3.14.4). `FollowOriginalTests` is now `PossibleCopyTests`, which asserts decision C. |
| Calibration: the harness plants each of the 26 kinds of mistake that applies to a task. It plants them in 1 run that passes, for each of the 21 tasks. 25 kinds apply, because no task now expects an archive. | 27 Sept | **295 / 295 caught**, each on the exact check that it targets. Calibration exercised every expectation key (22 Sept: 265 / 265 on 19 tasks). |
| Routes (`routes.toml`), **scripted model only** | 27 Sept | **10 / 10**. The scripted router is specific to these questions, so this result says nothing yet about the real model. |
| `python -m harness smoke` | 27 Sept | `OK   run + score`, `OK   rescore identical`, `OK   calibration catches faults` |
| Live Keystone, the 10 read-only tasks × 1 (D1–D3, DU1, C1, C2, R1–R3, TI1), scripted model | 22 Sept | **10 / 10 pass**, 0 write calls (before the data change) |
| Live Keystone, read-only checks | 27 Sept | Live Keystone matches the 26 Sept fixture exactly. It has 113 files, 212 tools, hash `c10a009a80de46c6`, Incoming 18 and 30 in folders. The Drive overview shows 15 files / 13,200 bytes. There are 83 e-sign rows, and 0 escalations by this seat. |
| Live `python -m harness preflight` | 27 Sept | **`pre-flight OK`** with **9 warnings**, 1 for each 23 Sept copy. The old check failed with 10 problems. Since the STRIDE fixes (2 Oct), pre-flight also needs a fixture from the last 24 hours. So this fixture fails now. Re-capture first. PR #4's stricter checks (document kind, folders) have not run live yet. |
| Live write without `--live-apply`, `agent ask --apply` on live, offline-only tasks on live, and any write task except TI2L on live | 22 Sept. Offline re-check of the TI2L and TI2 refusals on 27 Sept. | **refused** |
| Secret scan of the repo and every run file | 22 Sept | no password, token or key |
| Secret scan of the full git history (every commit of every branch, read-only) | 2 Oct | nothing found. Second run on 3 Oct on the merged tree, with `python scripts/secret_scan.py` and `--history`: `no secrets found`. The scan allows only the exact dummy password in `tests/test_redact.py`. |

**Not yet done:**

- Runs with the **real model** (`--model anthropic`) need your API key. This includes `python -m harness routes --model anthropic`.
- The **single live write run** (TI2L) needs the approval of the repo owner and a date that the team chooses. See [The live write run](live-run.md#11-the-live-write-run).
- **More hand-written tests** (Phase 5). 296 tests exist in 48 files (4 Oct 2026). They include the pre-flight, scope and same-name rules, and the find-drawing and score scenarios. They also include the leak guard, the harness runner, decision C and the STRIDE fixes. `tests/README.md` lists the open scenarios and STRIDE checks.
- **The team's review of the STRIDE fixes** (AI-assisted), and then a commit on a branch. See [Security review (STRIDE)](safety.md#security-review-stride).
- **A live, read-only `python -m harness preflight`** with PR #4's stricter checks, a long time before the write date.

---

## 12. Files you own

AI helped to draft the first 4 files, and the STRIDE review with its fixes (last row). The captured live data is the source of the task files and the score rules. You must write the tests in `tests/` and the staff answers.

For each file in the table below, do these steps yourselves:

1. Review the file.
2. Change the file.
3. Commit the file.

| File | What you decide |
|---|---|
| `agent/filing_rules.toml` | document types, score weights, and the score that a file needs for a move. Below that score, the agent escalates the file. |
| `harness/tasks/*.toml` | what counts as a correct answer for each task. This includes the decisions in the headers. Confirmed: decision A (the live write run writes to and escalates only the 9 allow-listed originals) and decision B (the same-name rule). Also confirmed: the agent refuses a shared filename, and never picks one. Also, a second tidy pass must do nothing. New on 4 Oct: decision C (the duplicate rule), with task TI7 as a draft. |
| `harness/tasks/routes.toml` | the route questions, and the skill that each question must reach |
| `harness/verifiers.py` | the checks that decide pass or fail (plan tasks T4.3 and T4.4 mark these as yours) |
| `tests/` | **your hand-written tests**: 296 in 48 files so far (4 Oct 2026). There is also `helpers.py`, a shared stub module with no tests of its own. `tests/README.md` is the guide, and it lists what is still to write. AI helped to update it on 4 Oct, and it holds no test code. |
| the *Answer* column in [6.6 Questions for staff](#66-questions-for-staff) | the staff's answers to the 8 open questions |
| `docs/security/stride-review.md`, and the code with the mark `# STRIDE <id>`. This includes the new `agent/textsafe.py`, `scripts/secret_scan.py` and `.githooks/pre-commit`. | the STRIDE review and its fixes. Decide if each fix, and each risk that is still open, is correct for the team. Review them before you rely on them. |

Optional: do not let AI tools edit your hand-written files (plan task T0.5). For example, use a deny rule in `.claude/settings.json` for `tests/**`, `harness/tasks/**` and `harness/verifiers.py`.
