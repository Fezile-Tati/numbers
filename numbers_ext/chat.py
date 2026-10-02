"""Intersession chat for the NUMBERS CLI: text-only DMs and group chats.

Talks to the hub's Angel ``dm`` and ``group-chat`` services with the user's
own agent token (the one ``/sign-in`` stores)::

    GET    {hub}/api/angel/v1/dm/associates?q=     friends, followers, following
    GET    {hub}/api/angel/v1/dm/{username}        conversation ?limit=&before=
    POST   {hub}/api/angel/v1/dm/send              {to, text, confirmed}
    GET    {hub}/api/angel/v1/group-chat?q=        my groups
    GET    {hub}/api/angel/v1/group-chat/{id}      details + members
    GET    {hub}/api/angel/v1/group-chat/messages  ?chat_id=&limit=&before=
    POST   {hub}/api/angel/v1/group-chat           {name, description, members}
    PATCH  {hub}/api/angel/v1/group-chat/{id}      {name?, description?}
    DELETE {hub}/api/angel/v1/group-chat/{id}      {confirmed}
    POST   {hub}/api/angel/v1/group-chat/send      {chat_id, text, confirmed}
    GET    {hub}/api/angel/v1/group-chat/replies       ?message_id=
    POST   {hub}/api/angel/v1/group-chat/removemember  {chat_id, username, confirmed}
    POST   {hub}/api/angel/v1/group-chat/invite        {chat_id, username}
    GET    {hub}/api/angel/v1/group-chat/invitations   my pending invites
    POST   {hub}/api/angel/v1/group-chat/respond       {invitation_id, accept}
    POST   {hub}/api/angel/v1/group-chat/leave         {chat_id, confirmed}
    POST   {hub}/api/angel/v1/group-chat/role          {chat_id, username, role}
    POST   {hub}/api/angel/v1/group-chat/edit | deletemessage
    POST   {hub}/api/angel/v1/group-chat/clear         {chat_id, confirmed}   for me only
    GET    {hub}/api/angel/v1/dm                       inbox
    POST   {hub}/api/angel/v1/dm/edit | deletemessage
    POST   {hub}/api/angel/v1/dm/clear                 {with, confirmed}      for me only
    POST   {hub}/api/angel/v1/dm/inbox-clear           {confirmed}

Messages are read as text only. The server moves rich media out of ``text``
into ``media`` ([{type, url, name}]); the CLI shows a ``(contains media)``
badge and a browser link per attachment, and drops custom emoji.

Sends, edits, deletes, clears, invites, invite answers, admin changes and leaving pass
``confirmed: true``: in the CLI a human typed the message or picked the action,
which is exactly the confirmation the server's confirmation class asks for. An agent
calling the same routes through MCP still has to confirm on its own.
"""

from __future__ import annotations

import json
import re
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


def dm_inbox_clear() -> None:
    """Inbox "clear all / mark as read": every DM is marked read and /inbox
    lists only conversations with messages received after this. The user
    picked it, so it goes out confirmed."""
    _request("POST", "/api/angel/v1/dm/inbox-clear", body={"confirmed": True})


def dm_clear(with_username: str) -> None:
    """Clear your chat history with one user. Hidden for you only (the other
    person keeps theirs); nothing is deleted on the server."""
    _request("POST", "/api/angel/v1/dm/clear",
             body={"with": with_username, "confirmed": True})


def group_clear(chat_id: str) -> None:
    """Clear your chat history in a group chat (for you only)."""
    _request("POST", "/api/angel/v1/group-chat/clear",
             body={"chat_id": chat_id, "confirmed": True})


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


def parse_group_edit(arg: str) -> tuple[Optional[str], Optional[str]]:
    """``/group-edit`` argument -> (name, description); None keeps the value.

    ``Elders | Weekly prayer`` sets both, ``Elders`` only the name,
    ``| Weekly prayer`` only the description and ``Elders |`` clears it.
    """
    name, bar, desc = arg.partition("|")
    return (name.strip() or None), (desc.strip() if bar else None)


def group_edit(chat_id: str, name: Optional[str] = None, description: Optional[str] = None) -> dict:
    """Owner only. A field left as None keeps its value; "" clears the description."""
    body = {}
    if name is not None:
        body["name"] = name
    if description is not None:
        body["description"] = description
    return _request("PATCH", "/api/angel/v1/group-chat/" + urllib.parse.quote(chat_id, safe=""),
                    body=body) or {}


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
                    body={"chat_id": chat_id, "username": username.lstrip("@"), "confirmed": True}) or {}


def reply_parents(msgs: list[dict]) -> list[tuple[dict, int]]:
    """Messages in ``msgs`` that have replies, as (message, reply count),
    the most recently answered first. The count is the server's
    ``reply_count`` (replies anywhere in the chat), or the replies seen in
    ``msgs`` when that is larger."""
    by_id = {m["id"]: m for m in msgs if m.get("id")}
    seen: dict[str, int] = {}
    latest: dict[str, str] = {}
    for m in msgs:
        parent = m.get("reply_to")
        if parent in by_id:
            seen[parent] = seen.get(parent, 0) + 1
            latest[parent] = max(latest.get(parent, ""), m.get("created_at", ""))
    out = []
    for mid, m in by_id.items():
        n = max(m.get("reply_count") or 0, seen.get(mid, 0))
        if n:
            out.append((m, n))
    out.sort(key=lambda p: latest.get(p[0]["id"]) or p[0].get("created_at", ""), reverse=True)
    return out


