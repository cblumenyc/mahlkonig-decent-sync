"""Read-only E64 WS Owner client and Xenia feedback validation.

Based on firmware V01.16-24; see the compatibility documentation.
The development-only AddBrewEvent command is deliberately not exposed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import re
import sqlite3
import time
import uuid

import aiohttp

MAX_DATABASE_BYTES = 32 * 1024 * 1024
MAX_RESPONSE_BYTES = 256 * 1024


async def bounded_body(response, limit):
    if response.content_length is not None and response.content_length > limit:
        raise BridgeError('Grinder response exceeds the supported size')
    body = bytearray()
    async for chunk in response.content.iter_chunked(65536):
        body.extend(chunk)
        if len(body) > limit:
            raise BridgeError('Grinder response exceeds the supported size')
    return bytes(body)


class BridgeError(Exception):
    pass


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def decode(value):
    return value.decode("utf-8") if isinstance(value, bytes) else value


def json_value(value):
    value = decode(value)
    return json.loads(value) if value else None


def snapshot(database: bytes) -> dict:
    """Read only coffee-related fields; never return Wi-Fi or password settings."""
    if len(database) > MAX_DATABASE_BYTES or not database.startswith(b"SQLite format 3\0"):
        raise BridgeError("Grinder did not return a SQLite database")
    db = sqlite3.connect(":memory:")
    try:
        # The export endpoint checkpoints WAL before returning the main file.
        # SQLite's in-memory deserialize cannot reopen WAL files. Change only
        # this disposable copy's read/write-version flags to rollback format.
        exported = bytearray(database)
        exported[18:20] = b"\x01\x01"
        db.deserialize(bytes(exported))
        db.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 2
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        db.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 1024 * 1024)
        db.row_factory = sqlite3.Row

        def rows(query):
            return [{k: decode(v) for k, v in dict(r).items()} for r in db.execute(query)]

        def setting(key):
            row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return json_value(row[0]) if row else None

        grinds = rows("""SELECT g.grind_id, g.grind_uuid, g.grind_date_time,
            g.recipe_index, g.recipe_data_id, g.grind_ddd, g.grind_weight,
            g.grind_successfull, g.recipe_gbs_enabled,
            d.recipe_type_id, d.recipe_brew_time, d.recipe_brew_weight
            FROM grind_log g LEFT JOIN recipe_data d USING(recipe_data_id)
            ORDER BY grind_id DESC LIMIT 30""")
        recipes = rows("""SELECT e.recipe_index, e.recipe_active, e.recipe_data_id,
            e.recipe_gbs_data, d.recipe_type_id, d.recipe_coarseness,
            d.recipe_brew_time, d.recipe_brew_weight, d.recipe_amount
            FROM recipe_entries e JOIN recipe_data d USING(recipe_data_id)
            ORDER BY recipe_index""")
        states = rows("SELECT recipe_index,brew_device,group_head,data FROM gbs_algo_state")
        for r in recipes:
            r["recipe_gbs_data"] = json_value(r["recipe_gbs_data"])
        for r in states:
            r["data"] = json_value(r["data"])
        return {
            "captured_at": now_iso(), "grinds": grinds,
            "brews": rows("""SELECT brew_id,brew_uuid,brew_device_id,brew_group,
                brew_dose_index,brew_dose_type_id,brew_stop_target,brew_validity,
                brew_duration,brew_volume,brew_mass,grind_id
                FROM brew_events ORDER BY brew_id DESC LIMIT 1000"""),
            "recipes": recipes, "algorithm_states": states,
            "motion_alert_events": rows("""SELECT l.alert_id,t.alert_type_name,
                l.alert_date_time,l.alert_active,l.alert_related_id
                FROM alert_log l JOIN alert_types t USING(alert_type_id)
                WHERE t.alert_type_name IN ('AgsaBlockage','AgsaTimeout','AgsaFaultPin',
                    'AgsaBlockagePrediction','AgsaOvercurrent','AgsaBlockedByHopper','AgsaLimitLowerHit')
                ORDER BY l.alert_id DESC LIMIT 20"""),
            "pending_brew": setting("GbS.State.PendingBrewInfo"),
            "grind_size_prediction": setting("Gbs.State.EffectiveGrindSizePrediction"),
        }
    finally:
        db.close()


def fingerprint(state):
    fields = {k: state[k] for k in ("recipes", "algorithm_states", "pending_brew")}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def integer(value, name, low, high):
    if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
        raise BridgeError(f"{name} must be an integer from {low} to {high}")
    return value


def normalize_shot(shot):
    if not isinstance(shot, dict):
        raise BridgeError("Shot must be an object")
    if shot.get("source") not in ("de1", "manual", "synthetic-de1"):
        raise BridgeError("Unknown shot source")
    shot_id = shot.get("shot_id")
    if not isinstance(shot_id, str) or not 1 <= len(shot_id) <= 120:
        raise BridgeError("Provide a stable shot_id")
    if shot.get("valid") is not True:
        raise BridgeError("Aborted or invalid shots cannot drive adjustment")
    duration = integer(shot.get("duration_ms"), "duration_ms", 10001, 80000)
    try:
        weight = Decimal(str(shot.get("weight_g")))
        if not weight.is_finite() or not Decimal("1") <= weight <= Decimal("250"):
            raise InvalidOperation
        weight_mg = int((weight * 1000).quantize(Decimal("1")))
    except (InvalidOperation, ValueError):
        raise BridgeError("weight_g must be a finite beverage mass from 1 to 250 g") from None
    return {"source": shot["source"], "shot_id": shot_id,
            "duration_ms": duration, "weight_mg": weight_mg, "valid": True}


def make_plan(state, shot, grind_id=None, *, current_time=None, record_only=False):
    shot = normalize_shot(shot)
    pending = state["pending_brew"] or {}
    if record_only and pending:
        raise BridgeError("Record-only mode requires no pending native adjustment")
    if grind_id is None:
        grind_id = state["grinds"][0]["grind_id"] if record_only and state["grinds"] else pending.get("grindId")
    matches = [g for g in state["grinds"] if g["grind_id"] == grind_id]
    if len(matches) != 1:
        raise BridgeError("No uniquely identified grind. Produce a fresh Sync-enabled grind first")
    grind = matches[0]
    if record_only and grind != state["grinds"][0]:
        raise BridgeError("Xenia record-only mode can only complete the latest grind")
    if not grind["grind_successfull"] or grind["grind_weight"] <= 0:
        raise BridgeError("Selected grind is unsuccessful or has no measured dose")
    if any(b["grind_id"] == grind_id for b in state["brews"]):
        raise BridgeError("This grind already has a brew record; refusing duplicate feedback")
    if grind["recipe_type_id"] not in (1, 2, 5, 6):
        raise BridgeError("This client requires an espresso recipe")
    if not grind["recipe_gbs_enabled"]:
        raise BridgeError("GbS was disabled when this grind was recorded")
    if not record_only and pending.get("grindId") != grind_id:
        raise BridgeError("Grind does not match the grinder's pending brew")
    if not record_only and pending.get("gbsMode") not in (0, "StandardGbs"):
        raise BridgeError("Recipe must use standard Sync, rather than Brew Time Input")
    if not record_only and pending.get("doseIndex") != grind["recipe_type_id"]:
        raise BridgeError("Pending recipe type differs from the recorded grind")
    age = (current_time or datetime.now(timezone.utc)) - datetime.fromisoformat(
        grind["grind_date_time"].replace("Z", "+00:00"))
    if not 0 <= age.total_seconds() <= 600:
        raise BridgeError("Grind must be less than ten minutes old")
    try:
        grind_uuid = str(uuid.UUID(grind["grind_uuid"]))
    except (ValueError, TypeError):
        raise BridgeError("Recorded grind UUID is invalid") from None
    return {"format_version": 1, "created_at": now_iso(), "shot": shot,
            "grind_id": grind_id, "grind_uuid": grind_uuid, "transport": "xenia",
            "record_only": record_only,
            "state_fingerprint": fingerprint(state), "baseline": state}


class E64:
    def __init__(self, host, password=None, fingerprints=None):
        if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]{1,253}', host):
            raise BridgeError('Use the grinder private IPv4 address or LAN hostname')
        self.host, self.password, self.token = host, password, None
        self.http = None
        self.fingerprints = fingerprints or {}

    def trust(self, port):
        digest = self.fingerprints.get(str(port))
        if not digest:
            raise BridgeError("Enroll the grinder certificate before connecting")
        return aiohttp.Fingerprint(bytes.fromhex(digest))

    async def __aenter__(self):
        from network import private_ipv4
        # Connect to the validated address for the lifetime of this Owner session.
        self.host = sorted(await private_ipv4(self.host))[0]
        self.http = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True),
            timeout=aiohttp.ClientTimeout(total=20))
        try:
            if self.password is None:
                async with self.http.get(f"https://{self.host}/login.php", ssl=self.trust(443),
                                         allow_redirects=False) as r:
                    self.token = r.headers.get("Authorization", "").removeprefix("Bearer ")
                    if r.status != 302:
                        raise BridgeError("Owner login is required; supply the Owner password")
            else:
                async with self.http.post(f"https://{self.host}/rest_api/login", ssl=self.trust(443),
                    json={"userRole": "Owner", "password": self.password}, allow_redirects=False) as r:
                    body = json.loads(await bounded_body(r, MAX_RESPONSE_BYTES))
                    if r.status != 200 or body.get("success") is not True:
                        raise BridgeError("Owner login failed")
                    self.token = r.headers.get("Authorization", "").removeprefix("Bearer ")
            return self
        except BaseException:
            await self.http.close()
            raise

    async def __aexit__(self, *args):
        await self.http.close()

    async def read(self):
        async with self.http.get(f"https://{self.host}/rest_api/download_database", ssl=self.trust(443),
                                 allow_redirects=False) as r:
            if r.status != 200:
                raise BridgeError("Owner database export was denied")
            data = await bounded_body(r, MAX_DATABASE_BYTES)
            # SQLite deserialization and projection cannot delay cached Xenia polls.
            return await asyncio.to_thread(snapshot, data)

    async def command(self, payload):
        if not self.token or not re.fullmatch(r"[a-f0-9]{64}", self.token):
            raise BridgeError("Firmware returned no usable session token. Set an Owner password and use REST login")
        if payload != {"type": "RequestDriverState"}:
            raise BridgeError("This client only implements the read-only driver-state request")
        # Firmware consumes the token on WebSocket connection. Use each once.
        token, self.token = self.token, None
        async with self.http.ws_connect(f"wss://{self.host}:9997/?token={token}", ssl=self.trust(9997),
                    timeout=aiohttp.ClientWSTimeout(ws_receive=10), max_msg_size=MAX_RESPONSE_BYTES) as ws:
            await ws.send_json({**payload, "msgId": 1})
            while True:
                message = await ws.receive()
                if message.type != aiohttp.WSMsgType.TEXT:
                    raise BridgeError("WebSocket closed before acknowledgement; read history before any retry")
                body = json.loads(message.data)
                if body.get("refId") == 1:
                    return body
