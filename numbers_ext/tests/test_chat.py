import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from numbers_ext import chat


class _FakeHub(BaseHTTPRequestHandler):
    """Answers the Angel chat routes the way the Go handlers do."""

    seen = []  # (method, path, body) of every request

    def log_message(self, *a):
        pass

    def _send(self, code, payload, raw=False):
        body = payload if raw else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "text/plain" if raw else "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _record(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"null")
        self.seen.append((self.command, self.path, body))
        return body

    def do_GET(self):
        self._record()
        if self.path.startswith("/api/angel/v1/dm/associates"):
            self._send(200, {"ok": True, "data": {"associates": [{"username": "deborah"}]}})
        elif self.path.startswith("/api/angel/v1/dm/barak"):
            self._send(200, {"ok": True, "data": {
                "messages": [{"id": "m0", "from": "barak", "text": "old"}],
                "has_more": "before=" not in self.path, "next_before": "m0", "read_only": True}})
        elif self.path.startswith("/api/angel/v1/group-chat/messages"):
            self._send(403, {"ok": False, "error": {"code": "not_owner",
                                                    "message": "missing required scope group-chat:read"}})
        elif self.path.startswith("/api/angel/v1/group-chat/"):
            self._send(404, {"ok": False, "error": {"code": "not_found", "message": "group chat not found"}})
        else:  # an old server: ServeMux's plain-text 404
            self._send(404, b"404 page not found\n", raw=True)

    def do_POST(self):
        body = self._record()
        if self.path == "/api/angel/v1/dm/send":
            self._send(201, {"ok": True, "data": {"id": "m1", "from": "me", "text": body["text"], "mine": True}})
        else:
            self._send(404, b"404 page not found\n", raw=True)


