"""Offline unit fixtures: these tests never contact a real Hue bridge."""
import copy
import io
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

try:
    from ambilight import hue
except ImportError:
    hue = None


class DiagnosticCopyTests(unittest.TestCase):
    def test_builtin_diagnostic_catalog_is_clear_english(self):
        # Pin every diagnostic template, including rare recovery failures that
        # must never reveal a remote description, owner, key, or saved state.
        import ast
        import inspect
        tree = ast.parse(inspect.getsource(hue))
        messages = set()
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call) and node.exc.args:
                value = node.exc.args[0]
            elif isinstance(node, ast.Assign) and any(
                    isinstance(target, ast.Name) and target.id in ('error', 'failure')
                    for target in node.targets):
                value = node.value
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.Mod):
                value = value.left
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                messages.add(value.value)
        self.assertEqual(messages, {
            'Invalid sequence.', 'Invalid lights or colors.',
            'An area requires 1 to 10 lights.', 'Invalid light ID.', 'Invalid RGB color.',
            'A private IPv4 address is required.', 'Invalid bridge pairing.',
            'Invalid Entertainment key.',
            'Restoration is pending. Release or recover the bridge first.',
            'Entertainment transport or bridge pairing is missing.',
            'Invalid Entertainment area.', 'This Entertainment area is already occupied.',
            'This Entertainment area changed or is occupied.',
            'Entertainment activation was not confirmed for this application.',
            'Unable to connect to Entertainment. Check the bridge and local transport.',
            'Restoration journal unavailable.', 'Entertainment transport is not ready.',
            'Entertainment transport was interrupted.', 'Entertainment transport is not active.',
            'Invalid Entertainment packet.', 'Entertainment stop was not confirmed.',
            'Light restoration was not confirmed.',
            'Release the current connection before recovering the bridge.',
            'Invalid restoration journal.', 'The area changed since the restoration journal was saved.',
            'A Hue BSB002 bridge is required for Entertainment.',
            'Pair the bridge before continuing.', 'Invalid area response.',
            'An Entertainment area is required.', 'Invalid area lights.',
            'Invalid light positions.', 'Invalid light position.', 'Invalid Entertainment state.',
            'Invalid Entertainment owner.', 'Invalid area name.', 'Invalid API path.',
            'Invalid API method.', 'The bridge returned an unexpected response (HTTP %d).',
            'Bridge response too large.', 'Invalid bridge response.',
            'Bridge unreachable. Check its IP address and the local network.',
            'Invalid light state.', 'Invalid restoration state.',
            'Press the bridge button, then try again within 30 seconds.',
            'Pairing rejected. Pair the bridge again.',
            'The bridge rejected the request (Hue error %d).', 'The bridge rejected the request.',
        })

    def test_unnamed_area_uses_an_english_fallback_without_changing_id(self):
        area = hue.HueBridge._area('123', {'type': 'Entertainment', 'lights': ['1']})
        self.assertEqual(area['name'], 'Area 123')
        self.assertEqual(area['id'], '123')


class PacketTests(unittest.TestCase):
    def test_android_v1_golden_bytes(self):
        self.assertIsNotNone(hue, "Hue packet implementation is missing")
        self.assertEqual(hue.packet(258, [1, 255], [(0, 128, 255), (1, 2, 3)]),
                         bytes.fromhex("48756553747265616d01000200000000"
                                       "00000100008080ffff0000ff010102020303"))

    def test_invalid_lamps_colors_and_sequence_are_rejected(self):
        cases = [(0, [], []), (0, list(range(1, 12)), [(0, 0, 0)] * 11),
                 (0, [1, 1], [(0, 0, 0)] * 2), (0, [0], [(0, 0, 0)]),
                 (0, [256], [(0, 0, 0)]), (0, [1], []),
                 (0, [True], [(0, 0, 0)]), (0, [1], [(0, -1, 0)]),
                 (0, [1], [(0, 256, 0)]), (0, [1], [(0, 1.0, 0)]),
                 (0, [1], [(0, True, 0)]), (0, [1], [(1, 2)]),
                 (1.0, [1], [(0, 0, 0)]), (True, [1], [(0, 0, 0)]),
                 (0, None, []), (0, [1], [None])]
        for args in cases:
            with self.subTest(args=args), self.assertRaises((ValueError, TypeError)):
                hue.packet(*args)


