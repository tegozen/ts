#!/usr/bin/env python3
"""Idempotent TeamSpeak 3 guild layout seeder via ServerQuery."""

from __future__ import annotations

import argparse
import os
import socket
import sys
import time
import zlib
from pathlib import Path
from typing import Any


GROUP_GUEST = "Гость"
GROUP_MEMBER = "Рядовой"
GROUP_OFFICER = "Офицер"
GROUP_ADMIN_ALIASES = ("Server Admin", "Админ", "Admin")

CHANNEL_GUEST = "[Гостевая]"
# name, needed_join_power, is_default, maxclients (None = unlimited)
CHANNELS = [
    (CHANNEL_GUEST, 10, True, None),
    ("Лобби", 50, False, None),
    ("Общий", 50, False, None),
    ("1 на 1", 50, False, 2),
    ("Рейд / Ивенты", 50, False, None),
    ("AFK", 50, False, None),
    ("Офицерская", 70, False, None),
]

# Guest: only guest channel
# Names from ReSpeak/tsdeclarations Permissions.csv (no b_client_move — use i_client_move_power).
PERMS_GUEST = {
    "i_channel_join_power": 10,
    "i_client_talk_power": 10,
    "i_client_move_power": 0,
    "i_client_needed_move_power": 0,
    "i_group_member_add_power": 0,
    "i_group_member_remove_power": 0,
    "i_group_needed_member_add_power": 10,
    "i_group_needed_member_remove_power": 10,
    "i_client_kick_from_channel_power": 0,
    "i_client_needed_kick_from_channel_power": 25,
    "i_client_kick_from_server_power": 0,
    "i_client_needed_kick_from_server_power": 100,
    "i_client_ban_power": 0,
    "i_client_needed_ban_power": 100,
    "b_client_ban_create": 0,
    "i_client_ban_max_bantime": 0,
    "b_channel_join_ignore_maxclients": 0,
    # 0 = не показывать имя группы справа от ника; i_icon_id ставится после upload иконок
    "i_group_show_name_in_tree": 0,
}

# Member: common channels
PERMS_MEMBER = {
    "i_channel_join_power": 50,
    "i_client_talk_power": 50,
    "i_client_move_power": 0,
    "i_client_needed_move_power": 30,
    "i_group_member_add_power": 0,
    "i_group_member_remove_power": 0,
    "i_group_needed_member_add_power": 40,
    "i_group_needed_member_remove_power": 40,
    "i_client_kick_from_channel_power": 0,
    "i_client_needed_kick_from_channel_power": 30,
    "i_client_kick_from_server_power": 0,
    "i_client_needed_kick_from_server_power": 100,
    "i_client_ban_power": 0,
    "i_client_needed_ban_power": 100,
    "b_client_ban_create": 0,
    "i_client_ban_max_bantime": 0,
    "b_channel_join_ignore_maxclients": 0,
    "i_group_show_name_in_tree": 0,
}