@pytest.fixture()
def hub(monkeypatch):
    _FakeHub.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _FakeHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(chat, "_agent_token", lambda: "tok-abc")
    monkeypatch.setattr(chat, "_hub_base", lambda: f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(chat, "_ssl_ctx", lambda url: None)
    yield _FakeHub
    srv.shutdown()


def test_no_token_asks_to_sign_in(monkeypatch):
    monkeypatch.setattr(chat, "_agent_token", lambda: "")
    with pytest.raises(chat.ChatError, match="/sign-in"):
        chat.list_groups()


def test_list_associates_sends_query_and_bearer(hub):
    assert chat.list_associates("deb") == [{"username": "deborah"}]
    method, path, _ = hub.seen[-1]
    assert (method, path) == ("GET", "/api/angel/v1/dm/associates?q=deb")


def test_dm_send_is_confirmed_and_strips_at(hub):
    msg = chat.dm_send("@deborah", "hi")
    assert msg["mine"] is True
    assert hub.seen[-1][2] == {"to": "deborah", "text": "hi", "confirmed": True}


def test_missing_route_means_old_server(hub):
    with pytest.raises(chat.ChatError, match="update the server"):
        chat.list_groups()


def test_real_not_found_is_passed_through(hub):
    with pytest.raises(chat.ChatError, match="group chat not found"):
        chat.group_read("g-missing")


def test_missing_scope_suggests_fresh_sign_in(hub):
    with pytest.raises(chat.ChatError, match="/logout then /sign-in"):
        chat.group_messages("g1")


def test_format_message_uses_mine_flag():
    assert chat.format_message({"from": "me", "text": "hi", "mine": True}).endswith("you: hi")
    line = chat.format_message({"from": "deborah", "text": "amen", "edited": True})
    assert line.endswith("@deborah: amen (edited)")
    assert "[--:--]" in line  # no timestamp is shown, not crashed on


def test_format_message_shows_media_badge_and_links(monkeypatch):
    monkeypatch.setattr(chat, "_hub_base", lambda: "https://hub.example/")
    m = {"from": "deborah", "text": "look 🙏", "edited": True, "media": [
        {"type": "image", "url": "/uploads/a.webp"},
        {"type": "file", "url": "/uploads/c.pdf", "name": "notes.pdf"},
        {"type": "audio", "url": "https://cdn.example/v.webm"},
        {"type": "sticker"},
    ]}
    first, *links = chat.format_message(m).split("\n")
    assert first.endswith("@deborah: look 🙏 (edited) (contains media)")
    assert links == [
        "      ↳ image: https://hub.example/uploads/a.webp",
        "      ↳ file (notes.pdf): https://hub.example/uploads/c.pdf",
        "      ↳ audio: https://cdn.example/v.webm",
    ]


def test_media_only_messages_name_what_they_hold():
    sticker = {"from": "a", "text": "", "media": [{"type": "sticker"}]}
    assert chat.format_message(sticker).endswith("@a: [custom emoji] (contains media)")
    assert chat.message_label(dict(sticker, text="")).endswith("[custom emoji] 📎")
    # an older server sends the sticker path as text
    assert chat.format_message({"from": "a", "text": "/img/Custom-Emoji/icon-chat4.webp"}).endswith("@a: [custom emoji]")
    assert chat.inbox_label({"with_username": "a", "last_message": "", "last_has_media": True}).endswith("[media] 📎")
    assert chat.inbox_label({"with_username": "a", "last_message": "hi", "last_has_media": True}).endswith("hi 📎")


def test_control_characters_never_reach_the_terminal():
    m = {"from": "x", "text": "hi\x1b[31m red\x07", "reply_preview": {"from": "y", "text": "\x1b]0;pwn\x07ok"}}
    out = chat.format_message(m)
    assert "\x1b" not in out and "\x07" not in out
    assert out.split("\n")[0].endswith("@y: ]0;pwnok")


# --- management endpoints (invites, admins, leave, edit/delete) ------------

class _EchoHub(BaseHTTPRequestHandler):
    """Accepts every route; /dm/expired answers 401 like a revoked token."""

    seen = []

    def log_message(self, *a):
        pass

    def _answer(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"null")
        self.seen.append((self.command, self.path, body))
        if self.path.startswith("/api/angel/v1/dm/expired"):
            payload, code = {"ok": False, "error": {"code": "not_owner", "message": "invalid token"}}, 401
        elif self.path.startswith("/api/angel/v1/group-chat/invitations"):
            payload, code = {"ok": True, "data": {"invitations": [{"id": "i1", "group_name": "Elders"}]}}, 200
        elif self.path == "/api/angel/v1/dm":
            payload, code = {"ok": True, "data": {"threads": [{"with_username": "deborah", "unread": True}]}}, 200
        elif self.path.startswith("/api/angel/v1/group-chat/replies"):
            payload, code = {"ok": True, "data": {"parent": {"id": "p1"},
                                                  "replies": [{"id": "r1", "reply_to": "p1"}]}}, 200
        elif self.path == "/api/angel/v1/group-chat" and self.command == "POST":
            payload, code = {"ok": True, "data": {"group": {"id": "g-new", "name": body["name"]}, "invited": []}}, 201
        else:
            payload, code = {"ok": True, "data": {}}, 200
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_PATCH = do_DELETE = _answer


@pytest.fixture()
def echo(monkeypatch):
    _EchoHub.seen = []
    srv = HTTPServer(("127.0.0.1", 0), _EchoHub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(chat, "_agent_token", lambda: "tok-abc")
    monkeypatch.setattr(chat, "_hub_base", lambda: f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(chat, "_ssl_ctx", lambda url: None)
    yield _EchoHub
    srv.shutdown()


@pytest.mark.parametrize("call, method, path, body", [
    (lambda: chat.group_invite("g1", "@eli"), "POST", "/api/angel/v1/group-chat/invite",
     {"chat_id": "g1", "username": "eli", "confirmed": True}),
    (lambda: chat.respond_invitation("i1", True), "POST", "/api/angel/v1/group-chat/respond",
     {"invitation_id": "i1", "accept": True, "confirmed": True}),
    (lambda: chat.group_leave("g1"), "POST", "/api/angel/v1/group-chat/leave",
     {"chat_id": "g1", "confirmed": True}),
    (lambda: chat.group_set_role("g1", "@deborah", "admin"), "POST", "/api/angel/v1/group-chat/role",
     {"chat_id": "g1", "username": "deborah", "role": "admin", "confirmed": True}),
    (lambda: chat.group_edit_message("m1", "fixed"), "POST", "/api/angel/v1/group-chat/edit",
     {"message_id": "m1", "text": "fixed", "confirmed": True}),
    (lambda: chat.group_delete_message("m1"), "POST", "/api/angel/v1/group-chat/deletemessage",
     {"message_id": "m1", "confirmed": True}),
    (lambda: chat.dm_edit("d1", "fixed"), "POST", "/api/angel/v1/dm/edit",
     {"message_id": "d1", "text": "fixed", "confirmed": True}),
    (lambda: chat.dm_delete("d1"), "POST", "/api/angel/v1/dm/deletemessage",
     {"message_id": "d1", "confirmed": True}),
    (lambda: chat.dm_inbox_clear(), "POST", "/api/angel/v1/dm/inbox-clear", {"confirmed": True}),
    (lambda: chat.dm_clear("tim"), "POST", "/api/angel/v1/dm/clear",
     {"with": "tim", "confirmed": True}),
    (lambda: chat.group_clear("g1"), "POST", "/api/angel/v1/group-chat/clear",
     {"chat_id": "g1", "confirmed": True}),
])
def test_management_calls_hit_the_right_route(echo, call, method, path, body):
    call()
    assert echo.seen[-1] == (method, path, body)


def test_list_invitations_and_inbox(echo):
    assert chat.list_invitations()[0]["id"] == "i1"
    assert chat.dm_inbox()[0]["with_username"] == "deborah"


def test_group_create_returns_the_group_not_the_envelope(echo):
    assert chat.group_create("Elders", ["@deborah"])["id"] == "g-new"
    assert echo.seen[-1][2] == {"name": "Elders", "members": ["deborah"]}


def test_group_create_sends_the_description(echo):
    chat.group_create("Elders", None, "  Weekly prayer for the elders.  ")
    assert echo.seen[-1][2] == {"name": "Elders", "members": [],
                                "description": "Weekly prayer for the elders."}


def test_group_send_reply_carries_reply_to(echo):
    chat.group_send("g1", "amen @deborah", "p1")
    assert echo.seen[-1][2] == {"chat_id": "g1", "text": "amen @deborah",
                                "reply_to": "p1", "confirmed": True}
    chat.group_send("g1", "plain")
    assert "reply_to" not in echo.seen[-1][2]


def test_group_messages_pages_back_with_before(echo):
    chat.group_messages("g1", 30, "m-old")
    assert echo.seen[-1][:2] == ("GET", "/api/angel/v1/group-chat/messages?chat_id=g1&limit=30&before=m-old")


def test_group_replies_returns_parent_and_replies(echo):
    parent, replies = chat.group_replies("p1")
    assert parent == {"id": "p1"} and replies == [{"id": "r1", "reply_to": "p1"}]
    assert echo.seen[-1][:2] == ("GET", "/api/angel/v1/group-chat/replies?message_id=p1")


def test_format_message_shows_reply_and_mention():
    line = chat.format_message({"from": "joe", "text": "@me look", "mentions_you": True,
                                "reply_preview": {"from": "deborah", "text": "Pray for rain"}})
    first, second = line.split("\n")
    assert first.strip() == "↪ @deborah: Pray for rain"
    assert second.endswith("@joe: @me look  🔔 mentions you")
    # Your own message never flags you, even if you typed your own name.
    assert "mentions you" not in chat.format_message({"mine": True, "text": "@me", "mentions_you": True})


def test_group_description_is_one_short_line():
    assert chat.check_group_description("") is None
    assert chat.check_group_description("x" * chat.GROUP_DESC_MAX) is None
    assert "37" in chat.check_group_description("x" * (chat.GROUP_DESC_MAX + 1))
    assert "one line" in chat.check_group_description("Pray\nweekly")


def test_expired_token_is_an_auth_error(echo):
    with pytest.raises(chat.ChatAuthError, match="/sign-in"):
        chat.dm_read("expired")


def test_missing_scope_is_an_auth_error(hub):
    with pytest.raises(chat.ChatAuthError):
        chat.group_messages("g1")


def test_signed_out_makes_no_request(monkeypatch, echo):
    monkeypatch.setattr(chat, "_agent_token", lambda: "")
    with pytest.raises(chat.ChatAuthError, match="sign in"):
        chat.group_invite("g1", "eli")
    assert echo.seen == []


def test_labels():
    assert chat.invite_label({"group_name": "Elders", "inviter": "me"}) == "📥 Invite: Elders  from @me"
    assert chat.member_label({"username": "deborah", "role": "admin"}) == "@deborah  (admin)"
    assert "● unread" in chat.inbox_label({"with_username": "deborah", "unread": True})


def test_dm_page_sends_before_and_returns_paging_fields(hub):
    first = chat.dm_page("@barak")
    assert first["has_more"] is True and first["next_before"] == "m0" and first["read_only"] is True
    older = chat.page("dm", "barak", 50, "m0")
    assert older["has_more"] is False
    method, path, _ = hub.seen[-1]
    assert method == "GET" and path.startswith("/api/angel/v1/dm/barak?") and "before=m0" in path and "limit=50" in path
    # dm_read keeps returning just the messages.
    assert chat.dm_read("barak") == [{"id": "m0", "from": "barak", "text": "old"}]


def test_format_history_adds_one_separator_per_day():
    msgs = [
        {"id": "a", "from": "x", "text": "one", "created_at": "2026-09-21T10:00:00Z"},
        {"id": "b", "from": "x", "text": "two", "created_at": "2026-09-21T11:00:00Z"},
        {"id": "c", "from": "x", "text": "three", "created_at": "2026-09-23T10:00:00Z"},
    ]
    lines = chat.format_history(msgs)
    separators = [line for line in lines if line.strip().startswith("──")]
    assert len(separators) == 2
    assert len(lines) == 5
    # A day already announced by the previous page isn't announced again.
    again = chat.format_history(msgs[1:], prev_ts=msgs[0]["created_at"])
    assert len([line for line in again if line.strip().startswith("──")]) == 1


def test_format_day_separator_handles_bad_timestamps():
    assert chat.format_day_separator("not a time") == ""
    assert chat.format_day_separator("2026-09-22T12:00:00Z").strip().startswith("── ")


def test_inbox_label_marks_read_only_threads():
    assert "(read-only)" in chat.inbox_label({"with_username": "barak", "read_only": True})
    assert "(read-only)" not in chat.inbox_label({"with_username": "deborah"})
