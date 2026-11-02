import json

import httpx
import pytest

from app.instagram.client import InstagramAPIError, InstagramClient

TOKEN = "IGAAtest-secret-token"
IG_USER = "17841400000000001"


@pytest.fixture
def live_settings(make_settings):
    return make_settings(dry_run=False, instagram_user_id=IG_USER, instagram_access_token=TOKEN)


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        resp = self.responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp


def make_client(settings, *responses):
    rec = Recorder(*responses)
    return InstagramClient(settings, transport=httpx.MockTransport(rec), retry_delay=0), rec


def test_reply_to_comment_request(live_settings):
    client, rec = make_client(live_settings, httpx.Response(200, json={"id": "17999"}))
    assert client.reply_to_comment("17890001", "Thank you!") == "17999"
    req = rec.requests[0]
    assert req.method == "POST"
    assert str(req.url) == "https://graph.instagram.com/v23.0/17890001/replies"
    assert req.headers["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in str(req.url)
    assert req.content == b"message=Thank+you%21"


def test_private_reply_request(live_settings):
    client, rec = make_client(
        live_settings, httpx.Response(200, json={"recipient_id": "9", "message_id": "mid.1"})
    )
    assert client.send_private_reply("17890001", "Hi! Details here") == "mid.1"
    req = rec.requests[0]
    assert str(req.url) == f"https://graph.instagram.com/v23.0/{IG_USER}/messages"
    assert json.loads(req.content) == {
        "recipient": {"comment_id": "17890001"},
        "message": {"text": "Hi! Details here"},
    }


def test_text_and_image_dm_requests(live_settings):
    ok = httpx.Response(200, json={"message_id": "mid.2"})
    client, rec = make_client(live_settings, ok, httpx.Response(200, json={"message_id": "mid.3"}))
    client.send_text_message("555", "Hello")
    client.send_image_message("555", "https://cdn.example.com/a.jpg")
    assert json.loads(rec.requests[0].content) == {
        "recipient": {"id": "555"},
        "message": {"text": "Hello"},
    }
    assert json.loads(rec.requests[1].content) == {
        "recipient": {"id": "555"},
        "message": {
            "attachment": {"type": "image", "payload": {"url": "https://cdn.example.com/a.jpg"}}
        },
    }


def test_api_error_parsed_and_token_sanitized(live_settings):
    body = {
        "error": {
            "message": f"Invalid OAuth access token {TOKEN}",
            "type": "OAuthException",
            "code": 190,
            "fbtrace_id": "x",
        }
    }
    client, rec = make_client(live_settings, httpx.Response(400, json=body))
    with pytest.raises(InstagramAPIError) as exc_info:
        client.reply_to_comment("1", "hi")
    err = exc_info.value
    assert err.code == 190
    assert err.status_code == 400
    assert err.retryable is False
    assert TOKEN not in str(err)
    assert len(rec.requests) == 1  # no retry on client errors


def test_retries_once_on_server_error(live_settings):
    client, rec = make_client(
        live_settings, httpx.Response(500, json={}), httpx.Response(200, json={"id": "5"})
    )
    assert client.reply_to_comment("1", "hi") == "5"
    assert len(rec.requests) == 2


def test_retries_once_on_rate_limit_then_raises(live_settings):
    limited = {"error": {"message": "limit", "code": 4}}
    client, rec = make_client(
        live_settings, httpx.Response(400, json=limited), httpx.Response(400, json=limited)
    )
    with pytest.raises(InstagramAPIError) as exc_info:
        client.reply_to_comment("1", "hi")
    assert exc_info.value.retryable is True
    assert len(rec.requests) == 2


def test_network_error_retried(live_settings):
    client, rec = make_client(
        live_settings, httpx.ConnectError("boom"), httpx.Response(200, json={"id": "7"})
    )
    assert client.reply_to_comment("1", "hi") == "7"


@pytest.mark.parametrize("bad_id", ["", "../me", "1/replies?x=1", "a" * 65])
def test_invalid_ids_rejected_without_request(live_settings, bad_id):
    client, rec = make_client(live_settings)
    with pytest.raises(InstagramAPIError, match="Invalid Instagram ID"):
        client.reply_to_comment(bad_id, "hi")
    assert rec.requests == []


def test_requires_credentials(make_settings):
    with pytest.raises(InstagramAPIError, match="credentials"):
        InstagramClient(make_settings())