# Officer: join everywhere, assign Рядовой/Офицер, move, channel-kick
PERMS_OFFICER = {
    "i_channel_join_power": 70,
    "i_client_talk_power": 70,
    "i_client_move_power": 60,
    "i_client_needed_move_power": 50,
    "i_group_member_add_power": 75,
    "i_group_member_remove_power": 75,
    "i_group_needed_member_add_power": 70,
    "i_group_needed_member_remove_power": 70,
    "i_client_kick_from_channel_power": 50,
    "i_client_needed_kick_from_channel_power": 50,
    "i_client_kick_from_server_power": 0,
    "i_client_needed_kick_from_server_power": 100,
    "i_client_ban_power": 0,
    "i_client_needed_ban_power": 100,
    "b_client_ban_create": 0,
    "i_client_ban_max_bantime": 0,
    # Can enter full channels (e.g. "1 на 1" with max 2)
    "b_channel_join_ignore_maxclients": 1,
    "i_group_show_name_in_tree": 0,
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
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self._sock = sock
                self._buf = b""
                sock.settimeout(self.timeout)
                banner = self._read_line()
                if "TS3" not in banner:
                    raise QueryError(f"Unexpected ServerQuery banner: {banner!r}")
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
        except (TimeoutError, socket.timeout):
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
        while True:
            # TS3 ServerQuery often terminates lines with LFCR (\n\r).
            # A leftover \r otherwise prefixes the next line as "\rerror ..." and breaks parsing.
            self._buf = (
                self._buf.replace(b"\r\n", b"\n")
                .replace(b"\n\r", b"\n")
                .replace(b"\r", b"\n")
            )
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line.decode("utf-8", errors="replace")
            chunk = self._sock.recv(4096)
            if not chunk:
                raise QueryError("ServerQuery connection closed")
            self._buf += chunk

    def command(self, cmd: str, *options: str, **params: Any) -> list[dict[str, str]]:
        parts = [cmd, *options]
        for key, value in params.items():
            if value is None:
                continue
            parts.append(f"{key}={escape(value)}")
        line = " ".join(parts)
        return self._send(line, label=cmd)

    def _peek_unread(self) -> bytes:
        assert self._sock is not None
        leftover = bytes(self._buf)
        try:
            self._sock.settimeout(0.0)
            while True:
                try:
                    chunk = self._sock.recv(4096)
                except BlockingIOError:
                    break
                if not chunk:
                    break
                leftover += chunk
        except (TimeoutError, socket.timeout, OSError):
            pass
        finally:
            self._sock.settimeout(self.timeout)
        return leftover

    def _send(self, line: str, label: str | None = None) -> list[dict[str, str]]:
        if self.dry_run:
            print(f"[dry-run] {line}")
            return []

        assert self._sock is not None
        shown = label or line.split(" ", 1)[0]
        payload = (line + "\n").encode("utf-8")
        print(f"→ {line if shown != 'login' else shown + ' ***'}")
        self._sock.sendall(payload)

        body_lines: list[str] = []
        try:
            while True:
                raw = self._read_line().strip()
                if not raw:
                    continue
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
                body_lines.append(raw)
        except (TimeoutError, socket.timeout) as exc:
            stuck = self._peek_unread()
            raise QueryError(
                f"timed out waiting for response to {shown!r}. "
                f"unread_bytes={stuck!r}.",
            ) from exc

    def login(self, user: str, password: str) -> None:
        print(f"Authenticating as {user}...")
        # Positional form is the most compatible with classic ServerQuery.
        self._send(f"login {escape(user)} {escape(password)}", label="login")
        print(f"Logged in as {user}")

    def use(self, sid: int = 1) -> None:
        self.command("use", sid=sid)
        print(f"Selected virtual server sid={sid}")

    def upload_icon(self, png_path: Path, ft_counter: list[int]) -> int:
        """Upload PNG via FileTransfer; return TeamSpeak icon id (signed CRC32)."""
        data = png_path.read_bytes()
        crc = zlib.crc32(data) & 0xFFFFFFFF
        icon_id = crc - 0x100000000 if crc > 0x7FFFFFFF else crc
        if self.dry_run:
            print(f"[dry-run] upload icon {png_path.name} -> i_icon_id={icon_id}")
            return icon_id

        ft_counter[0] += 1
        clientftfid = ft_counter[0]
        # TS stores icons as /icon_<unsigned_crc>
        remote = f"/icon_{crc}"
        try:
            info = self.command(
                "ftinitupload",
                clientftfid=clientftfid,
                name=remote,
                size=len(data),
                overwrite=1,
                channelid=0,
                cpw="",
            )
        except QueryError as exc:
            # 1101 / already exists — still use CRC id
            if "exists" in str(exc).lower() or exc.error_id in (1101, 1281):
                print(f"Icon {png_path.name} already on server, id={icon_id}")
                return icon_id
            raise

        row = info[0] if info else {}
        ftkey = row.get("ftkey")
        port = int(row.get("port", "30033"))
        if not ftkey:
            raise QueryError(f"ftinitupload returned no ftkey for {png_path.name}")

        with socket.create_connection((self.host, port), timeout=self.timeout) as ft_sock:
            ft_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            ft_sock.sendall(ftkey.encode("utf-8"))
            ft_sock.sendall(data)

        print(f"Uploaded icon {png_path.name} -> i_icon_id={icon_id} (crc={crc})")
        return icon_id


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


def load_permissions(q: ServerQuery) -> tuple[set[str], dict[str, int]]:
    """Live permission names + IDs from this TS instance."""
    rows = q.command("permissionlist")
    names: set[str] = set()
    ids: dict[str, int] = {}
    for r in rows:
        name = r.get("permname") or r.get("permsid")
        if not name:
            continue
        names.add(name)
        if r.get("permid"):
            ids[name] = int(r["permid"])
    if q.dry_run and not names:
        names = set(PERMS_GUEST) | set(PERMS_MEMBER) | set(PERMS_OFFICER) | set(PERMS_ADMIN_GUARD)
    print(f"Loaded {len(names)} permissions from permissionlist ({len(ids)} with IDs)")
    return names, ids


def set_group_perms(
    q: ServerQuery,
    sgid: int,
    perms: dict[str, int],
    known: set[str],
) -> None:
    # One permission per command. Only apply names that exist on this server.
    applied = 0
    for name, value in perms.items():
        if known and name not in known:
            print(f"WARN: skip unknown perm {name!r} on sgid={sgid} (not in permissionlist)")
            continue
        try:
            q.command(
                "servergroupaddperm",
                sgid=sgid,
                permsid=name,
                permvalue=value,
                permnegated=0,
                permskip=0,
            )
            applied += 1
        except QueryError as exc:
            if exc.error_id == 2562:
                print(f"WARN: skip invalid perm {name!r} on sgid={sgid}")
                continue
            raise
    print(f"Applied {applied}/{len(perms)} permissions to sgid={sgid}")


def find_channel(channels: list[dict[str, str]], name: str) -> dict[str, str] | None:
    for ch in channels:
        if ch.get("channel_name") == name:
            return ch
    return None


def ensure_channels(q: ServerQuery, perm_ids: dict[str, int]) -> dict[str, int]:
    listed = q.command("channellist", "-flags")
    by_name: dict[str, int] = {}
    if q.dry_run:
        listed = []

    join_perm = "i_channel_needed_join_power"
    join_permid = perm_ids.get(join_perm)

    def set_join_power(cid: int, needed: int) -> None:
        """Set needed join power via channeladdperm (channeledit property is ignored by TS)."""
        q.command("channeledit", cid=cid, channel_flag_permanent=1)
        # Replace any previous value
        try:
            if join_permid is not None:
                q.command("channeldelperm", cid=cid, permid=join_permid)
            else:
                q.command("channeldelperm", cid=cid, permsid=join_perm)
        except QueryError:
            pass

        add_kwargs: dict[str, Any] = {
            "cid": cid,
            "permvalue": needed,
            "permnegated": 0,
            "permskip": 0,
        }
        if join_permid is not None:
            add_kwargs["permid"] = join_permid
        else:
            add_kwargs["permsid"] = join_perm
        q.command("channeladdperm", **add_kwargs)

        # Verify
        listed_perms = q.command("channelpermlist", "-permsid", cid=cid)
        found = None
        for row in listed_perms:
            if row.get("permsid") == join_perm or (
                join_permid is not None and row.get("permid") == str(join_permid)
            ):
                found = row.get("permvalue")
                break
        if found is None:
            # Retry without -permsid (numeric ids only)
            listed_perms = q.command("channelpermlist", cid=cid)
            for row in listed_perms:
                if join_permid is not None and row.get("permid") == str(join_permid):
                    found = row.get("permvalue")
                    break
        if found != str(needed):
            raise QueryError(
                f"Failed to set {join_perm}={needed} on cid={cid} (got {found!r})"
            )
        print(f"Channel cid={cid} {join_perm}={found} OK")

    def set_max_clients(cid: int, maxclients: int | None) -> None:
        if maxclients is None:
            q.command(
                "channeledit",
                cid=cid,
                channel_flag_maxclients_unlimited=1,
            )
            return
        q.command(
            "channeledit",
            cid=cid,
            channel_flag_maxclients_unlimited=0,
            channel_maxclients=maxclients,
        )
        print(f"Channel cid={cid} maxclients={maxclients}")

    for name, needed, is_default, maxclients in CHANNELS:
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
            )
            print(f"Renamed default channel -> {name!r} (cid={cid})")
            default["channel_name"] = name
        else:
            params: dict[str, Any] = {
                "channel_name": name,
                "channel_flag_permanent": 1,
            }
            if is_default:
                params["channel_flag_default"] = 1
            if maxclients is not None:
                params["channel_flag_maxclients_unlimited"] = 0
                params["channel_maxclients"] = maxclients
            result = q.command("channelcreate", **params)
            if q.dry_run:
                cid = -1
            else:
                cid = int(result[0]["cid"])
            print(f"Created channel {name!r} (cid={cid})")
            listed.append({"cid": str(cid), "channel_name": name})

        if cid >= 0:
            if is_default:
                q.command("channeledit", cid=cid, channel_flag_default=1)
            set_join_power(cid, needed)
            set_max_clients(cid, maxclients)
        by_name[name] = cid

    return by_name


