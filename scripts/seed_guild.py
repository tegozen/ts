#!/usr/bin/env python3
"""Idempotent TeamSpeak 3 guild layout seeder via ServerQuery."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
from typing import Any


GROUP_GUEST = "Гость"
GROUP_MEMBER = "Рядовой"
GROUP_OFFICER = "Офицер"
GROUP_ADMIN_ALIASES = ("Server Admin", "Админ", "Admin")

CHANNEL_GUEST = "[Гостевая]"
CHANNELS = [
    # name, needed_join_power, is_default
    (CHANNEL_GUEST, 10, True),
    ("Лобби", 50, False),
    ("Общий", 50, False),
    ("Рейд / Ивенты", 50, False),
    ("AFK", 50, False),
    ("Офицерская", 70, False),
]

# Guest: only guest channel
PERMS_GUEST = {
    "i_channel_join_power": 10,
    "i_client_move_power": 0,
    "i_client_needed_move_power": 0,
    "i_group_member_add_power": 0,
    "i_group_member_remove_power": 0,
    "i_group_needed_member_add_power": 10,
    "i_group_needed_member_remove_power": 10,
    "b_client_move": 0,
    "b_client_kick_from_channel": 0,
    "b_client_kick_from_server": 0,
    "b_client_ban_create": 0,
    "i_client_talk_power": 10,
}

# Member: common channels
PERMS_MEMBER = {
    "i_channel_join_power": 50,
    "i_client_move_power": 0,
    "i_client_needed_move_power": 30,
    "i_group_member_add_power": 0,
    "i_group_member_remove_power": 0,
    "i_group_needed_member_add_power": 40,
    "i_group_needed_member_remove_power": 40,
    "b_client_move": 0,
    "b_client_kick_from_channel": 0,
    "b_client_kick_from_server": 0,
    "b_client_ban_create": 0,
    "i_client_talk_power": 50,
}

# Officer: all channels + assign member/officer + move
PERMS_OFFICER = {
    "i_channel_join_power": 70,
    "i_client_move_power": 60,
    "i_client_needed_move_power": 50,
    "i_group_member_add_power": 75,
    "i_group_member_remove_power": 75,
    "i_group_needed_member_add_power": 70,
    "i_group_needed_member_remove_power": 70,
    "b_client_move": 1,
    "b_client_kick_from_channel": 1,
    "b_client_kick_from_server": 0,
    "b_client_ban_create": 0,
    "i_client_talk_power": 70,
}

# Ensure admin cannot be assigned by officers (needed > officer power 75)
PERMS_ADMIN_GUARD = {
    "i_group_needed_member_add_power": 100,
    "i_group_needed_member_remove_power": 100,
}


class QueryError(RuntimeError):
    def __init__(self, message: str, error_id: int = -1) -> None:
        super().__init__(message)
        self.error_id = error_id


def escape(value: str | int) -> str:
    text = str(value)
    return (
        text.replace("\\", "\\\\")
        .replace(" ", "\\s")
        .replace("|", "\\p")
        .replace("/", "\\/")
        .replace("\t", "\\t")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
    )


def unescape(value: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "\\" and i + 1 < len(value):
            nxt = value[i + 1]
            mapping = {"s": " ", "p": "|", "/": "/", "\\": "\\", "t": "\t", "r": "\r", "n": "\n"}
            out.append(mapping.get(nxt, nxt))
            i += 2
        else:
            out.append(value[i])
            i += 1
    return "".join(out)


def parse_records(body: str) -> list[dict[str, str]]:
    body = body.strip()
    if not body:
        return []
    records: list[dict[str, str]] = []
    for chunk in body.split("|"):
        chunk = chunk.strip()
        if not chunk:
            continue
        item: dict[str, str] = {}
        for token in chunk.split(" "):
            if "=" not in token:
                continue
            key, raw = token.split("=", 1)
            item[key] = unescape(raw)
        if item:
            records.append(item)
    return records


class ServerQuery:
    def __init__(self, host: str, port: int, timeout: float = 30.0, dry_run: bool = False) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.dry_run = dry_run
        self._sock: socket.socket | None = None
        self._buf = b""

    def connect(self) -> None:
        if self.dry_run:
            print(f"[dry-run] connect {self.host}:{self.port}")
            return
        last_err: Exception | None = None
        for attempt in range(1, 31):
            try:
                sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
                self._sock = sock
                self._buf = b""
                sock.settimeout(self.timeout)
                banner = self._read_line()
                if "TS3" not in banner:
                    raise QueryError(f"Unexpected ServerQuery banner: {banner!r}")
                # Drain MOTD / any extra greeting lines until the server goes quiet.
                self._drain_greeting()
                print(f"Connected to ServerQuery at {self.host}:{self.port}")
                return
            except (OSError, QueryError) as exc:
                last_err = exc
                self.close()
                time.sleep(1)
                if attempt % 5 == 0:
                    print(f"Waiting for ServerQuery... ({attempt}/30)")
        raise QueryError(f"Cannot connect to ServerQuery at {self.host}:{self.port}: {last_err}")

    def _drain_greeting(self) -> None:
        """Read leftover welcome lines without blocking the first real command."""
        assert self._sock is not None
        self._sock.settimeout(1.0)
        try:
            while True:
                line = self._read_line()
                if line:
                    print(f"Query greeting: {line[:120]}")
        except TimeoutError:
            pass
        finally:
            self._sock.settimeout(self.timeout)

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            finally:
                self._sock = None

    def _read_line(self) -> str:
        assert self._sock is not None
        while b"\n" not in self._buf:
            chunk = self._sock.recv(4096)
            if not chunk:
                raise QueryError("ServerQuery connection closed")
            self._buf += chunk
        line, self._buf = self._buf.split(b"\n", 1)
        return line.decode("utf-8", errors="replace").rstrip("\r")

    def command(self, cmd: str, *options: str, **params: Any) -> list[dict[str, str]]:
        parts = [cmd, *options]
        for key, value in params.items():
            if value is None:
                continue
            parts.append(f"{key}={escape(value)}")
        line = " ".join(parts)
        return self._send(line, label=cmd)

    def _send(self, line: str, label: str | None = None) -> list[dict[str, str]]:
        if self.dry_run:
            print(f"[dry-run] {line}")
            return []

        assert self._sock is not None
        shown = label or line.split(" ", 1)[0]
        # CR-LF is accepted by ServerQuery and avoids rare telnet-style stalls on \n-only.
        self._sock.sendall((line + "\r\n").encode("utf-8"))

        body_lines: list[str] = []
        try:
            while True:
                raw = self._read_line()
                if raw.startswith("error "):
                    err = parse_records(raw[len("error ") :])
                    info = err[0] if err else {}
                    error_id = int(info.get("id", "-1"))
                    msg = info.get("msg", "unknown")
                    if error_id != 0:
                        raise QueryError(
                            f"{shown} failed: id={error_id} msg={msg} ({line})",
                            error_id,
                        )
                    body = " ".join(body_lines)
                    return parse_records(body)
                if raw:
                    body_lines.append(raw)
        except TimeoutError as exc:
            raise QueryError(
                f"timed out waiting for response to {shown!r}. "
                "Check TS3_QUERY_PASSWORD (from first-boot logs) and that Query is not flood-limited.",
            ) from exc

    def login(self, user: str, password: str) -> None:
        # Prefer positional login — widely compatible with TS3 ServerQuery.
        print(f"Authenticating as {user}...")
        self._send(f"login {escape(user)} {escape(password)}", label="login")
        print(f"Logged in as {user}")

    def use(self, sid: int = 1) -> None:
        self.command("use", sid=sid)
        print(f"Selected virtual server sid={sid}")


def find_group(groups: list[dict[str, str]], *names: str) -> dict[str, str] | None:
    wanted = {n.lower() for n in names}
    for group in groups:
        if group.get("name", "").lower() in wanted:
            return group
    return None


def ensure_named_group(
    q: ServerQuery,
    groups: list[dict[str, str]],
    target_name: str,
    source_aliases: tuple[str, ...],
    copy_from_sgid: int | None = None,
) -> int:
    existing = find_group(groups, target_name, *source_aliases)
    if existing:
        sgid = int(existing["sgid"])
        if existing.get("name") != target_name:
            q.command("servergrouprename", sgid=sgid, name=target_name)
            print(f"Renamed server group {existing.get('name')!r} -> {target_name!r} (sgid={sgid})")
            existing["name"] = target_name
        else:
            print(f"Server group {target_name!r} already exists (sgid={sgid})")
        return sgid

    if copy_from_sgid is None:
        raise QueryError(f"Cannot create group {target_name!r}: no source group to copy")

    result = q.command("servergroupcopy", ssgid=copy_from_sgid, tsgid=0, name=target_name, type=1)
    if q.dry_run:
        sgid = 9000 + len(groups)
        groups.append({"sgid": str(sgid), "name": target_name, "type": "1"})
        print(f"[dry-run] would create group {target_name!r} (sgid={sgid})")
        return sgid
    sgid = int(result[0]["sgid"])
    groups.append({"sgid": str(sgid), "name": target_name, "type": "1"})
    print(f"Created server group {target_name!r} (sgid={sgid})")
    return sgid


def set_group_perms(q: ServerQuery, sgid: int, perms: dict[str, int]) -> None:
    chunks: list[str] = []
    for name, value in perms.items():
        chunks.append(
            f"permsid={escape(name)} permvalue={escape(value)} permnegated=0 permskip=0"
        )
    batch_size = 8
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        line = f"servergroupaddperm sgid={sgid} " + "|".join(batch)
        q._send(line, label="servergroupaddperm")
    print(f"Applied {len(perms)} permissions to sgid={sgid}")


def find_channel(channels: list[dict[str, str]], name: str) -> dict[str, str] | None:
    for ch in channels:
        if ch.get("channel_name") == name:
            return ch
    return None


def ensure_channels(q: ServerQuery) -> dict[str, int]:
    listed = q.command("channellist", "-flags")
    by_name: dict[str, int] = {}
    if q.dry_run:
        listed = []

    for name, needed, is_default in CHANNELS:
        existing = find_channel(listed, name)
        if existing:
            cid = int(existing["cid"])
            print(f"Channel {name!r} already exists (cid={cid})")
        elif is_default and listed:
            default = next(
                (
                    c
                    for c in listed
                    if c.get("channel_flag_default") == "1"
                    or c.get("channel_name") in ("Default Channel", "Default channel")
                ),
                listed[0],
            )
            cid = int(default["cid"])
            q.command(
                "channeledit",
                cid=cid,
                channel_name=name,
                channel_flag_permanent=1,
                channel_flag_default=1,
                channel_needed_join_power=needed,
            )
            print(f"Renamed default channel -> {name!r} (cid={cid})")
            default["channel_name"] = name
        else:
            params: dict[str, Any] = {
                "channel_name": name,
                "channel_flag_permanent": 1,
                "channel_needed_join_power": needed,
            }
            if is_default:
                params["channel_flag_default"] = 1
            result = q.command("channelcreate", **params)
            if q.dry_run:
                cid = -1
            else:
                cid = int(result[0]["cid"])
            print(f"Created channel {name!r} (cid={cid})")
            listed.append({"cid": str(cid), "channel_name": name})

        edit: dict[str, Any] = {
            "cid": cid,
            "channel_flag_permanent": 1,
            "channel_needed_join_power": needed,
        }
        if is_default:
            edit["channel_flag_default"] = 1
        if cid >= 0:
            q.command("channeledit", **edit)
        by_name[name] = cid

    return by_name


def seed(q: ServerQuery) -> None:
    groups = q.command("servergrouplist")
    if q.dry_run and not groups:
        # Synthetic defaults for dry-run messaging
        groups = [
            {"sgid": "6", "name": "Server Admin", "type": "1"},
            {"sgid": "7", "name": "Normal", "type": "1"},
            {"sgid": "8", "name": "Guest", "type": "1"},
        ]

    # Only regular (type=1) groups matter for players
    regular = [g for g in groups if g.get("type") == "1"] or groups

    guest_src = ("Guest", "Гость")
    member_src = ("Normal", "Рядовой", "Member")
    officer_src = ("Офицер", "Officer")

    guest_sgid = ensure_named_group(q, regular, GROUP_GUEST, guest_src)
    member_existing = find_group(regular, GROUP_MEMBER, *member_src)
    if member_existing:
        member_sgid = ensure_named_group(q, regular, GROUP_MEMBER, member_src)
    else:
        # Copy from guest if Normal missing (unusual)
        member_sgid = ensure_named_group(
            q, regular, GROUP_MEMBER, member_src, copy_from_sgid=guest_sgid
        )

    officer_existing = find_group(regular, GROUP_OFFICER, *officer_src)
    if officer_existing:
        officer_sgid = ensure_named_group(q, regular, GROUP_OFFICER, officer_src)
    else:
        officer_sgid = ensure_named_group(
            q, regular, GROUP_OFFICER, officer_src, copy_from_sgid=member_sgid
        )

    admin = find_group(regular, *GROUP_ADMIN_ALIASES)
    admin_sgid = int(admin["sgid"]) if admin else None

    set_group_perms(q, guest_sgid, PERMS_GUEST)
    set_group_perms(q, member_sgid, PERMS_MEMBER)
    set_group_perms(q, officer_sgid, PERMS_OFFICER)
    if admin_sgid is not None:
        set_group_perms(q, admin_sgid, PERMS_ADMIN_GUARD)
        print(f"Guarded Server Admin group (sgid={admin_sgid}) against officer assign")

    # Default group for new clients = Guest
    q.command("servermodify", virtualserver_default_server_group=guest_sgid)
    print(f"Default server group set to {GROUP_GUEST!r} (sgid={guest_sgid})")

    channels = ensure_channels(q)
    guest_cid = channels.get(CHANNEL_GUEST)
    if guest_cid is not None and guest_cid >= 0:
        q.command("servermodify", virtualserver_default_channel_id=guest_cid)
        print(f"Default channel set to {CHANNEL_GUEST!r} (cid={guest_cid})")

    print("Guild seed completed successfully.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed TeamSpeak guild channels and groups")
    parser.add_argument("--dry-run", action="store_true", help="Print planned commands only")
    parser.add_argument("--host", default=os.getenv("TS3_QUERY_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TS3_QUERY_PORT", "10011")))
    parser.add_argument("--user", default=os.getenv("TS3_QUERY_USER", "serveradmin"))
    parser.add_argument("--password", default=os.getenv("TS3_QUERY_PASSWORD", ""))
    args = parser.parse_args()

    password = (args.password or "").strip().strip('"').strip("'")
    if not password and not args.dry_run:
        print(
            "TS3_QUERY_PASSWORD is empty. Set it in .env from first-boot logs "
            "(loginname=serveradmin password=...).",
            file=sys.stderr,
        )
        return 1

    q = ServerQuery(args.host, args.port, dry_run=args.dry_run)
    try:
        q.connect()
        if not args.dry_run:
            q.login(args.user, password)
            q.use(1)
        else:
            print("[dry-run] login/use skipped")
        seed(q)
        return 0
    except QueryError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except TimeoutError as exc:
        print(
            f"ERROR: timed out ({exc}). Check TS3_QUERY_PASSWORD and ServerQuery allowlist.",
            file=sys.stderr,
        )
        return 2
    except OSError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    finally:
        q.close()


if __name__ == "__main__":
    raise SystemExit(main())
