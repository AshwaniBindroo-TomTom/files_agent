# STRIDE E3 + I12 for the mcp client
# E3: only our 3 write tools and read only tools get sent, any other write tool could skip the write guard and get resent on a 5xx
# I12: platform error text that may quote a record never reaches the model, the records or the trace
import json
import unittest
from agent.http import TransportError
from agent.loop import run_agent
from agent.mcp_client import McpClient, McpError, safe_error_text
from agent.redact import Redactor
from agent.skills import triage
from agent.trace import Trace
from harness.fake_server import FakeServer
from helpers import FIXTURE_DIR, build_runtime

WRITE_TOOL_REFUSED = "write_tool_refused"  # data_code when the client refuses a tool
# not read only in the 26 Sept catalogue and not one of our 3 write tools, each with its required args
OTHER_WRITE_TOOLS = {
    "DriveFolder.create": {"name": "E3 folder"},
    "DriveShare.share_file": {"file_id": "e3-file"},
    "AgentTask.run_now": {"id": "e3-task"},
    "endpoint.agent_governance.escalations.update": {"escalation_id": "e3-escalation", "action": "withdraw"},
}
NOT_IN_CATALOGUE = "NotInCatalogue.create"  # made up, catalogue doesnt have it
CATALOGUE_ONLY_READ = "AgentTask.list"  # read only per catalogue but not one of our read tools

WITHHELD = "the platform's message is withheld because it may quote record data"
ESIGN_FILE_ID = "eff13c89-3309-4622-861a-903758c6194b"  # esign row this seat cant open
WITHHELD_TITLE = "Offer Letter - Jane Doe CANARY-I12.pdf"  # made up title for it
LEAKY_ERROR = f"FileAttachment {ESIGN_FILE_ID} ({WITHHELD_TITLE}) belongs to EsignDocument; not permitted"
MISSING_FILE_ID = "00000000-0000-0000-0000-000000000000"  # no such file so fake says "Record not found"
TIMED_OUT = "POST /api/mcp failed: TimeoutError: timed out"  # what HttpTransport raises when all attempts time out
TIDY_MOVES = 5  # 26 Sept tidy moves 5 files out of Incoming


class RecordingSession:
    # fake Session, notes every json-rpc request and answers with an empty object

    def __init__(self):
        self.sent = []  # (method, tool, idempotent) per request

    def request(self, method, path, body=None, idempotent=True):
        self.sent.append((body["method"], body["params"].get("name"), idempotent))
        return 200, {"jsonrpc": "2.0", "id": body["id"], "result": {"content": [{"type": "text", "text": "{}"}]}}


class CannedSession:
    # fake Session, 200 + the same reply every time

    def __init__(self, reply):
        self.reply = reply

    def request(self, method, path, body=None, idempotent=True):
        return 200,self.reply


class DeadSession:
    # fake Session for when the platform cant be reached, every request times out

    def request(self, method, path, body=None, idempotent=True):
        raise TransportError(TIMED_OUT)


class LeakyServer(FakeServer):
    # tools in leaky_tools fail with an error that quotes a withheld title

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.leaky_tools = set()

    def _rpc(self, body):
        name = (body.get("params") or {}).get("name")
        if body.get("method") != "tools/call" or name not in self.leaky_tools:
            return super()._rpc(body)
        self.calls.append(name)
        error = {"code": -32003, "message": LEAKY_ERROR, "data": {"code": "forbidden"}}
        return {"jsonrpc": "2.0", "id": body.get("id"), "error": error}


class OneReadModel:
    # fake LLM, FileAttachment.get on one id then stops, keeps every tool result

    name = "stand-in"

    def __init__(self, file_id):
        self.file_id = file_id
        self.tool_results = []

    def create(self, system, messages, tools):
        last = messages[-1]["content"]
        if isinstance(last, str):  # first turn = the question
            use = {"type": "tool_use", "id": "toolu_1", "name": "mcp__FileAttachment__get", "input": {"id": self.file_id}}
            return {"content": [use], "stop_reason": "tool_use", "usage": {}}
        self.tool_results.extend(block["content"] for block in last if block.get("type") == "tool_result")
        return {"content": [{"type": "text", "text": "done"}], "stop_reason": "end_turn", "usage": {}}


def _leaky_runtime(mode, tool):
    # runtime on the fake server where `tool` fails with the title quoting error after build
    server = LeakyServer.from_fixture("keystone", FIXTURE_DIR)
    rt, _ = build_runtime(mode, server=server)
    server.leaky_tools.add(tool)
    return rt


