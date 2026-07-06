"""
tests/test_feed.py — Mixtape

Tests for the "Friends Listening Now" feed logic.

Regression coverage for Issue #2: RECENT_THRESHOLD was originally set to
timedelta(hours=24), which let friends who listened anywhere in the past day
show up as if they were listening right now. The fix narrows the window to
30 minutes. These tests pin down that exact boundary using the same event
ages (10-20 minutes vs. 2+ hours) that seed_data.py documents as the
intended "should show" / "should not show" split.
"""

import pytest
from datetime import datetime, timedelta, timezone
from app import create_app, db
from models import User, Song, ListeningEvent, friendships
from services.feed_service import get_friends_listening_now


@pytest.fixture
def app():
    app = create_app({"TESTING": True, "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:"})
    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture
def friends(app):
    """Two friended users and a song shared by one of them."""
    with app.app_context():
        me = User(username="me", email="me@example.com")
        friend = User(username="friend", email="friend@example.com")
        db.session.add_all([me, friend])
        db.session.flush()

        db.session.execute(friendships.insert().values(user_id=me.id, friend_id=friend.id))
        db.session.execute(friendships.insert().values(user_id=friend.id, friend_id=me.id))

        song = Song(title="Test Track", artist="Test Artist", shared_by=me.id)
        db.session.add(song)
        db.session.commit()

        yield {"me": me, "friend": friend, "song": song}


def _listen(friend_id, song_id, minutes_ago=None, hours_ago=None):
    now = datetime.now(timezone.utc)
    delta = timedelta(minutes=minutes_ago) if minutes_ago is not None else timedelta(hours=hours_ago)
    event = ListeningEvent(user_id=friend_id, song_id=song_id, listened_at=now - delta)
    db.session.add(event)
    db.session.commit()


def test_friend_listening_10_minutes_ago_shows_up(app, friends):
    """A friend who listened 10 minutes ago should appear as listening now."""
    with app.app_context():
        _listen(friends["friend"].id, friends["song"].id, minutes_ago=10)
        result = get_friends_listening_now(friends["me"].id)
        assert len(result) == 1
        assert result[0]["friend"]["username"] == "friend"


def test_friend_listening_2_hours_ago_does_not_show_up(app, friends):
    """
    A friend who listened 2 hours ago should NOT appear as listening now.

    Before the fix, RECENT_THRESHOLD was 24 hours, so this event would have
    incorrectly shown up as current activity.
    """
    with app.app_context():
        _listen(friends["friend"].id, friends["song"].id, hours_ago=2)
        result = get_friends_listening_now(friends["me"].id)
        assert result == []  # Bug caused this to return the 2-hour-old event


def test_friend_listening_20_minutes_ago_shows_up(app, friends):
    """20 minutes ago is still within the 30-minute window and should show."""
    with app.app_context():
        _listen(friends["friend"].id, friends["song"].id, minutes_ago=20)
        result = get_friends_listening_now(friends["me"].id)
        assert len(result) == 1


def test_friend_listening_40_minutes_ago_does_not_show_up(app, friends):
    """40 minutes ago is outside the 30-minute window and should not show."""
    with app.app_context():
        _listen(friends["friend"].id, friends["song"].id, minutes_ago=40)
        result = get_friends_listening_now(friends["me"].id)
        assert result == []
