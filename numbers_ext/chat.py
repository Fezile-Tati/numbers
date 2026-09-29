"""Intersession chat for the NUMBERS CLI: text-only DMs and group chats.

Talks to the hub's Angel ``dm`` and ``group-chat`` services with the user's
own agent token (the one ``/sign-in`` stores)::

    GET    {hub}/api/angel/v1/dm/associates?q=     friends, followers, following
    GET    {hub}/api/angel/v1/dm/{username}        conversation ?limit=&before=
    POST   {hub}/api/angel/v1/dm/send              {to, text, confirmed}
    GET    {hub}/api/angel/v1/group-chat?q=        my groups
    GET    {hub}/api/angel/v1/group-chat/{id}      details + members
    GET    {hub}/api/angel/v1/group-chat/messages  ?chat_id=&limit=&before=
    POST   {hub}/api/angel/v1/group-chat           {name, members}
    PATCH  {hub}/api/angel/v1/group-chat/{id}      {name}
    DELETE {hub}/api/angel/v1/group-chat/{id}      {confirmed}
    POST   {hub}/api/angel/v1/group-chat/send      {chat_id, text, confirmed}
    POST   {hub}/api/angel/v1/group-chat/removemember  {chat_id, username}
    POST   {hub}/api/angel/v1/group-chat/invite        {chat_id, username}
    GET    {hub}/api/angel/v1/group-chat/invitations   my pending invites
    POST   {hub}/api/angel/v1/group-chat/respond       {invitation_id, accept}
    POST   {hub}/api/angel/v1/group-chat/leave         {chat_id, confirmed}
    POST   {hub}/api/angel/v1/group-chat/role          {chat_id, username, role}
    POST   {hub}/api/angel/v1/group-chat/edit | deletemessage
    GET    {hub}/api/angel/v1/dm                       inbox
    POST   {hub}/api/angel/v1/dm/edit | deletemessage | read-all

Sends, edits, deletes, invites, invite answers, admin changes and leaving pass
``confirmed: true``: in the CLI a human typed the message or picked the action,
which is exactly the confirmation the server's confirmation class asks for. An agent
calling the same routes through MCP still has to confirm on its own.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Any, Optional

from numbers_ext.debug_share_intersession import _agent_token
from numbers_ext.device_auth import _hub_base, _ssl_ctx

RECENT_LIMIT = 30
# How often an open chat checks for new messages (6 calls/min against the
# server's 600/min "generous" read class).
POLL_SECONDS = 10


class ChatError(RuntimeError):
    """A chat call failed; ``str(err)`` is fit to show the user."""


class ChatAuthError(ChatError):
    """Not signed in, or the server no longer accepts this device's token.

    The CLI closes any open chat on this error: chat is for signed-in users
    only, and a stale token must not keep a chat looking open.
    """


SIGN_IN_MESSAGE = "Chat needs you to sign in first. Run /sign-in."


def has_token() -> bool:
    return bool(_agent_token())


def _request(method: str, path: str, *, query: Optional[dict] = None,
             body: Optional[dict] = None, timeout: float = 20) -> Any:
    token = _agent_token()
    if not token:
        raise ChatAuthError(SIGN_IN_MESSAGE)
    url = _hub_base().rstrip("/") + path
    if query:
        q = {k: v for k, v in query.items() if v not in (None, "")}
        if q:
            url += "?" + urllib.parse.urlencode(q)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token)
    req.add_header("Accept", "application/json")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_ctx(url)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            env = json.loads(raw.decode("utf-8") or "{}")
            msg = (env.get("error") or {}).get("message") or f"HTTP {exc.code}"
        except Exception:
            msg = f"HTTP {exc.code}"
        if exc.code == 401:
            raise ChatAuthError("Your Intersession session has expired or was revoked. Run /sign-in.") from None
        if exc.code == 404 and "not found" not in msg.lower():
            msg = "chat is not available on this Intersession server (update the server)"
        if "missing required scope" in msg:
            raise ChatAuthError(msg + " -- run /logout then /sign-in to refresh this device's access") from None
        raise ChatError(msg) from None
    except (urllib.error.URLError, OSError) as exc:
        raise ChatError(f"cannot reach Intersession at {_hub_base()}: {exc}") from None
    try:
        env = json.loads(raw.decode("utf-8") or "{}")
    except ValueError:
        raise ChatError("unexpected response from Intersession") from None
    if not env.get("ok", False):
        raise ChatError((env.get("error") or {}).get("message") or "request failed")
    return env.get("data")


# ── DMs ──────────────────────────────────────────────────────────────────────

def list_associates(query: str = "") -> list[dict]:
    return (_request("GET", "/api/angel/v1/dm/associates", query={"q": query}) or {}).get("associates") or []


def dm_page(username: str, limit: int = RECENT_LIMIT, before: str = "") -> dict:
    """One page of a DM thread: ``messages`` (oldest first), ``has_more``,
    ``next_before`` (pass it as ``before`` for the next older page) and
    ``read_only`` (a past thread you can read but no longer reply to)."""
    path = "/api/angel/v1/dm/" + urllib.parse.quote(username.lstrip("@"), safe="")
    query = {"limit": limit}
    if before:
        query["before"] = before
    data = _request("GET", path, query=query) or {}
    data.setdefault("messages", [])
    return data


def dm_read(username: str, limit: int = RECENT_LIMIT) -> list[dict]:
    return dm_page(username, limit).get("messages") or []


def dm_send(username: str, text: str) -> dict:
    return _request("POST", "/api/angel/v1/dm/send",
                    body={"to": username.lstrip("@"), "text": text, "confirmed": True}) or {}


def dm_inbox() -> list[dict]:
    """One entry per DM thread: with_username, last_message, unread."""
    return (_request("GET", "/api/angel/v1/dm") or {}).get("threads") or []


def dm_edit(message_id: str, text: str) -> dict:
    return _request("POST", "/api/angel/v1/dm/edit",
                    body={"message_id": message_id, "text": text, "confirmed": True}) or {}


def dm_delete(message_id: str) -> None:
    _request("POST", "/api/angel/v1/dm/deletemessage",
             body={"message_id": message_id, "confirmed": True})


def dm_read_all() -> None:
    _request("POST", "/api/angel/v1/dm/read-all", body={})


# ── Group chats ──────────────────────────────────────────────────────────────

def list_groups(query: str = "") -> list[dict]:
    return (_request("GET", "/api/angel/v1/group-chat", query={"q": query}) or {}).get("groups") or []


def group_read(chat_id: str) -> dict:
    return _request("GET", "/api/angel/v1/group-chat/" + urllib.parse.quote(chat_id, safe="")) or {}


def group_page(chat_id: str, limit: int = RECENT_LIMIT, before: str = "") -> dict:
    """One page of a group chat: ``messages`` (oldest first), ``has_more`` and
    ``next_before``. ``before`` (a message id) pages back: the ``limit``
    messages sent just before that one."""
    query = {"chat_id": chat_id, "limit": limit}
    if before:
        query["before"] = before
    data = _request("GET", "/api/angel/v1/group-chat/messages", query=query) or {}
    data.setdefault("messages", [])
    return data


def group_messages(chat_id: str, limit: int = RECENT_LIMIT, before: str = "") -> list[dict]:
    """Recent messages, oldest first (see group_page)."""
    return group_page(chat_id, limit, before).get("messages") or []


# Paging back for /load-more and /history-chat asks for the server's largest page.
HISTORY_PAGE = 50


def page(target_kind: str, target_id: str, limit: int = RECENT_LIMIT, before: str = "") -> dict:
    """One page of the open DM ("dm") or group chat ("group")."""
    if target_kind == "dm":
        return dm_page(target_id, limit, before)
    return group_page(target_id, limit, before)


def group_send(chat_id: str, text: str, reply_to: str = "") -> dict:
    """Send to a group. ``reply_to`` answers a message of the same group;
    ``@username`` mentions in the text must name members."""
    body = {"chat_id": chat_id, "text": text, "confirmed": True}
    if reply_to:
        body["reply_to"] = reply_to
    return _request("POST", "/api/angel/v1/group-chat/send", body=body) or {}


def group_replies(message_id: str) -> tuple[dict, list[dict]]:
    """A message and its replies (oldest first)."""
    data = _request("GET", "/api/angel/v1/group-chat/replies",
                    query={"message_id": message_id}) or {}
    return data.get("parent") or {}, data.get("replies") or []


GROUP_DESC_MAX = 37  # one short sentence; the server enforces the same cap


def check_group_description(text: str) -> Optional[str]:
    """Return why ``text`` is not a valid group description, or None if it is."""
    if "\n" in text or "\r" in text:
        return "Keep the description to one line."
    if len(text.strip()) > GROUP_DESC_MAX:
        return f"Keep the description to {GROUP_DESC_MAX} characters (one short sentence)."
    return None


def group_create(name: str, members: Optional[list[str]] = None, description: str = "") -> dict:
    """Create a group; ``members`` are invited, not added. Returns the group."""
    body = {"name": name, "members": [m.lstrip("@") for m in (members or [])]}
    if description.strip():
        body["description"] = description.strip()
    data = _request("POST", "/api/angel/v1/group-chat", body=body) or {}
    return data.get("group") or data


def group_rename(chat_id: str, name: str) -> dict:
    return _request("PATCH", "/api/angel/v1/group-chat/" + urllib.parse.quote(chat_id, safe=""),
                    body={"name": name}) or {}


def group_delete(chat_id: str) -> None:
    _request("DELETE", "/api/angel/v1/group-chat/" + urllib.parse.quote(chat_id, safe=""),
             body={"confirmed": True})


def group_invite(chat_id: str, username: str) -> dict:
    """Invite an associate (owner or admin). ``already_member`` /
    ``already_invited`` are set instead of sending a second invite."""
    return _request("POST", "/api/angel/v1/group-chat/invite",
                    body={"chat_id": chat_id, "username": username.lstrip("@"), "confirmed": True}) or {}


def list_invitations() -> list[dict]:
    return (_request("GET", "/api/angel/v1/group-chat/invitations") or {}).get("invitations") or []


def respond_invitation(invitation_id: str, accept: bool) -> dict:
    return _request("POST", "/api/angel/v1/group-chat/respond",
                    body={"invitation_id": invitation_id, "accept": bool(accept), "confirmed": True}) or {}


def group_leave(chat_id: str) -> None:
    _request("POST", "/api/angel/v1/group-chat/leave", body={"chat_id": chat_id, "confirmed": True})


def group_set_role(chat_id: str, username: str, role: str) -> dict:
    """Owner only: role is "admin" or "member" (max 2 admins per group)."""
    return _request("POST", "/api/angel/v1/group-chat/role",
                    body={"chat_id": chat_id, "username": username.lstrip("@"), "role": role,
                          "confirmed": True}) or {}


def group_edit_message(message_id: str, text: str) -> dict:
    return _request("POST", "/api/angel/v1/group-chat/edit",
                    body={"message_id": message_id, "text": text, "confirmed": True}) or {}


def group_delete_message(message_id: str) -> None:
    _request("POST", "/api/angel/v1/group-chat/deletemessage",
             body={"message_id": message_id, "confirmed": True})


def group_remove_member(chat_id: str, username: str) -> dict:
    return _request("POST", "/api/angel/v1/group-chat/removemember",
                    body={"chat_id": chat_id, "username": username.lstrip("@")}) or {}


# ── Formatting ───────────────────────────────────────────────────────────────

def associate_label(a: dict) -> str:
    rel = ", ".join(a.get("relations") or [])
    name = a.get("display_name") or ""
    label = "@" + (a.get("username") or "?")
    if name and name != a.get("username"):
        label += f"  {name}"
    return f"{label}  ({rel})" if rel else label


def group_label(g: dict) -> str:
    n = g.get("member_count") or 0
    return f"{g.get('name') or '(unnamed)'}  ({n} member{'s' if n != 1 else ''})"


def invite_label(inv: dict) -> str:
    return f"✉ Invite: {inv.get('group_name') or 'a group chat'}  from @{inv.get('inviter') or '?'}"


def inbox_label(t: dict) -> str:
    unread = "  ● unread" if t.get("unread") else ""
    if t.get("read_only"):
        unread += "  (read-only)"
    last = (t.get("last_message") or "").replace("\n", " ")
    if len(last) > 40:
        last = last[:39] + "…"
    return f"@{t.get('with_username') or '?'}{unread}  [{_clock(t.get('updated_at', ''))}] {last}"


def member_label(m: dict) -> str:
    return f"@{m.get('username') or '?'}  ({m.get('role') or 'member'})"


def message_label(m: dict) -> str:
    text = (m.get("text") or "").replace("\n", " ")
    if len(text) > 60:
        text = text[:59] + "…"
    return f"[{_clock(m.get('created_at', ''))}] {text}"


def _clock(ts: str) -> str:
    try:
        dt = datetime.fromisoformat((ts or "").replace("Z", "+00:00")).astimezone()
    except ValueError:
        return "--:--"
    if dt.date() == datetime.now().astimezone().date():
        return dt.strftime("%H:%M")
    return dt.strftime("%d %b %H:%M")


START_OF_CONVERSATION = "  ── start of conversation ──"


def _local_date(ts: str):
    try:
        return datetime.fromisoformat((ts or "").replace("Z", "+00:00")).astimezone().date()
    except ValueError:
        return None


def format_day_separator(ts: str) -> str:
    """``  ── Mon 22 Sep 2026 ──`` for the local day of ``ts`` ("" if unparseable)."""
    day = _local_date(ts)
    return f"  ── {day.strftime('%a %d %b %Y')} ──" if day else ""


def format_history(msgs: list[dict], *, prev_ts: str = "") -> list[str]:
    """Chat lines for ``msgs`` (oldest first) with a day separator before the
    first message of each local day. ``prev_ts`` is the message printed just
    before these, so a day already announced isn't announced again."""
    lines = []
    prev_day = _local_date(prev_ts) if prev_ts else None
    for m in msgs:
        day = _local_date(m.get("created_at", ""))
        if day and day != prev_day:
            lines.append(format_day_separator(m.get("created_at", "")))
            prev_day = day
        lines.append(format_message(m))
    return lines


def format_message(m: dict) -> str:
    """One chat line. The server flags the caller's own messages ``mine``."""
    tag = "you" if m.get("mine") else "@" + (m.get("from") or "?")
    edited = " (edited)" if m.get("edited") else ""
    mention = "  🔔 mentions you" if m.get("mentions_you") and not m.get("mine") else ""
    line = f"  [{_clock(m.get('created_at', ''))}] {tag}: {m.get('text', '')}{edited}{mention}"
    preview = m.get("reply_preview")
    if preview:
        who = f"@{preview['from']}: " if preview.get("from") else ""
        line = f"    ↪ {who}{preview.get('text', '')}\n{line}"
    return line
