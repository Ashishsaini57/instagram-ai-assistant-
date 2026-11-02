import pytest
from sqlalchemy.orm import sessionmaker

from app.ai.pipeline import process_comments
from app.db import create_db_engine, init_db
from app.models import Comment, CommentStatus, ReplyDraft
from app.runtime_settings import (
    SettingsError,
    effective_settings,
    load_overrides,
    reset_settings,
    save_settings,
)
from tests.fakes import FakeGenerator


@pytest.fixture
def factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path / 'rs.db'}")
    init_db(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(factory):
    with factory() as session:
        yield session


def test_no_overrides_returns_env_settings(db, make_settings):
    base = make_settings()
    assert effective_settings(db, base) is base


def test_overrides_are_applied(db, make_settings):
    base = make_settings()
    s = save_settings(
        db,
        base,
        {"review_queue_enabled": False, "auto_reply_enabled": True, "creator_name": "Ashish"},
    )
    assert (s.review_queue_enabled, s.auto_reply_enabled, s.creator_name) == (False, True, "Ashish")
    assert s.dry_run is True and not s.can_send_to_instagram
    assert base.creator_name == "Creator"  # env settings untouched


def test_auto_reply_requires_review_queue_off(db, make_settings):
    with pytest.raises(SettingsError, match="review queue"):
        save_settings(db, make_settings(), {"auto_reply_enabled": True})
    assert load_overrides(db) == {}


@pytest.mark.parametrize(
    "key",
    ["dry_run", "instagram_access_token", "openai_api_key", "dashboard_password", "database_url"],
)
def test_dry_run_and_secrets_not_editable(db, make_settings, key):
    with pytest.raises(SettingsError, match="Not editable"):
        save_settings(db, make_settings(), {key: False})


def test_text_validation(db, make_settings):
    base = make_settings()
    with pytest.raises(SettingsError, match="links"):
        save_settings(db, base, {"dm_offer_text": "visit www.shop.com"})
    with pytest.raises(SettingsError, match="at most"):
        save_settings(db, base, {"creator_persona": "x" * 501})
    with pytest.raises(SettingsError, match="empty"):
        save_settings(db, base, {"creator_name": "  "})
    with pytest.raises(SettingsError, match="true or false"):
        save_settings(db, base, {"auto_dm_enabled": "yes"})


def test_stored_unsafe_combo_is_neutralised(db, make_settings):
    from app.models import AppSetting

    db.add(AppSetting(key="auto_reply_enabled", value="true"))
    db.commit()
    s = effective_settings(db, make_settings())
    assert s.review_queue_enabled and not s.auto_reply_enabled


def test_reset(db, make_settings):
    save_settings(db, make_settings(), {"creator_name": "X"})
    reset_settings(db)
    assert load_overrides(db) == {}


def test_pipeline_uses_dashboard_persona_and_blocked_words(db, factory, make_settings):
    base = make_settings()
    save_settings(db, base, {"creator_name": "Ashish", "blocked_words": "giveaway"})
    db.add_all(
        [
            Comment(ig_comment_id="1", from_id="5", text="Nice!", status=CommentStatus.NEW),
            Comment(ig_comment_id="2", from_id="5", text="free GIVEAWAY", status=CommentStatus.NEW),
        ]
    )
    db.commit()
    gen = FakeGenerator()
    process_comments(factory, base, gen)
    assert len(gen.calls) == 1
    assert gen.settings_seen[0].creator_name == "Ashish"
    skipped = db.query(ReplyDraft).filter_by(skip_reason="blocked_word").count()
    assert skipped == 1
