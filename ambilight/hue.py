"""Hue v1 local REST control and HueStream packet generation.

REST uses plaintext HTTP on an explicitly validated RFC1918 IPv4 address,
matching Hue's local v1 API. No proxy, redirect, or relaxed TLS is used.
Credentials belong only in local REST paths and the CLI's stdin, never logs
or process arguments. Continuous colors go exclusively over the DTLS CLI.
"""
import http.client
import ipaddress
import json
import math
import os
import queue
import subprocess
import threading
import uuid
import time
import re
import struct
from pathlib import Path

_PRIVATE_NETWORKS = tuple(ipaddress.IPv4Network(n) for n in
                          ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"))


def _valid_user(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_-]{10,100}", value) is not None


def _valid_key(value):
    return isinstance(value, str) and re.fullmatch(r"[A-Fa-f0-9]{32}", value) is not None

RGBtuple = tuple[int, int, int]


def packet(sequence: int, ids: list[int], rgb: list[RGBtuple]) -> bytes:
    """Encode the Android Core.packet HueStream v1 wire format."""
    if type(sequence) is not int:
        raise ValueError("Invalid sequence.")
    if not isinstance(ids, list) or not isinstance(rgb, list):
        raise ValueError("Invalid lights or colors.")
    if not 1 <= len(ids) <= 10 or len(rgb) != len(ids):
        raise ValueError("An area requires 1 to 10 lights.")
    seen = set()
    result = bytearray(b"HueStream\x01\x00" + bytes([sequence & 255]) + b"\x00" * 4)
    for light_id, color in zip(ids, rgb):
        if type(light_id) is not int or not 1 <= light_id <= 255 or light_id in seen:
            raise ValueError("Invalid light ID.")
        seen.add(light_id)
        if (not isinstance(color, (tuple, list)) or len(color) != 3 or
                any(type(c) is not int or not 0 <= c <= 255 for c in color)):
            raise ValueError("Invalid RGB color.")
        result.extend(struct.pack(">BH3H", 0, light_id, *(c * 257 for c in color)))
    return bytes(result)


class HueBridge:
    """Control one Hue v2 bridge through its local v1 API."""

    def __init__(self, ip: str, username: str | None = None,
                 clientkey: str | None = None, java_command: list[str] | None = None,
                 journal_path: Path | None = None):
        try:
            address = ipaddress.IPv4Address(ip) if isinstance(ip, str) else None
        except ipaddress.AddressValueError:
            address = None
        if address is None or not any(address in net for net in _PRIVATE_NETWORKS):
            raise ValueError("A private IPv4 address is required.")
        if username is not None and not _valid_user(username):
            raise ValueError("Invalid bridge pairing.")
        if clientkey is not None and not _valid_key(clientkey):
            raise ValueError("Invalid Entertainment key.")
        self.ip = ip
        self.username = username
        self.clientkey = clientkey
        self.java_command = list(java_command) if java_command else None
        self.journal_path = (Path(journal_path) if journal_path is not None else
                             Path.home() / (".hue-server-restore-" + ip + ".json"))
        self._process = None
        self._record = None
        self._active = False
        self._restored = False

    @property
    def active(self):
        return self._active and self._process is not None and self._process.poll() is None

    def connect(self, area_id: str) -> None:
        if self._record is not None or self.journal_path.exists():
            raise RuntimeError("Restoration is pending. Release or recover the bridge first.")
        if not self.java_command or not _valid_key(self.clientkey):
            raise RuntimeError("Entertainment transport or bridge pairing is missing.")
        if not isinstance(area_id, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", area_id):
            raise ValueError("Invalid Entertainment area.")
        group_path = self._path("/groups/" + area_id)
        area = self._area(area_id, self.request("GET", group_path))
        if area["active"]:
            raise RuntimeError("This Entertainment area is already occupied.")
        snapshots = {}
        for light_id in area["ids"]:
            light = self.request("GET", self._path("/lights/" + str(light_id)))
            state = light.get("state") if isinstance(light, dict) else None
            snapshots[str(light_id)] = _snapshot(state)
        record = {"version": 1, "ip": self.ip, "username": self.username,
                  "area_id": area_id, "snapshots": snapshots}
        self._persist(record)
        self._record = record
        failure = None
        try:
            current = self._area(area_id, self.request("GET", group_path))
            if current["active"] or current["ids"] != area["ids"]:
                raise RuntimeError("This Entertainment area changed or is occupied.")
            result = self.request("PUT", group_path, {"stream": {"active": True}})
            _check_hue_errors(result)
            confirmed = self._area(area_id, self.request("GET", group_path))
            if not confirmed["active"] or confirmed["owner"] != self.username:
                raise RuntimeError("Entertainment activation was not confirmed for this application.")
            self._start_transport()
            self._active = True
        except Exception:
            failure = "Unable to connect to Entertainment. Check the bridge and local transport."
            try:
                self.release()
            except Exception:
                # Preserve the pre-activation journal for a later recovery.
                pass
        if failure:
            raise RuntimeError(failure)

    def _persist(self, record):
        temporary = self.journal_path.with_name(self.journal_path.name + "." + uuid.uuid4().hex + ".tmp")
        failed = False
        try:
            # Caller supplies a private writable state directory; do not create it.
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as output:
                json.dump(record, output, allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.journal_path)
        except Exception:
            failed = True
        finally:
            temporary.unlink(missing_ok=True)
        if failed:
            raise RuntimeError("Restoration journal unavailable.")

    def _start_transport(self):
        deadline = time.monotonic() + 14.0
        self._process = subprocess.Popen(self.java_command, stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                         bufsize=0, shell=False)
        process = self._process
        ready = queue.Queue(maxsize=1)
        def read_ready():
            try:
                ready.put(process.stdout.readline(256))
            except Exception:
                ready.put(b"")
        threading.Thread(target=read_ready, daemon=True).start()
        credentials = {"ip": self.ip, "username": self.username, "clientkey": self.clientkey}
        self._write_line(json.dumps(credentials).encode("utf-8") + b"\n", timeout=3.0)
        remaining = max(0, deadline - time.monotonic())
        try:
            line = ready.get(timeout=remaining)
        except queue.Empty:
            line = b""
        if line not in (b"READY\n", b"READY\r\n") or process.poll() is not None:
            raise RuntimeError("Entertainment transport is not ready.")

    def _write_line(self, line, timeout=3.0):
        process = self._process
        completed = queue.Queue(maxsize=1)
        def write():
            try:
                view = memoryview(line)
                while view:
                    count = process.stdin.write(view)
                    if not count:
                        raise OSError()
                    view = view[count:]
                process.stdin.flush()
                completed.put(True)
            except Exception:
                completed.put(False)
        threading.Thread(target=write, daemon=True).start()
        try:
            success = completed.get(timeout=timeout)
        except queue.Empty:
            success = False
        if not success:
            raise RuntimeError("Entertainment transport was interrupted.")

    def send(self, data: bytes) -> None:
        """Send one frame to the DTLS transport without any per-frame REST."""
        if not self.active:
            raise RuntimeError("Entertainment transport is not active.")
        if not isinstance(data, bytes) or not 25 <= len(data) <= 106:
            raise ValueError("Invalid Entertainment packet.")
        failed = False
        try:
            self._write_line(data.hex().encode("ascii") + b"\n")
        except Exception:
            failed = True
            try:
                self.release()
            except Exception:
                pass
        if failed:
            raise RuntimeError("Entertainment transport was interrupted.")

    def _stop_transport(self):
        process, self._process = self._process, None
        self._active = False
        if process is None:
            return
        try:
            process.stdin.close()
            process.wait(timeout=2.0)
        except Exception:
            try:
                process.kill()
                process.wait(timeout=2.0)
            except Exception:
                pass
        finally:
            for pipe in (process.stdin, process.stdout):
                try:
                    pipe.close()
                except Exception:
                    pass

    def release(self) -> None:
        """Stop local transport; recheck ownership before best-effort release."""
        self._stop_transport()
        self._restored = False
        record = self._record
        if record is None or record.get("restore_blocked", False):
            return
        area_id = record["area_id"]
        path = self._path("/groups/" + area_id)
        area = self._area(area_id, self.request("GET", path))
        if area["owner"] != self.username:
            self._defer_restoration()
            return
        if area["active"]:
            # Narrow the stale-read window. This is not an atomic owner check:
            # a takeover after this GET can still race the following Hue v1 PUT.
            area = self._area(area_id, self.request("GET", path))
            if area["owner"] != self.username:
                self._defer_restoration()
                return
            if area["active"]:
                _check_hue_errors(self.request("PUT", path, {"stream": {"active": False}}))
            area = self._area(area_id, self.request("GET", path))
            if area["owner"] not in (None, "", self.username):
                self._defer_restoration()
                return
            if area["active"]:
                raise RuntimeError("Entertainment stop was not confirmed.")
        for light_id, snapshot in record["snapshots"].items():
            area = self._area(area_id, self.request("GET", path))
            if area["active"] or area["owner"] not in (None, "", self.username):
                self._defer_restoration()
                return
            # Hue v1 GET/PUT are separate requests, not an atomic owner fence.
            light_path = self._path("/lights/" + light_id)
            _check_hue_errors(self.request("PUT", light_path + "/state", snapshot))
            for attempt in range(20):
                observed = self.request("GET", light_path)
                state = observed.get("state", {}) if isinstance(observed, dict) else {}
                if all(state.get(k) == v for k, v in snapshot.items() if k != "transitiontime"):
                    break
                if attempt == 19:
                    raise RuntimeError("Light restoration was not confirmed.")
                # Hue transitions are asynchronous (transitiontime is deciseconds).
                time.sleep(0.1)
        # Do not discard the journal if ownership changed during the final
        # lamp's transition/verification. Like every GET, this is advisory.
        area = self._area(area_id, self.request("GET", path))
        if area["active"] or area["owner"] not in (None, "", self.username):
            self._defer_restoration()
            return
        self._forget_record()
        self._restored = True

    def recover(self) -> bool:
        """Recover a previous crash only while the selected stream is ours."""
        if self.active or self._record is not None:
            raise RuntimeError("Release the current connection before recovering the bridge.")
        if not self.journal_path.exists():
            return False
        failed = False
        try:
            with self.journal_path.open("rb") as source:
                data = source.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError()
            record = json.loads(data)
            if not isinstance(record, dict) or record.get("version") != 1:
                raise ValueError()
            if type(record.get("restore_blocked", False)) is not bool:
                raise ValueError()
            if record.get("ip") != self.ip or record.get("username") != self.username:
                raise ValueError()
            area_id = record.get("area_id")
            if not isinstance(area_id, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", area_id):
                raise ValueError()
            snapshots = record.get("snapshots")
            if not isinstance(snapshots, dict) or not 1 <= len(snapshots) <= 10:
                raise ValueError()
            for light_id, state in snapshots.items():
                if not isinstance(light_id, str) or not re.fullmatch(r"[1-9][0-9]{0,2}", light_id):
                    raise ValueError()
                if int(light_id) > 255:
                    raise ValueError()
                _validate_snapshot(state)
        except Exception:
            failed = True
        if failed:
            raise RuntimeError("Invalid restoration journal.")
        if record.get("restore_blocked", False):
            return False
        area = self._area(area_id, self.request("GET", self._path("/groups/" + area_id)))
        if set(record["snapshots"]) != {str(i) for i in area["ids"]}:
            raise RuntimeError("The area changed since the restoration journal was saved.")
        self._record = record
        owned = area["owner"] == self.username
        if not owned:
            self._defer_restoration()
            return False
        self.release()
        return self._restored

    def _defer_restoration(self):
        """Retain snapshots for review, never automatically replay after takeover."""
        # Fence this instance even if persisting the marker fails; the original
        # journal survives _persist's atomic replacement on ordinary failures.
        self._record["restore_blocked"] = True
        self._persist(self._record)

    def _forget_record(self):
        failed = False
        try:
            self.journal_path.unlink(missing_ok=True)
        except OSError:
            failed = True
        if failed:
            raise RuntimeError("Restoration journal unavailable.")
        self._record = None

    def pair(self):
        """Pair using the bridge button and request an Entertainment PSK."""
        config = self.request("GET", "/api/config")
        if not isinstance(config, dict) or config.get("modelid") != "BSB002":
            raise RuntimeError("A Hue BSB002 bridge is required for Entertainment.")
        result = self.request("POST", "/api", {"devicetype": "hue_server_sync#server",
                                                  "generateclientkey": True})
        _check_hue_errors(result)
        success = (result[0].get("success") if isinstance(result, list) and result
                   and isinstance(result[0], dict) else None)
        if not isinstance(success, dict):
            raise RuntimeError("Invalid bridge pairing.")
        username, clientkey = success.get("username"), success.get("clientkey")
        if not _valid_user(username) or not _valid_key(clientkey):
            raise RuntimeError("Invalid bridge pairing.")
        self.username, self.clientkey = username, clientkey
        return {"ip": self.ip, "username": username, "clientkey": clientkey}

    def _path(self, suffix):
        if not _valid_user(self.username):
            raise RuntimeError("Pair the bridge before continuing.")
        return "/api/" + self.username + suffix

    def areas(self):
        groups = self.request("GET", self._path("/groups"))
        if not isinstance(groups, dict):
            raise RuntimeError("Invalid area response.")
        return [self._area(area_id, group) for area_id, group in groups.items()
                if isinstance(group, dict) and group.get("type") == "Entertainment"]

    @staticmethod
    def _area(area_id, group):
        if not isinstance(area_id, str) or not re.fullmatch(r"[1-9][0-9]{0,5}", area_id):
            raise ValueError("Invalid Entertainment area.")
        if not isinstance(group, dict) or group.get("type") != "Entertainment":
            raise ValueError("An Entertainment area is required.")
        lights = group.get("lights")
        if not isinstance(lights, list):
            raise ValueError("Invalid area lights.")
        ids = []
        for value in lights:
            if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,2}", value):
                raise ValueError("Invalid light ID.")
            ids.append(int(value))
        packet(0, ids, [(0, 0, 0)] * len(ids))
        locations = group.get("locations", {})
        if not isinstance(locations, dict):
            raise ValueError("Invalid light positions.")
        xs = []
        for light_id in ids:
            position = locations.get(str(light_id), [0.0])
            x = position[0] if isinstance(position, list) and position else None
            if (type(x) not in (int, float) or not math.isfinite(x) or not -1 <= x <= 1):
                raise ValueError("Invalid light position.")
            xs.append(float(x))
        stream = group.get("stream", {})
        if not isinstance(stream, dict) or type(stream.get("active", False)) is not bool:
            raise ValueError("Invalid Entertainment state.")
        owner = stream.get("owner")
        if owner is not None and not isinstance(owner, str):
            raise ValueError("Invalid Entertainment owner.")
        name = group.get("name", "Area " + area_id)
        if not isinstance(name, str):
            raise ValueError("Invalid area name.")
        return {"id": area_id, "name": name, "ids": ids, "xs": xs,
                "active": stream.get("active", False), "owner": owner}

    def request(self, method, path, json_body=None):
        """Bounded plaintext local control; all remote failures are secret-safe."""
        if not isinstance(path, str) or not re.fullmatch(r"/api(?:/[A-Za-z0-9_-]+)*", path):
            raise ValueError("Invalid API path.")
        if method not in ("GET", "POST", "PUT", "DELETE"):
            raise ValueError("Invalid API method.")
        connection = None
        request_socket = None
        response = None
        watchdog = None
        error = None
        result = None
        try:
            deadline = time.monotonic() + 6.0
            connection = http.client.HTTPConnection(self.ip, timeout=3.5)
            def abort_request():
                try:
                    sock = request_socket if request_socket is not None else connection.sock
                    if sock is not None:
                        sock.shutdown(2)
                    connection.close()
                except Exception:
                    pass
            watchdog = threading.Timer(6.0, abort_request)
            watchdog.daemon = True
            watchdog.start()
            body = None if json_body is None else json.dumps(json_body, allow_nan=False).encode("utf-8")
            connection.request(method, path, body=body, headers={"Content-Type": "application/json"})
            request_socket = connection.sock
            response = connection.getresponse()
            if response.status != 200:
                error = "The bridge returned an unexpected response (HTTP %d)." % response.status
            else:
                data = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError()
                    if connection.sock is not None:
                        connection.sock.settimeout(min(remaining, 3.5))
                    block = response.read1(min(4096, 1024 * 1024 + 1 - len(data)))
                    if not block:
                        break
                    data.extend(block)
                    if len(data) > 1024 * 1024:
                        error = "Bridge response too large."
                        break
                if error is None:
                    result = json.loads(data)
                    if not isinstance(result, (dict, list)):
                        error = "Invalid bridge response."
        except Exception:
            # Raise outside except: even __context__ cannot carry the REST URL.
            error = "Bridge unreachable. Check its IP address and the local network."
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if response is not None:
                try:
                    response.close()
                except Exception:
                    error = "Bridge unreachable. Check its IP address and the local network."
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    error = "Bridge unreachable. Check its IP address and the local network."
        if error:
            raise RuntimeError(error)
        _check_hue_errors(result)
        return result


def _snapshot(state):
    if not isinstance(state, dict) or type(state.get("on")) is not bool:
        raise ValueError("Invalid light state.")
    saved = {"on": state["on"], "transitiontime": 4}
    if "bri" in state:
        saved["bri"] = state["bri"]
    mode = state.get("colormode")
    if mode == "xy" and "xy" in state:
        saved["xy"] = list(state["xy"])
    elif mode == "ct" and "ct" in state:
        saved["ct"] = state["ct"]
    elif "hue" in state and "sat" in state:
        saved["hue"], saved["sat"] = state["hue"], state["sat"]
    _validate_snapshot(saved)
    return saved


def _validate_snapshot(state):
    allowed = {"on", "bri", "xy", "ct", "hue", "sat", "transitiontime"}
    if (not isinstance(state, dict) or not set(state) <= allowed or
            type(state.get("on")) is not bool or
            type(state.get("transitiontime")) is not int or state["transitiontime"] != 4):
        raise ValueError("Invalid restoration state.")
    for key, low, high in (("bri", 0, 254), ("ct", 1, 65535),
                           ("hue", 0, 65535), ("sat", 0, 254)):
        if key in state and (type(state[key]) is not int or not low <= state[key] <= high):
            raise ValueError("Invalid restoration state.")
    if "xy" in state:
        xy = state["xy"]
        if (not isinstance(xy, list) or len(xy) != 2 or
                any(type(v) not in (int, float) or not math.isfinite(v) or not 0 <= v <= 1 for v in xy)):
            raise ValueError("Invalid restoration state.")
    if (sum(k in state for k in ("xy", "ct", "hue")) > 1 or
            ("hue" in state) != ("sat" in state)):
        raise ValueError("Invalid restoration state.")


def _check_hue_errors(result):
    if not isinstance(result, list):
        return
    for item in result:
        if isinstance(item, dict) and "error" in item:
            error = item["error"]
            code = error.get("type") if isinstance(error, dict) else None
            if code == 101:
                raise RuntimeError("Press the bridge button, then try again within 30 seconds.")
            if code == 1:
                raise RuntimeError("Pairing rejected. Pair the bridge again.")
            if type(code) is int and 0 <= code <= 9999:
                raise RuntimeError("The bridge rejected the request (Hue error %d)." % code)
            raise RuntimeError("The bridge rejected the request.")
