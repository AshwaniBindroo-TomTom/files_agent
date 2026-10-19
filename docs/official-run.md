# The official server run (Release 8.1)

Since Release 8.1 (9 Oct 2026), the course grades the harness on the AgentSwitch server:
**Our harness → Submit for a run**. The server checks out the exact commit on the saved
branch, runs it against a **fresh copy** of the instance, throws the writes away afterwards,
and shows pass/fail per task. One run per team every 3 days, so rehearse first.

## What the server provides

No passwords, no `.env`, no own keys. The runner sets:

| Variable | Use in this repo |
|---|---|
| `AGENTSWITCH_BASE_URL` | overrides the instance URL (`get_settings` in `agent/config.py`) |
| `AGENTSWITCH_TOKEN` | a ready bearer token: `TokenSession` in `agent/auth.py` uses it instead of a login; a 401 is fatal because the token cannot be refreshed |
| `AGENTSWITCH_INSTANCE` | `keystone` (Seat 20's scenario data exists only there; `agentswitch-harness.toml` lists only keystone) |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY`, `OPENAI_MODEL` | the platform model. `OpenAICompatModel` in `agent/model.py` speaks the OpenAI chat-completions API over plain HTTPS — still standard library only, no SDK |

## What `python -m harness official` does

1. **Captures a fresh fixture** from the instance copy (read-only), so pre-flight's
   "fixture older than 24 hours" rule cannot block the write task.
2. **Runs every live-safe task once** with the platform model: the read-mode tasks without
   fake-server faults or planted files, plus **TI2L**, the one `live_write = true` task.
   Nothing is weakened: the write guard, the 9-id allow-list, pre-flight, snapshot, the
   write journal and restore all run exactly as in a live write run. The only switch this
   command flips is `AS_ALLOW_WRITES=1`, inside its own process, because the server copy
   is disposable by design.
3. **Writes `results.json`** in the runner's format (id, title, passed, score, evidence),
   checked against that format before writing. Evidence states that pass/fail comes from
   the database state after the run, or quotes the verifier's failure reasons.

The offline-only tasks (fault injection, planted files, the 18-file TI2/TI3 variants)
stay out **by design**: their faults need the fake server. They are graded by the offline
`pass^5` evidence, not by the official run.

## Rehearse before submitting (the run quota is 1 per 3 days)

- Offline, no keys, no network: `python -m harness official --dry-run`
  (fake server + scripted model). Since 12 Oct it caps the write allow-list to the 9 originals,
  as the server's live target does. With the cap it gives 11 of 12: TI1 fails exactly as it did
  on the server, because its expectations assume the uncapped 18-file allow-list. The uncapped
  dry-run had given 12 of 12 on 9 Oct.
- The scripted model repeats each skill's answer word for word, so the dry-run cannot test the
  answer-wording checks (`answer_must_mention` and similar). Only a real model run does.

## Results so far

| Run | Date | Commit | Result | Cause |
|---|---|---|---|---|
| `39458e84` | 9 Oct | `8a8fb4e` | 0 / 12 | the runner's `http://sandbox-keystone:8000` was refused by the https-only check (fixed in PR #11) |
| `3f13e97c` | 12 Oct | `e3491e1` | 7 / 12 | C1, C2, R3, R5: the platform model reworded the skill answers, so wording checks failed. TI1: expectations assume the uncapped allow-list. Database-state checks and TI2L passed. |
| `c2224f4d` | 15 Oct | `9771f79` | 6 / 12 | C1: live Keystone has 114 files. C2: the model wrote its tool call as text (DSML). R3, R5, TI1: wording. TI2L: `AgentEscalation.create` was gone, so pre-flight refused it. Fixed in PR #13 and PR #14. |
| `99f28b88` | 19 Oct | `1d2b431` | 0 / 12 | the course's model provider key had expired (HTTP 401 `upstream_error`); the staff marked it as their error, so it did not count |
| `1bc6cc49` | 19 Oct | `1d2b431` | 10 / 12 | C1, C2, R3, R5 pass now. TI1: the 2 copies under the cap (open). TI2L: skipped (`needs_tools`), since create is gone; the raise path is in the next PR. |

The run files stay on the server. Each task's answer excerpt is printed to the log, and a failed
task's evidence shows the answer next to the failure reason.
- Against the real platform with your own token: export `AGENTSWITCH_BASE_URL`,
  `AGENTSWITCH_TOKEN` (from a normal login) and any OpenAI-compatible model endpoint,
  then run `python -m harness official`.

## Before the first submission (checklist)

1. Merge this branch and **save the repo + branch in "Our harness"** on the platform.
2. `python -m harness official --dry-run` passes on the saved commit.
3. `python -m unittest discover -s tests` and `python -m harness smoke` pass.
4. Expect drift: the fresh copy is seeded by the course, and the platform data changed
   mid-course once already (23 Sept). Treat the first run partly as reconnaissance; the
   per-task evidence in the panel says exactly which expectation disagreed.