class UnlistedWriteToolTests(unittest.TestCase):  # E3

    def test_write_refused_before_catalogue(self):
        # DriveFolder.create before the catalogue loads -> write_tool_refused, nothing sent
        session = RecordingSession()
        trace = Trace(None, Redactor())
        client = McpClient(session, trace)
        with self.assertRaises(McpError) as refused:
            client.call("DriveFolder.create", {"name": "E3 folder"})
        self.assertEqual(refused.exception.data_code, WRITE_TOOL_REFUSED)
        self.assertTrue(refused.exception.own)
        self.assertEqual(session.sent, [])
        self.assertEqual([e["tool"] for e in trace.of_kind("write_blocked")], ["DriveFolder.create"])

    def test_non_readonly_tools_refused(self):
        # 4 write tools outside our 3 + one unknown tool all refused, none reach the server
        rt, server = build_runtime("plan")
        calls_before = list(server.calls)
        for name, args in {**OTHER_WRITE_TOOLS, NOT_IN_CATALOGUE: {}}.items():
            with self.subTest(tool=name):
                with self.assertRaises(McpError) as refused:
                    rt.mcp.call(name, args)
                self.assertEqual(refused.exception.data_code, WRITE_TOOL_REFUSED)
        self.assertEqual(server.calls, calls_before)
        self.assertEqual(server.write_log, [])
        self.assertEqual([e["tool"] for e in rt.trace.of_kind("write_blocked")], [*OTHER_WRITE_TOOLS, NOT_IN_CATALOGUE])

    def test_allowed_tools_go_out(self):
        # read only tool goes out as safe to resend, our 3 write tools once each and never resent
        session = RecordingSession()
        client = McpClient(session, Trace(None, Redactor()))
        for name in ("FileAttachment.list", "FileAttachment.update", "AgentSession.create", "AgentEscalation.create"):
            client.call(name, {})
        self.assertEqual(session.sent, [("tools/call", "FileAttachment.list", True),
                                        ("tools/call", "FileAttachment.update", False),
                                        ("tools/call", "AgentSession.create", False),
                                        ("tools/call", "AgentEscalation.create", False)])

    def test_catalogue_readonly_goes_out(self):
        # AgentTask.list is read only per catalogue only, still gets sent
        rt, server = build_runtime("plan")
        rt.mcp.call(CATALOGUE_ONLY_READ, {})
        self.assertEqual(server.calls[-1], CATALOGUE_ONLY_READ)
        self.assertEqual(rt.trace.of_kind("write_blocked"), [])


class WithheldErrorTextTests(unittest.TestCase):  # I12

    def test_error_text_withheld(self):
        # error reply or isError result quoting a title -> withheld from error text and trace
        error_reply = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32003, "message": LEAKY_ERROR, "data": {"code": "forbidden"}}}
        is_error_reply = {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": LEAKY_ERROR}], "isError": True}}
        for label, reply in (("error reply", error_reply), ("isError result", is_error_reply)):
            with self.subTest(reply=label):
                trace = Trace(None, Redactor())
                client = McpClient(CannedSession(reply), trace)
                with self.assertRaises(McpError) as failed:
                    client.call("FileAttachment.get", {"id": ESIGN_FILE_ID})
                self.assertEqual(safe_error_text(failed.exception), WITHHELD)
                self.assertEqual([e["error"] for e in trace.of_kind("mcp_call")], [WITHHELD])
                self.assertNotIn(WITHHELD_TITLE, json.dumps(trace.events, ensure_ascii=False))

    def test_model_gets_withheld_note(self):
        # get fails with title quoting text, model only gets the withheld note
        rt = _leaky_runtime("plan", "FileAttachment.get")
        model = OneReadModel(ESIGN_FILE_ID)
        run_agent(f"What is in file {ESIGN_FILE_ID}?", rt.ctx, model, rt.budget, rt.trace)
        self.assertEqual(model.tool_results, [f"Tool error (forbidden): {WITHHELD}"])
        self.assertNotIn(WITHHELD_TITLE, json.dumps(rt.trace.events, ensure_ascii=False))

    def test_records_dont_quote_platform(self):
        # every tidy update fails with title quoting text, each failed move just says withheld
        rt = _leaky_runtime("apply", "FileAttachment.update")
        answer = triage.run(rt.ctx, {})["answer_text"]
        errors = [r.details["error"] for r in rt.ctx.records.all() if r.action == "move" and r.status == "failed"]
        self.assertEqual(errors, [WITHHELD] * TIDY_MOVES)
        self.assertNotIn(WITHHELD_TITLE, json.dumps(rt.ctx.records.to_list(), ensure_ascii=False, default=str))
        self.assertNotIn(WITHHELD_TITLE, answer)
        self.assertNotIn(WITHHELD_TITLE, json.dumps(rt.trace.events, ensure_ascii=False))

    def test_record_not_found_passes(self):
        # missing id -> model gets 'Record not found', that text quotes no record
        rt, _ = build_runtime("plan")
        model = OneReadModel(MISSING_FILE_ID)
        run_agent(f"What is in file {MISSING_FILE_ID}?", rt.ctx, model, rt.budget, rt.trace)
        self.assertEqual(model.tool_results, ["Tool error (not_found): Record not found"])

    def test_own_message_in_trace(self):
        # platform unreachable, error + trace keep our own text
        trace = Trace(None, Redactor())
        client = McpClient(DeadSession(), trace)
        with self.assertRaises(McpError) as failed:
            client.call("FileAttachment.list", {})
        self.assertEqual(failed.exception.data_code, "transport_error")
        self.assertEqual(safe_error_text(failed.exception), f"platform unreachable: {TIMED_OUT}")
        self.assertEqual([e["error"] for e in trace.of_kind("mcp_call")], [f"platform unreachable: {TIMED_OUT}"])


if __name__ == "__main__":
    unittest.main()