def replies_label(n: int) -> str:
    return f"{n} repl{'y' if n == 1 else 'ies'}"


def group_stats_lines(detail: dict) -> list[str]:
    """/group-stats: the group's description, owner and member numbers."""
    members = [m for m in detail.get("members") or []
               if m.get("status") == "active" and not m.get("blocked")]
    owner_id = detail.get("owner_id")
    admins = sorted("@" + (m.get("username") or "?") for m in members
                    if m.get("role") == "admin" and m.get("user_id") != owner_id)
    owner = "@" + (detail.get("owner_username") or "?")
    if detail.get("owner_account_type"):
        owner += f"  ({detail['owner_account_type']})"
    count = detail.get("member_count")
    if count is None:
        count = len(members)
    cap = detail.get("max_members") or 0
    lines = [
        f"  📊 {detail.get('name') or '(unnamed)'}",
        f"    Description:  {detail.get('description') or '—'}",
        f"    Owner:        {owner}",
        f"    Members:      {count}" + (f" / {cap}" if cap else ""),
        f"    Admins:       {', '.join(admins) if admins else 'none'}",
    ]
    if detail.get("my_role"):
        lines.append(f"    Your role:    {detail['my_role']}")
    if detail.get("last_activity"):
        lines.append(f"    Last active:  {_clock(detail['last_activity'])}")
    return lines


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
    return f"📥 Invite: {inv.get('group_name') or 'a group chat'}  from @{inv.get('inviter') or '?'}"


def inbox_label(t: dict) -> str:
    unread = "  ● unread" if t.get("unread") else ""
    if t.get("read_only"):
        unread += "  (read-only)"
    last = display_text(t.get("last_message") or "").replace("\n", " ")
    if len(last) > 40:
        last = last[:39] + "…"
    if t.get("last_has_media"):
        last = f"{last} 📎" if last else "[media] 📎"
    return f"@{t.get('with_username') or '?'}{unread}  [{_clock(t.get('updated_at', ''))}] {last}"


def member_label(m: dict) -> str:
    return f"@{m.get('username') or '?'}  ({m.get('role') or 'member'})"


_STICKER_RE = re.compile(r"^/?img/(Custom-Emoji/)?icon-chat\d+\.(webp|png)$", re.IGNORECASE)
# C0/C1 control characters except tab and newline. Lines are printed as ANSI,
# so an ESC in message text could otherwise restyle the terminal.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def display_text(text: str) -> str:
    """Message text for the terminal, without control characters. The server
    moves media out of the text (see ``media``); an older server sends a custom
    emoji as its image path (/img/Custom-Emoji/icon-chatN.webp), shown as
    [custom emoji]."""
    text = _CONTROL_RE.sub("", text or "")
    return "[custom emoji]" if _STICKER_RE.match(text.strip()) else text


def media_url(url: str) -> str:
    """A media link to paste into a browser: root-relative server paths
    (/uploads/x.webp) get the Intersession host; absolute URLs are kept."""
    url = _CONTROL_RE.sub("", url or "").strip()
    if not url or "://" in url:
        return url
    return _hub_base().rstrip("/") + "/" + url.lstrip("/")


def media_lines(m: dict) -> list[str]:
    """One link line per media item. Custom emoji (stickers) have no link."""
    lines = []
    for ref in m.get("media") or []:
        if ref.get("type") == "sticker" or not ref.get("url"):
            continue
        name = f" ({display_text(ref['name'])})" if ref.get("name") else ""
        lines.append(f"      ↳ {ref.get('type') or 'media'}{name}: {media_url(ref['url'])}")
    return lines


def _text_or_media(m: dict) -> str:
    """The message text; a media-only message says what it holds."""
    text = display_text(m.get("text") or "")
    if text.strip() or not m.get("media"):
        return text
    if all(r.get("type") == "sticker" for r in m["media"]):
        return "[custom emoji]"
    return "[media]"


def message_label(m: dict) -> str:
    text = _text_or_media(m).replace("\n", " ")
    if len(text) > 60:
        text = text[:59] + "…"
    clip = " 📎" if m.get("media") else ""
    return f"[{_clock(m.get('created_at', ''))}] {text}{clip}"


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
    """One chat line. The server flags the caller's own messages ``mine``.
    A message with media gets a ``(contains media)`` badge and one link line
    per attachment below it (custom emoji are dropped)."""
    tag = "you" if m.get("mine") else "@" + (m.get("from") or "?")
    edited = " (edited)" if m.get("edited") else ""
    media = " (contains media)" if m.get("media") else ""
    mention = "  🔔 mentions you" if m.get("mentions_you") and not m.get("mine") else ""
    line = f"  [{_clock(m.get('created_at', ''))}] {tag}: {_text_or_media(m)}{edited}{media}{mention}"
    links = media_lines(m)
    if links:
        line = "\n".join([line, *links])
    preview = m.get("reply_preview")
    if preview:
        who = f"@{display_text(preview['from'])}: " if preview.get("from") else ""
        line = f"    ↪ {who}{display_text(preview.get('text', ''))}\n{line}"
    return line