class AddressTests(unittest.TestCase):
    def test_bridge_requires_literal_rfc1918_address_and_valid_credentials(self):
        self.assertTrue(hasattr(hue, "HueBridge"), "HueBridge is missing")
        for ip in ("10.0.0.1", "172.16.0.1", "172.31.255.254", "192.168.2.1"):
            self.assertEqual(hue.HueBridge(ip).ip, ip)
        for ip in ("127.0.0.1", "169.254.1.1", "172.15.0.1", "172.32.0.1",
                   "8.8.8.8", "localhost", "192.168.001.1", "192.168.1.1:80",
                   "192.168.1.1/", "192.168.1.256", "::1", "10.1.1.1\n", None):
            with self.subTest(ip=ip), self.assertRaises(ValueError):
                hue.HueBridge(ip)
        for kwargs in ({"username": "bad/url"}, {"clientkey": "secret"}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                hue.HueBridge("10.0.0.1", **kwargs)


USER = "fixture_user_0123456789"
KEY = "0123456789abcdef0123456789abcdef"


class RequestTests(unittest.TestCase):
    def setUp(self):
        self.bridge = hue.HueBridge("10.0.0.2", USER, KEY)
        self.response = io.BytesIO(b'{"ok":true}')
        self.response.status = 200
        self.connection = Mock()
        self.connection.getresponse.return_value = self.response
        self.factory = patch("http.client.HTTPConnection", return_value=self.connection)
        self.factory_mock = self.factory.start()
        self.addCleanup(self.factory.stop)

    def require_request(self):
        self.assertTrue(hasattr(self.bridge, "request"), "Bounded REST transport is missing")

    def test_local_http_request_uses_bounded_socket_and_json(self):
        self.require_request()
        self.assertEqual(self.bridge.request("PUT", "/api/" + USER + "/groups/1",
                                             {"stream": {"active": True}}), {"ok": True})
        args, kwargs = self.factory_mock.call_args
        self.assertEqual(args[0], "10.0.0.2")
        self.assertGreater(kwargs["timeout"], 0)
        self.assertLessEqual(kwargs["timeout"], 6)
        call = self.connection.request.call_args
        self.assertEqual(call.args[:2], ("PUT", "/api/" + USER + "/groups/1"))
        self.assertEqual(json.loads(call.kwargs["body"]), {"stream": {"active": True}})
        self.connection.close.assert_called_once()

    def test_path_and_method_rejected_before_network(self):
        self.require_request()
        for path in ("/", "/api/", "/api//foo", "/api/a/../b", "/api/a?x=1",
                     "/api/a#x", "http://public/api", "/api/%2f", "/api/a\n"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.bridge.request("GET", path)
        with self.assertRaises(ValueError):
            self.bridge.request("CONNECT", "/api")
        self.factory_mock.assert_not_called()

    def test_redirect_is_denied_without_following_location(self):
        self.require_request()
        self.response.status = 302
        with self.assertRaisesRegex(RuntimeError, "HTTP 302"):
            self.bridge.request("GET", "/api/config")
        self.factory_mock.assert_called_once()

    def test_network_exception_never_leaks_tokens_or_url(self):
        self.require_request()
        self.connection.request.side_effect = OSError("http://10.0.0.2/api/" + USER + KEY)
        with self.assertRaises(RuntimeError) as raised:
            self.bridge.request("GET", "/api/" + USER + "/groups")
        self.assertNotIn(USER, str(raised.exception))
        self.assertNotIn(KEY, str(raised.exception))
        self.assertNotIn("http", str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.connection.close.assert_called_once()

    def test_response_over_one_megabyte_is_rejected(self):
        self.require_request()
        response = io.BytesIO(b" " * (1024 * 1024 + 1))
        response.status = 200
        self.connection.getresponse.return_value = response
        with self.assertRaisesRegex(RuntimeError, "too large"):
            self.bridge.request("GET", "/api/config")

    def test_deadline_keeps_socket_handle_when_http_detaches_connection(self):
        original_socket = self.connection.sock
        timer = Mock()
        with patch("threading.Timer", return_value=timer) as factory:
            def detached_response():
                self.connection.sock = None
                factory.call_args.args[1]()
                raise TimeoutError()
            self.connection.getresponse.side_effect = detached_response
            with self.assertRaises(RuntimeError):
                self.bridge.request("GET", "/api/config")
        original_socket.shutdown.assert_called_once()

    def test_http_response_is_closed_even_on_size_rejection(self):
        response = io.BytesIO(b"x" * (1024 * 1024 + 1))
        response.status = 200
        self.connection.getresponse.return_value = response
        with self.assertRaises(RuntimeError):
            self.bridge.request("GET", "/api/config")
        self.assertTrue(response.closed, "HTTP response body was not closed")

    def test_absolute_deadline_aborts_a_dribbling_http_header(self):
        timer = Mock()
        with patch("threading.Timer", return_value=timer) as factory:
            def dribbling_header():
                if factory.called:
                    callback = factory.call_args.args[1]
                    callback()
                    raise TimeoutError(USER)
                return self.response
            self.connection.getresponse.side_effect = dribbling_header
            try:
                self.bridge.request("GET", "/api/" + USER + "/groups")
            except RuntimeError:
                pass
            self.assertTrue(factory.called, "Absolute HTTP watchdog is missing")
            self.assertLessEqual(factory.call_args.args[0], 6)
            timer.start.assert_called_once()
            timer.cancel.assert_called_once()
            self.connection.sock.shutdown.assert_called()

    def test_hue_error_descriptions_are_never_exposed(self):
        for code, label in ((101, "bridge button"), (1, "Pairing rejected"), (901, "Hue error 901")):
            response = io.BytesIO(json.dumps([{"error": {"type": code,
                                         "description": USER + KEY, "address": "/api/" + USER}}]).encode())
            response.status = 200
            self.connection.getresponse.return_value = response
            with self.subTest(code=code), self.assertRaisesRegex(RuntimeError, label) as raised:
                self.bridge.request("POST", "/api")
            self.assertNotIn(USER, str(raised.exception))
            self.assertNotIn(KEY, str(raised.exception))

    def test_malformed_response_is_sanitized(self):
        self.require_request()
        for data in (USER.encode(), b"123", b"null", b"{", b"\xff"):
            response = io.BytesIO(data)
            response.status = 200
            self.connection.getresponse.return_value = response
            with self.subTest(data=data), self.assertRaises(RuntimeError) as raised:
                self.bridge.request("GET", "/api/config")
            self.assertNotIn(USER, str(raised.exception))
            self.assertIsNone(raised.exception.__context__)


class FixtureBridge(hue.HueBridge):
    """In-memory bridge state, not evidence of real hardware success."""
    def __init__(self, journal_path=None, java_command=None):
        super().__init__("10.0.0.2", USER, KEY, java_command, journal_path)
        self.calls = []
        self.model = "BSB002"
        self.pair_result = [{"success": {"username": USER, "clientkey": KEY}}]
        self.groups = {
            "1": {"type": "Entertainment", "name": "Desk", "lights": ["1", "2"],
                  "locations": {"1": [-0.7, 0, 0], "2": [0.8, 0, 0]},
                  "stream": {"active": False, "owner": None}},
            "2": {"type": "Entertainment", "name": "Screen", "lights": ["3"],
                  "locations": {}, "stream": {"active": False, "owner": None}},
            "3": {"type": "Room", "name": "Kitchen", "lights": ["4"]}}
        self.lights = {
            "1": {"on": True, "bri": 140, "colormode": "xy", "xy": [0.2, 0.3], "reachable": True},
            "2": {"on": False, "bri": 50, "colormode": "ct", "ct": 300},
            "3": {"on": True, "bri": 80, "colormode": "hs", "hue": 12000, "sat": 180},
            "4": {"on": True}}
        self.activation_owner = USER
        self.before_activate = None

    def request(self, method, path, json_body=None):
        self.calls.append((method, path, copy.deepcopy(json_body)))
        if (method, path) == ("GET", "/api/config"):
            return {"modelid": self.model}
        if (method, path) == ("POST", "/api"):
            return copy.deepcopy(self.pair_result)
        prefix = "/api/" + USER
        if not path.startswith(prefix):
            raise AssertionError("Unexpected fixture path")
        parts = path[len(prefix):].strip("/").split("/")
        if parts == ["groups"] and method == "GET":
            return copy.deepcopy(self.groups)
        if parts[0] == "groups" and len(parts) == 2:
            group = self.groups[parts[1]]
            if method == "GET":
                return copy.deepcopy(group)
            if method == "PUT":
                if json_body["stream"]["active"]:
                    if self.before_activate:
                        self.before_activate()
                    group["stream"] = {"active": True, "owner": self.activation_owner}
                else:
                    group["stream"]["active"] = False
                return [{"success": {path + "/stream/active": json_body["stream"]["active"]}}]
        if parts[0] == "lights" and len(parts) == 2 and method == "GET":
            return {"state": copy.deepcopy(self.lights[parts[1]])}
        if parts[0] == "lights" and len(parts) == 3 and parts[2] == "state" and method == "PUT":
            self.lights[parts[1]].update(copy.deepcopy(json_body))
            return [{"success": {path + "/on": json_body["on"]}}]
        raise AssertionError("Unexpected fixture request")


class PairTests(unittest.TestCase):
    def test_pair_rejects_wrong_models_and_malformed_credentials(self):
        for model in ("BSB001", "BSB003", "not_hue", None):
            bridge = FixtureBridge()
            bridge.model = model
            with self.subTest(model=model), self.assertRaises(RuntimeError):
                bridge.pair()
            self.assertEqual(len(bridge.calls), 1)
        for result in ([], {}, [{"success": {"username": "bad/url", "clientkey": KEY}}],
                       [{"success": {"username": USER, "clientkey": "not-a-key"}}],
                       [{"error": {"type": 101, "description": USER + KEY}}]):
            bridge = FixtureBridge()
            bridge.pair_result = result
            with self.subTest(result=result), self.assertRaises(RuntimeError) as raised:
                bridge.pair()
            self.assertNotIn(USER, str(raised.exception))
            self.assertNotIn(KEY, str(raised.exception))

    def test_pair_returns_validated_credentials_from_bsb002(self):
        bridge = FixtureBridge()
        self.assertTrue(hasattr(bridge, "pair"), "Pairing is missing")
        self.assertEqual(bridge.pair(), {"ip": "10.0.0.2", "username": USER, "clientkey": KEY})
        self.assertEqual(bridge.calls[0], ("GET", "/api/config", None))
        self.assertEqual(bridge.calls[1][0:2], ("POST", "/api"))
        self.assertIs(bridge.calls[1][2]["generateclientkey"], True)


class AreaTests(unittest.TestCase):
    def test_malformed_entertainment_light_ids_and_positions_rejected(self):
        for lights, locations in (([], {}), (["1", "1"], {}), (["256"], {}),
                                  (["01"], {}), ([True], {}),
                                  (["1"], {"1": [float("nan")]}),
                                  (["1"], {"1": [1.1]}), (["1"], {"1": [True]})):
            bridge = FixtureBridge()
            bridge.groups["1"]["lights"] = lights
            bridge.groups["1"]["locations"] = locations
            with self.subTest(lights=lights, locations=locations), self.assertRaises(ValueError):
                bridge.areas()

    def test_only_entertainment_groups_map_lights_positions_and_ownership(self):
        bridge = FixtureBridge()
        bridge.groups["1"]["stream"] = {"active": True, "owner": "other_app"}
        self.assertTrue(hasattr(bridge, "areas"), "Area discovery is missing")
        self.assertEqual(bridge.areas(), [
            {"id": "1", "name": "Desk", "ids": [1, 2], "xs": [-0.7, 0.8],
             "active": True, "owner": "other_app"},
            {"id": "2", "name": "Screen", "ids": [3], "xs": [0.0],
             "active": False, "owner": None}])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Path(self.directory.name) / "restore.json"
        self.bridge = FixtureBridge(self.journal, ["java", "-cp", "fixture", "StreamMain"])
        self.process = Mock()
        self.process.stdin = io.BytesIO()
        self.process.stdout = io.BytesIO(b"READY\n")
        self.process.poll.return_value = None
        self.process.wait.return_value = 0
        self.launch = patch("subprocess.Popen", return_value=self.process)
        self.launch_mock = self.launch.start()
        self.addCleanup(self.launch.stop)

    def require_connect(self):
        self.assertTrue(hasattr(self.bridge, "connect"), "Stream lifecycle is missing")

    def test_takeover_after_initial_release_read_skips_deactivation(self):
        self.bridge.connect("1")
        self.bridge.calls.clear()
        request = self.bridge.request
        reads = 0
        def takeover(method, path, json_body=None):
            nonlocal reads
            result = request(method, path, json_body)
            if method == "GET" and path.endswith("/groups/1"):
                reads += 1
                if reads == 1:
                    # The first response is already a stale snapshot when the
                    # caller receives it; the bridge now belongs to another app.
                    self.bridge.groups["1"]["stream"] = {
                        "active": True, "owner": "another_application"}
            return result
        with patch.object(self.bridge, "request", side_effect=takeover):
            self.bridge.release()
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.assertFalse(self.bridge.active)
        self.assertTrue(self.journal.exists())
        self.assertTrue(json.loads(self.journal.read_text()).get("restore_blocked", False))
        self.assertFalse(self.bridge._restored)

    def test_takeover_during_deactivation_preserves_journal_without_restoring(self):
        self.bridge.connect("1")
        saved_journal = self.journal.read_bytes()
        self.bridge.lights["1"]["bri"] = 1
        self.bridge.calls.clear()
        request = self.bridge.request
        def takeover(method, path, json_body=None):
            result = request(method, path, json_body)
            if method == "PUT" and path.endswith("/groups/1"):
                # Ownership changed while our deactivation was in flight.
                # Its result leaves an inactive stream with a foreign owner.
                self.bridge.groups["1"]["stream"] = {
                    "active": False, "owner": "another_application"}
            return result
        with patch.object(self.bridge, "request", side_effect=takeover):
            self.bridge.release()
        writes = [c for c in self.bridge.calls if c[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][2], {"stream": {"active": False}})
        self.assertEqual(self.bridge.lights["1"]["bri"], 1)
        self.assertTrue(self.journal.exists())
        saved = json.loads(saved_journal)
        retained = json.loads(self.journal.read_text())
        self.assertEqual(retained["snapshots"], saved["snapshots"])
        self.assertTrue(retained.get("restore_blocked", False))
        self.assertFalse(self.bridge._restored)

    def test_takeover_between_lamps_blocks_stale_restoration_after_restart(self):
        self.bridge.connect("1")
        self.bridge.lights["1"]["bri"] = 1
        self.bridge.lights["2"]["bri"] = 2
        self.bridge.calls.clear()
        request = self.bridge.request
        def takeover(method, path, json_body=None):
            result = request(method, path, json_body)
            if method == "GET" and path.endswith("/lights/1"):
                # Lamp one was restored, but the next group check must see
                # takeover and prevent the write for lamp two.
                self.bridge.groups["1"]["stream"] = {
                    "active": True, "owner": "another_application"}
            return result
        with patch.object(self.bridge, "request", side_effect=takeover):
            self.bridge.release()
        lamp_writes = [c[1] for c in self.bridge.calls
                       if c[0] == "PUT" and "/lights/" in c[1]]
        self.assertEqual(lamp_writes, ["/api/" + USER + "/lights/1/state"])
        self.assertEqual(self.bridge.lights["2"]["bri"], 2)
        self.assertTrue(self.journal.exists())
        self.assertFalse(self.bridge._restored)
        # A later own-looking stream cannot prove continuity. Never replay
        # snapshots made before the observed takeover, even after a restart.
        restarted = FixtureBridge(self.journal)
        restarted.groups["1"]["stream"] = {"active": False, "owner": USER}
        self.assertFalse(restarted.recover())
        self.assertFalse(any(c[0] == "PUT" for c in restarted.calls))
        self.assertTrue(self.journal.exists())

    def test_takeover_after_last_lamp_verification_keeps_uncertain_journal(self):
        self.bridge.connect("1")
        request = self.bridge.request
        def takeover(method, path, json_body=None):
            result = request(method, path, json_body)
            if method == "GET" and path.endswith("/lights/2"):
                self.bridge.groups["1"]["stream"] = {
                    "active": True, "owner": "another_application"}
            return result
        with patch.object(self.bridge, "request", side_effect=takeover):
            self.bridge.release()
        self.assertTrue(self.journal.exists())
        self.assertFalse(self.bridge._restored)
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.assertTrue(json.loads(self.journal.read_text()).get("restore_blocked", False))

    def test_recovery_rejects_malformed_restoration_block_marker(self):
        self.bridge.connect("1")
        self.bridge._stop_transport()
        original = json.loads(self.journal.read_text())
        for value in (0, 1, None, "false", [], {}):
            document = copy.deepcopy(original)
            document["restore_blocked"] = value
            self.journal.write_text(json.dumps(document))
            restarted = FixtureBridge(self.journal)
            restarted.groups["1"]["stream"] = {"active": False, "owner": USER}
            with self.subTest(value=value), self.assertRaisesRegex(
                    RuntimeError, "Invalid restoration journal"):
                restarted.recover()
            self.assertFalse(restarted.calls)
            self.assertTrue(self.journal.exists())

    def test_blocked_recovery_keeps_journal_without_consulting_bridge(self):
        self.bridge.connect("1")
        self.bridge.groups["1"]["stream"] = {
            "active": True, "owner": "another_application"}
        self.bridge.release()
        restarted = FixtureBridge(self.journal)
        with patch.object(restarted, "request", wraps=restarted.request) as request:
            self.assertFalse(restarted.recover())
        request.assert_not_called()
        self.assertTrue(self.journal.exists())
        with self.assertRaisesRegex(RuntimeError, "Restoration is pending"):
            restarted.connect("1")

    def test_recover_returns_false_if_owner_changes_before_release(self):
        self.bridge.connect("1")
        self.bridge._stop_transport()
        restarted = FixtureBridge(self.journal)
        restarted.groups = copy.deepcopy(self.bridge.groups)
        request = restarted.request
        reads = {"count": 0}
        def takeover(method, path, json_body=None):
            if method == "GET" and path.endswith("/groups/1"):
                reads["count"] += 1
                if reads["count"] == 2:
                    restarted.groups["1"]["stream"]["owner"] = "another_application"
            return request(method, path, json_body)
        with patch.object(restarted, "request", side_effect=takeover):
            self.assertFalse(restarted.recover())
        self.assertFalse(any(c[0] == "PUT" for c in restarted.calls))
        self.assertTrue(restarted.groups["1"]["stream"]["active"])

    def test_restoration_waits_for_short_hue_transition_before_verifying(self):
        self.bridge.connect("2")
        request = self.bridge.request
        pending = {"count": 0}
        def transition(method, path, json_body=None):
            result = request(method, path, json_body)
            if method == "PUT" and path.endswith("/lights/3/state"):
                pending["count"] = 1
            if method == "GET" and path.endswith("/lights/3") and pending["count"]:
                pending["count"] -= 1
                result["state"]["bri"] = 1
            return result
        with patch.object(self.bridge, "request", side_effect=transition), patch("time.sleep"):
            self.bridge.release()
        self.assertFalse(self.journal.exists())
        self.assertEqual(self.bridge.lights["3"]["bri"], 80)
        self.assertEqual(self.bridge.lights["3"]["hue"], 12000)
        self.assertEqual(self.bridge.lights["3"]["sat"], 180)

    def test_invalid_snapshot_cannot_be_persisted_or_activated(self):
        self.bridge.lights["1"]["bri"] = 999
        with self.assertRaises(ValueError):
            self.bridge.connect("1")
        self.assertFalse(self.journal.exists())
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))

    def test_cli_eof_or_unsafe_output_never_counts_as_ready(self):
        self.process.stdout = io.BytesIO((USER + KEY + "\n").encode())
        with self.assertRaises(RuntimeError) as raised:
            self.bridge.connect("1")
        self.assertNotIn(USER, str(raised.exception))
        self.assertNotIn(KEY, str(raised.exception))
        self.assertIsNone(raised.exception.__context__)
        self.assertFalse(self.bridge.active)
        self.assertFalse(self.bridge.groups["1"]["stream"]["active"])
        self.assertFalse(self.journal.exists())

    def test_cli_ready_wait_has_fourteen_second_deadline(self):
        class BlockedOutput:
            def __init__(self):
                import threading
                self.done = threading.Event()
            def readline(self, limit):
                self.done.wait(1)
                return b""
            def close(self):
                self.done.set()
        output = BlockedOutput()
        self.process.stdout = output
        # No fourteen-second sleep: advance only the readiness deadline clock.
        started = time.monotonic()
        with patch("ambilight.hue.time.monotonic", side_effect=[100.0, 114.01]):
            with self.assertRaises(RuntimeError):
                self.bridge.connect("1")
        self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(output.done.is_set())
        self.assertFalse(self.bridge.active)
        self.assertFalse(self.bridge.groups["1"]["stream"]["active"])

    def test_hung_cli_is_killed_after_bounded_grace_period(self):
        self.bridge.connect("1")
        import subprocess
        self.process.wait.side_effect = [subprocess.TimeoutExpired("fixture", 2), 0]
        self.bridge.release()
        self.process.kill.assert_called_once()
        self.assertTrue(all(c.kwargs["timeout"] <= 2 for c in self.process.wait.call_args_list))

    def test_journal_creation_failure_prevents_activation(self):
        self.bridge.journal_path = Path(self.directory.name) / "missing" / "restore.json"
        with self.assertRaisesRegex(RuntimeError, "Restoration journal"):
            self.bridge.connect("1")
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))
        self.launch_mock.assert_not_called()

    def test_release_retains_journal_on_rest_failure_for_later_recovery(self):
        self.bridge.connect("1")
        request = self.bridge.request
        def offline(method, path, json_body=None):
            if method == "GET":
                raise RuntimeError("Offline fixture")
            return request(method, path, json_body)
        with patch.object(self.bridge, "request", side_effect=offline):
            with self.assertRaises(RuntimeError):
                self.bridge.release()
        self.assertFalse(self.bridge.active)
        self.assertTrue(self.journal.exists())
        self.bridge.release()
        self.assertFalse(self.journal.exists())

    def test_broken_cli_write_stops_stream_with_sanitized_error(self):
        self.bridge.connect("1")
        self.process.stdin = Mock()
        self.process.stdin.write.side_effect = OSError(USER + KEY)
        with self.assertRaises(RuntimeError) as raised:
            self.bridge.send(hue.packet(0, [1, 2], [(0, 0, 0)] * 2))
        self.assertNotIn(USER, str(raised.exception))
        self.assertNotIn(KEY, str(raised.exception))
        self.assertFalse(self.bridge.active)
        self.assertFalse(self.bridge.groups["1"]["stream"]["active"])
        self.assertFalse(self.journal.exists())

    def test_tampered_journal_cannot_restore_unselected_or_unsafe_state(self):
        self.bridge.connect("1")
        self.bridge._stop_transport()
        original = json.loads(self.journal.read_text())
        variants = []
        extra_light = copy.deepcopy(original)
        extra_light["snapshots"]["4"] = {"on": False, "transitiontime": 4}
        variants.append(extra_light)
        unsafe_state = copy.deepcopy(original)
        unsafe_state["snapshots"]["1"]["alert"] = "select"
        variants.append(unsafe_state)
        invalid_color = copy.deepcopy(original)
        invalid_color["snapshots"]["1"]["xy"] = [float("nan"), 0.3]
        variants.append(invalid_color)
        path_injection = copy.deepcopy(original)
        path_injection["snapshots"]["1/state"] = path_injection["snapshots"].pop("1")
        variants.append(path_injection)
        for document in variants:
            self.journal.write_text(json.dumps(document))
            restarted = FixtureBridge(self.journal)
            restarted.groups = copy.deepcopy(self.bridge.groups)
            with self.subTest(document=document), self.assertRaises(RuntimeError):
                restarted.recover()
            self.assertFalse(any(c[0] == "PUT" for c in restarted.calls))
            self.assertTrue(self.journal.exists())

    def test_occupied_stream_refused_without_any_writes(self):
        for owner in (USER, "another_application"):
            self.bridge.groups["1"]["stream"] = {"active": True, "owner": owner}
            with self.subTest(owner=owner), self.assertRaisesRegex(RuntimeError, "occupied"):
                self.bridge.connect("1")
        self.assertFalse(any(c[0] != "GET" for c in self.bridge.calls))
        self.assertFalse(self.journal.exists())
        self.launch_mock.assert_not_called()

    def test_activation_verifies_exact_owner_before_launching_cli(self):
        self.bridge.activation_owner = "another_application"
        with self.assertRaises(RuntimeError):
            self.bridge.connect("1")
        self.assertFalse(self.bridge.active)
        self.launch_mock.assert_not_called()
        writes = [c for c in self.bridge.calls if c[0] == "PUT"]
        self.assertEqual(len(writes), 1)
        self.assertIs(writes[0][2]["stream"]["active"], True)
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.assertEqual(self.bridge.groups["1"]["stream"]["owner"], "another_application")

    def test_release_skips_writes_when_foreign_ownership_is_observed(self):
        self.bridge.connect("1")
        self.bridge.groups["1"]["stream"] = {"active": True, "owner": "another_application"}
        self.bridge.calls.clear()
        self.bridge.release()
        self.assertFalse(self.bridge.active)
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.assertTrue(self.journal.exists())
        self.assertTrue(json.loads(self.journal.read_text())["restore_blocked"])
        self.bridge.groups["1"]["stream"] = {"active": False, "owner": USER}
        self.bridge.calls.clear()
        self.bridge.release()
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))
        self.assertTrue(self.journal.exists())
        self.assertFalse(self.bridge._restored)

    def test_recover_preserves_journal_when_foreign_ownership_is_observed(self):
        self.bridge.connect("1")
        self.bridge._stop_transport()  # Simulate process loss without releasing REST state.
        restarted = FixtureBridge(self.journal)
        restarted.groups["1"]["stream"] = {"active": True, "owner": "another_application"}
        self.assertFalse(restarted.recover())
        self.assertFalse(any(c[0] == "PUT" for c in restarted.calls))
        self.assertTrue(restarted.groups["1"]["stream"]["active"])
        self.assertTrue(self.journal.exists())
        self.assertTrue(json.loads(self.journal.read_text())["restore_blocked"])

    def test_selection_cannot_activate_a_non_entertainment_group(self):
        for area_id in ("3", "../1", "1/", "0", "01", "1\n"):
            with self.subTest(area_id=area_id), self.assertRaises(ValueError):
                self.bridge.connect(area_id)
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))

    def test_ownership_is_rechecked_after_snapshot_before_activation(self):
        persist = self.bridge._persist
        def another_app_wins(record):
            persist(record)
            self.bridge.groups["1"]["stream"] = {"active": True, "owner": "another_application"}
        with patch.object(self.bridge, "_persist", side_effect=another_app_wins):
            with self.assertRaises(RuntimeError):
                self.bridge.connect("1")
        self.assertFalse(any(c[0] == "PUT" for c in self.bridge.calls))
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.launch_mock.assert_not_called()

    def test_crash_after_activation_recovers_pre_activation_journal(self):
        self.assertTrue(hasattr(self.bridge, "recover"), "Crash recovery is missing")
        with patch.object(self.bridge, "_start_transport", side_effect=BaseException("fixture crash")):
            with self.assertRaises(BaseException):
                self.bridge.connect("1")
        self.assertTrue(self.journal.exists())
        self.assertTrue(self.bridge.groups["1"]["stream"]["active"])
        self.bridge.lights["1"]["bri"] = 1
        restarted = FixtureBridge(self.journal)
        restarted.groups = copy.deepcopy(self.bridge.groups)
        restarted.lights = copy.deepcopy(self.bridge.lights)
        self.assertTrue(restarted.recover())
        self.assertFalse(restarted.groups["1"]["stream"]["active"])
        self.assertEqual(restarted.lights["1"]["bri"], 140)
        self.assertFalse(self.journal.exists())
        self.assertFalse(restarted.active)
        self.assertFalse(restarted.recover())
        self.launch_mock.assert_not_called()

    def test_frames_go_only_to_cli_hex_stdin(self):
        self.require_connect()
        self.assertTrue(hasattr(self.bridge, "send"), "Frame transport is missing")
        with self.assertRaises(RuntimeError):
            self.bridge.send(b"x")
        self.bridge.connect("2")
        data = hue.packet(0, [3], [(255, 100, 0)])
        call_count = len(self.bridge.calls)
        self.bridge.send(data)
        self.assertEqual(self.process.stdin.getvalue().splitlines()[1], data.hex().encode())
        self.assertEqual(len(self.bridge.calls), call_count)
        self.bridge.release()

    def test_selected_area_connect_and_release_restore_only_its_snapshots(self):
        self.require_connect()
        self.assertFalse(self.bridge.active)
        before = copy.deepcopy(self.bridge.lights)
        def check_before_activation():
            document = json.loads(self.journal.read_text())
            self.assertEqual(document["area_id"], "1")
            self.assertEqual(set(document["snapshots"]), {"1", "2"})
            self.assertNotIn(KEY, self.journal.read_text())
        self.bridge.before_activate = check_before_activation
        self.bridge.connect("1")
        self.assertTrue(self.bridge.active)
        self.assertEqual(json.loads(self.process.stdin.getvalue()),
                         {"ip": "10.0.0.2", "username": USER, "clientkey": KEY})
        self.assertNotIn(USER, str(self.launch_mock.call_args))
        self.assertNotIn(KEY, str(self.launch_mock.call_args))
        self.assertEqual(self.launch_mock.call_args.kwargs["stderr"], -3)
        if os.name != "nt":
            self.assertEqual(self.journal.stat().st_mode & 0o777, 0o600)
        self.bridge.lights["1"].update({"on": False, "bri": 1, "xy": [0.6, 0.4]})
        self.bridge.lights["2"].update({"on": True, "bri": 1, "ct": 150})
        self.bridge.release()
        self.assertFalse(self.bridge.active)
        self.assertFalse(self.bridge.groups["1"]["stream"]["active"])
        self.assertFalse(self.journal.exists())
        self.assertTrue(self.process.stdin.closed)
        self.assertEqual(self.bridge.lights["1"]["xy"], before["1"]["xy"])
        self.assertEqual(self.bridge.lights["1"]["on"], before["1"]["on"])
        self.assertEqual(self.bridge.lights["2"]["ct"], before["2"]["ct"])
        self.assertEqual(self.bridge.lights["2"]["on"], before["2"]["on"])
        self.assertEqual(self.bridge.lights["3"], before["3"])
        self.assertEqual(self.bridge.lights["4"], before["4"])
        state_writes = [c for c in self.bridge.calls if c[0] == "PUT" and "/lights/" in c[1]]
        self.assertEqual(len(state_writes), 2)
        self.assertTrue(all(set(c[2]) <= {"on", "bri", "xy", "ct", "hue", "sat", "transitiontime"}
                            for c in state_writes))
        reads = [c[1] for c in self.bridge.calls if c[0] == "GET" and "/lights/" in c[1]]
        self.assertFalse(any("/lights/3" in p or "/lights/4" in p for p in reads))
        self.bridge.release()
        self.assertEqual(len([c for c in self.bridge.calls if c[0] == "PUT" and "/lights/" in c[1]]), 2)


if __name__ == "__main__":
    unittest.main()
