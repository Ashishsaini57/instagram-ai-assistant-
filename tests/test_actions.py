import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.actions import ActionError, approve_draft, reject_draft
from app.ai.pipeline import process_comments
from app.db import create_db_engine, init_db
from app.instagram.sender import InstagramSender
from app.models import (
    ActionKind,
    Comment,
    CommentStatus,
    DraftStatus,
    OutboundAction,
    ReplyDraft,
    SendStatus,
)
from tests.fakes import FakeGenerator, make_result


def no_network_factory():
    raise AssertionError("No Instagram client may be created in DRY_RUN")


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'actions.db'}")
    init_db(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


@pytest.fixture
def sender(make_settings):
    return InstagramSender(make_settings(), client_factory=no_network_factory)


_ids = iter(range(1, 100_000))


def add_draft(db, status=DraftStatus.PENDING_REVIEW, dm_text="Hi! Details here."):
    c = Comment(
        ig_comment_id=f"9{next(_ids)}",
        from_id="555",
        from_username="fan",
        text="Price?",
        status=CommentStatus.PENDING_REVIEW,
    )
    db.add(c)
    db.flush()
    d = ReplyDraft(
        comment_id=c.id,
        status=status,
        reply_text="Thanks! Sharing details in DM.",
        suggest_dm=bool(dm_text),
        dm_text=dm_text,
    )
    db.add(d)
    db.commit()
    return d


def test_approve_in_dry_run(db, sender):
    draft = add_draft(db)
    result = approve_draft(db, draft.id, sender)
    assert result.reply_action.status == SendStatus.DRY_RUN
    assert result.reply_action.kind == ActionKind.COMMENT_REPLY
    assert result.dm_action is None
    assert draft.status == DraftStatus.DRY_RUN
    assert db.get(Comment, draft.comment_id).status == CommentStatus.DRY_RUN


def test_approve_with_edit_and_dm(db, sender):
    draft = add_draft(db)
    result = approve_draft(
        db, draft.id, sender, edited_text=" Shukriya! DM check karo. ", send_dm=True
    )
    assert result.reply_action.text == "Shukriya! DM check karo."
    assert draft.reply_text == "Shukriya! DM check karo."
    assert result.dm_action.kind == ActionKind.PRIVATE_REPLY
    assert result.dm_action.status == SendStatus.DRY_RUN


def test_invalid_edit_rejected_before_sending(db, sender):
    draft = add_draft(db)
    with pytest.raises(ActionError, match="contains_link"):
        approve_draft(db, draft.id, sender, edited_text="see www.x.com")
    assert db.scalars(select(OutboundAction)).all() == []
    assert draft.status == DraftStatus.PENDING_REVIEW


def test_dm_requested_without_dm_text(db, sender):
    draft = add_draft(db, dm_text=None)
    with pytest.raises(ActionError, match="no DM text"):
        approve_draft(db, draft.id, sender, send_dm=True)


@pytest.mark.parametrize(
    "status", [DraftStatus.POSTED, DraftStatus.REJECTED, DraftStatus.SKIPPED, DraftStatus.FAILED]
)
def test_cannot_approve_non_approvable(db, sender, status):
    draft = add_draft(db, status=status)
    with pytest.raises(ActionError):
        approve_draft(db, draft.id, sender)


def test_reject(db, sender):
    draft = add_draft(db)
    reject_draft(db, draft.id)
    assert draft.status == DraftStatus.REJECTED
    assert db.get(Comment, draft.comment_id).status == CommentStatus.REJECTED
    with pytest.raises(ActionError):
        approve_draft(db, draft.id, sender)


def test_missing_draft(db, sender):
    with pytest.raises(ActionError, match="not found"):
        approve_draft(db, 999, sender)


# --- automatic mode ---------------------------------------------------------


def _new_comment(db, text="Price kya hai?"):
    c = Comment(ig_comment_id=f"8{next(_ids)}", from_id="555", text=text, status=CommentStatus.NEW)
    db.add(c)
    db.commit()
    return c


DM_RESULT = make_result(
    intent="price_or_dm_request",
    suggest_dm=True,
    dm_text="Hi! Details.",
    reply_text="DM check karo!",
)


def test_review_queue_mode_never_auto_sends(db, factory, make_settings):
    settings = make_settings()  # review queue on, auto off
    _new_comment(db)
    process_comments(
        factory,
        settings,
        FakeGenerator(DM_RESULT),
        sender=InstagramSender(settings, no_network_factory),
    )
    assert db.scalars(select(OutboundAction)).all() == []
    assert db.scalars(select(ReplyDraft)).one().status == DraftStatus.PENDING_REVIEW


def test_auto_reply_mode_still_dry_run(db, factory, make_settings):
    settings = make_settings(
        review_queue_enabled=False, auto_reply_enabled=True, auto_dm_enabled=True
    )
    _new_comment(db)
    process_comments(
        factory,
        settings,
        FakeGenerator(DM_RESULT),
        sender=InstagramSender(settings, no_network_factory),
    )
    kinds = {a.kind: a.status for a in db.scalars(select(OutboundAction))}
    assert kinds == {
        ActionKind.COMMENT_REPLY: SendStatus.DRY_RUN,
        ActionKind.PRIVATE_REPLY: SendStatus.DRY_RUN,
    }


def test_auto_reply_without_auto_dm(db, factory, make_settings):
    settings = make_settings(review_queue_enabled=False, auto_reply_enabled=True)
    _new_comment(db)
    process_comments(
        factory,
        settings,
        FakeGenerator(DM_RESULT),
        sender=InstagramSender(settings, no_network_factory),
    )
    assert [a.kind for a in db.scalars(select(OutboundAction))] == [ActionKind.COMMENT_REPLY]


def test_low_confidence_not_auto_sent(db, factory, make_settings):
    settings = make_settings(review_queue_enabled=False, auto_reply_enabled=True)
    _new_comment(db)
    process_comments(
        factory,
        settings,
        FakeGenerator(make_result(confidence=0.2)),
        sender=InstagramSender(settings, no_network_factory),
    )
    assert db.scalars(select(OutboundAction)).all() == []


def test_media_folder_served(webhook_env, monkeypatch, tmp_path):
    from app.config import get_settings
    from app.main import create_app

    (tmp_path / "offer.png").write_bytes(b"\x89PNG fake")
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(create_app()) as client:
        assert client.get("/media/offer.png").content == b"\x89PNG fake"
        assert client.get("/media/../test.db").status_code == 404