def seed(q: ServerQuery) -> None:
    known_perms, perm_ids = load_permissions(q)

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

    # Custom icons: repo icons/ on host, /icons in seeder container
    icons_dir = next(
        (
            p
            for p in (
                Path(__file__).resolve().parent.parent / "icons",
                Path("/icons"),
                Path(__file__).resolve().parent / "icons",
            )
            if p.is_dir()
        ),
        None,
    )
    if icons_dir is None:
        raise QueryError("Icons directory not found (expected ./icons next to compose)")
    ft_counter = [0]
    icon_files = {
        GROUP_GUEST: icons_dir / "guest.png",
        GROUP_MEMBER: icons_dir / "member.png",
        GROUP_OFFICER: icons_dir / "officer.png",
    }
    for label, path in icon_files.items():
        if not path.is_file():
            raise QueryError(f"Missing icon file: {path}")

    guest_icon = q.upload_icon(icon_files[GROUP_GUEST], ft_counter)
    member_icon = q.upload_icon(icon_files[GROUP_MEMBER], ft_counter)
    officer_icon = q.upload_icon(icon_files[GROUP_OFFICER], ft_counter)

    perms_guest = {**PERMS_GUEST, "i_icon_id": guest_icon}
    perms_member = {**PERMS_MEMBER, "i_icon_id": member_icon}
    perms_officer = {**PERMS_OFFICER, "i_icon_id": officer_icon}

    set_group_perms(q, guest_sgid, perms_guest, known_perms)
    set_group_perms(q, member_sgid, perms_member, known_perms)
    set_group_perms(q, officer_sgid, perms_officer, known_perms)
    if admin_sgid is not None:
        set_group_perms(q, admin_sgid, PERMS_ADMIN_GUARD, known_perms)
        print(f"Guarded Server Admin group (sgid={admin_sgid}) against officer assign")

    # Default group for new clients = Guest (command is serveredit, not servermodify)
    q.command("serveredit", virtualserver_default_server_group=guest_sgid)
    print(f"Default server group set to {GROUP_GUEST!r} (sgid={guest_sgid})")

    channels = ensure_channels(q, perm_ids)
    guest_cid = channels.get(CHANNEL_GUEST)
    if guest_cid is not None and guest_cid >= 0:
        # Default channel is controlled by channel_flag_default (set in ensure_channels).
        print(f"Default channel is {CHANNEL_GUEST!r} (cid={guest_cid})")

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

    print(f"Using Query {args.user}@{args.host}:{args.port} (password length={len(password)})")

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
