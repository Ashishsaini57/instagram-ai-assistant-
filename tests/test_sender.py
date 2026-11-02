from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker

from app.db import create_db_engine, init_db
from app.instagram.client import InstagramClient
from app.instagram.media import image_url_problem, public_media_url
from app.instagram.sender import InstagramSender
from app.models import ActionKind, Comment, CommentStatus, Message, SendStatus

IG_USER = "17841400000000001"
TOKEN = "IGAAtest-secret-token"


@pytest.fixture
def db(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'sender.db'}")
    init_db(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session


def no_network_factory():
    raise AssertionError("No Instagram client may be created in DRY_RUN")


class Recorder:
    def __init__(self, response=None):
        self.response = response or httpx.Response(200, json={"id": "17999", "message_id": "mid.9"})
        self.requests: list[httpx.Request] = []

    def __call__(self, request):
        self.requests.append(request)
        return self.response


@pytest.fixture
def dry_sender(make_settings):
    s = make_settings(instagram_user_id=IG_USER, instagram_access_token=TOKEN)  # DRY_RUN default
    return InstagramSender(s, client_factory=no_network_factory)


@pytest.fixture
def live(make_settings):
    s = make_settings(dry_run=False, instagram_user_id=IG_USER, instagram_access_token=TOKEN)
    rec = Recorder()
    sender = InstagramSender(
        s,
        client_factory=lambda: InstagramClient(
            s, transport=httpx.MockTransport(rec), retry_delay=0
        ),
    )
    return sender, rec


_ids = iter(range(1000, 100_000))


def add_comment(db, created_at=None, from_id="555", status=CommentStatus.PENDING_REVIEW):
    c = Comment(
        ig_comment_id=str(next(_ids)),
        from_id=from_id,
        from_username="fan",
        text="Price?",
        status=status,
    )
    if created_at:
        c.created_at = created_at
    db.add(c)
    db.commit()
    return c


def add_message(db, sender_id="555", age=timedelta(minutes=5), is_echo=False):
    m = Message(
        ig_message_id=f"mid.{next(_ids)}",
        sender_id=sender_id,
        recipient_id=IG_USER,
        text="hi",
        is_echo=is_echo,
        sent_at=datetime.now(UTC) - age,
    )
    db.add(m)
    db.commit()


# --- DRY_RUN gate -----------------------------------------------------------


def test_dry_run_never_creates_client(db, dry_sender):
    c = add_comment(db)
    add_message(db)
    actions = [
        dry_sender.reply_to_comment(db, c, "Thanks!"),
        dry_sender.send_private_reply(db, c, "Details here"),
        dry_sender.send_text_dm(db, "555", "Follow-up"),
        dry_sender.send_image_dm(db, "555", "https://cdn.example.com/p.jpg"),
    ]
    assert [a.status for a in actions] == [SendStatus.DRY_RUN] * 4
    assert all(a.ig_response_id is None for a in actions)


def test_default_settings_are_dry_run_even_without_credentials(db, make_settings):
    sender = InstagramSender(make_settings(), client_factory=no_network_factory)
    assert sender.reply_to_comment(db, add_comment(db), "Thanks!").status == SendStatus.DRY_RUN


# --- live (mocked) ----------------------------------------------------------


def test_live_comment_reply_sent(db, live):
    sender, rec = live
    action = sender.reply_to_comment(db, add_comment(db), "Thanks!")
    assert action.status == SendStatus.SENT
    assert action.ig_response_id == "17999"
    assert len(rec.requests) == 1


def test_live_api_failure_recorded(db, make_settings):
    s = make_settings(dry_run=False, instagram_user_id=IG_USER, instagram_access_token=TOKEN)
    rec = Recorder(httpx.Response(400, json={"error": {"message": f"bad {TOKEN}", "code": 100}}))
    sender = InstagramSender(
        s, client_factory=lambda: InstagramClient(s, transport=httpx.MockTransport(rec))
    )
    action = sender.reply_to_comment(db, add_comment(db), "Thanks!")
    assert action.status == SendStatus.FAILED
    assert "code 100" in action.error
    assert TOKEN not in action.error


# --- content rules ----------------------------------------------------------


@pytest.mark.parametrize("text", ["", "x" * 301, "visit www.site.com"])
def test_invalid_reply_text_blocked(db, live, text):
    sender, rec = live
    action = sender.reply_to_comment(db, add_comment(db), text)
    assert action.status == SendStatus.BLOCKED
    assert action.reason.startswith("text_")
    assert rec.requests == []


def test_own_comment_never_replied(db, live):
    sender, rec = live
    c = add_comment(db, from_id=IG_USER, status=CommentStatus.IGNORED_SELF)
    assert sender.reply_to_comment(db, c, "hi").reason == "own_comment"
    assert sender.send_private_reply(db, c, "hi").reason == "own_comment"
    assert rec.requests == []


# --- private reply rules ----------------------------------------------------


def test_only_one_private_reply_per_comment(db, live):
    sender, rec = live
    c = add_comment(db)
    first = sender.send_private_reply(db, c, "Details here")
    second = sender.send_private_reply(db, c, "Details again")
    assert first.status == SendStatus.SENT
    assert second.status == SendStatus.BLOCKED
    assert second.reason == "private_reply_already_sent"
    assert len(rec.requests) == 1


def test_one_private_reply_also_enforced_in_dry_run(db, dry_sender):
    c = add_comment(db)
    assert dry_sender.send_private_reply(db, c, "a").status == SendStatus.DRY_RUN
    assert dry_sender.send_private_reply(db, c, "b").reason == "private_reply_already_sent"


def test_failed_private_reply_can_be_retried(db, make_settings):
    s = make_settings(dry_run=False, instagram_user_id=IG_USER, instagram_access_token=TOKEN)
    responses = [httpx.Response(400, json={"error": {"code": 100}}), httpx.Response(200, json={})]
    sender = InstagramSender(
        s,
        client_factory=lambda: InstagramClient(
            s, transport=httpx.MockTransport(lambda r: responses.pop(0))
        ),
    )
    c = add_comment(db)
    assert sender.send_private_reply(db, c, "x").status == SendStatus.FAILED
    assert sender.send_private_reply(db, c, "x").status == SendStatus.SENT


def test_private_reply_window(db, live):
    sender, rec = live
    old = add_comment(db, created_at=datetime.now(UTC) - timedelta(days=7, minutes=1))
    recent = add_comment(db, created_at=datetime.now(UTC) - timedelta(days=6, hours=23))
    assert sender.send_private_reply(db, old, "hi").reason == "private_reply_window_expired"
    assert sender.send_private_reply(db, recent, "hi").status == SendStatus.SENT
    assert len(rec.requests) == 1


# --- 24h messaging window ---------------------------------------------------


def test_follow_up_dm_requires_open_window(db, live):
    sender, rec = live
    assert sender.send_text_dm(db, "555", "hi").reason == "messaging_window_closed"
    add_message(db, age=timedelta(hours=23, minutes=59))
    assert sender.send_text_dm(db, "555", "hi").status == SendStatus.SENT
    assert len(rec.requests) == 1


def test_window_expired_after_24h(db, live):
    sender, rec = live
    add_message(db, age=timedelta(hours=24, minutes=1))
    assert sender.send_text_dm(db, "555", "hi").reason == "messaging_window_closed"
    assert rec.requests == []


def test_echo_messages_do_not_open_window(db, live):
    sender, _ = live
    add_message(db, sender_id="555", is_echo=True)
    assert sender.send_text_dm(db, "555", "hi").reason == "messaging_window_closed"


def test_cannot_dm_own_account(db, live):
    sender, _ = live
    assert sender.send_text_dm(db, IG_USER, "hi").reason == "own_account"


# --- images -----------------------------------------------------------------


def test_image_dm_sent_with_attachment(db, live):
    sender, rec = live
    add_message(db)
    action = sender.send_image_dm(db, "555", "https://cdn.example.com/product.jpg")
    assert action.status == SendStatus.SENT
    assert action.kind == ActionKind.DM_IMAGE
    assert b'"type":"image"' in rec.requests[0].content


def test_image_dm_needs_window(db, live):
    sender, rec = live
    action = sender.send_image_dm(db, "555", "https://cdn.example.com/p.jpg")
    assert action.reason == "messaging_window_closed"
    assert rec.requests == []


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://cdn.example.com/a.jpg", "not_https"),
        ("ftp://cdn.example.com/a.jpg", "not_https"),
        ("https:///a.jpg", "missing_host"),
        ("https://localhost/a.jpg", "private_host"),
        ("https://printer.local/a.jpg", "private_host"),
        ("https://127.0.0.1/a.jpg", "private_host"),
        ("https://10.0.0.5/a.jpg", "private_host"),
        ("https://192.168.1.2/a.jpg", "private_host"),
        ("https://169.254.169.254/latest", "private_host"),
        ("https://[::1]/a.jpg", "private_host"),
        ("https://intranet/a.jpg", "private_host"),
        ("", "missing_url"),
    ],
)
def test_image_url_rejected(url, reason):
    assert image_url_problem(url) == reason


@pytest.mark.parametrize(
    "url",
    [
        "https://cdn.example.com/a.jpg",
        "https://8.8.8.8/a.png",
        "https://my-app.onrender.com/media/x.jpg",
    ],
)
def test_image_url_accepted(url):
    assert image_url_problem(url) is None


def test_blocked_image_url_never_sent(db, live):
    sender, rec = live
    add_message(db)
    action = sender.send_image_dm(db, "555", "https://192.168.0.10/x.jpg")
    assert action.reason == "image_private_host"
    assert rec.requests == []


def test_public_media_url(make_settings):
    s = make_settings(public_base_url="https://my-app.example.com/")
    assert (
        public_media_url(s, "price-list.png") == "https://my-app.example.com/media/price-list.png"
    )
    with pytest.raises(ValueError):
        public_media_url(s, "price list.png")
    with pytest.raises(ValueError):
        public_media_url(s, "../secret.env")
    with pytest.raises(ValueError):
        public_media_url(make_settings(), "a.png")


def test_public_base_url_must_be_https(make_settings):
    with pytest.raises(ValueError, match="https"):
        make_settings(public_base_url="http://example.com")
