# Mixtape Bug Hunt — Submission

## AI Usage

I used Claude throughout this project as a pair-programming partner, mostly in a
question-and-answer format: I'd read a file and describe what I thought it did, and
it would confirm, correct, or point me at the specific line I'd missed, rather than
just telling me the answer outright.

**Codebase orientation.** During Milestone 1, I read `playlist_service.py` and
`notification_service.py` myself and described each function out loud; Claude
checked my summaries against the actual code. This caught two real gaps in my
understanding: I initially thought `get_playlist_songs()`'s ordering came from
insertion order, and it pointed me specifically at the `.order_by(asc(playlist_entries.c.position))`
line and the `position` column on the `playlist_entries` association table. I also
initially summarized `rate_song()` as "rejecting a second rating," and it corrected
me by pointing at the exact line (`existing.score = score`) showing it actually
overwrites the old rating.

**Finding Issue #4 during orientation, not during dedicated debugging.** While
comparing `add_to_playlist()` and `rate_song()` function-by-function (not looking
for bugs yet, just building a mental model), Claude asked whether `rate_song()`
ever calls `create_notification()` the way `add_to_playlist()` does. I said yes
from memory without rereading the function — Claude checked again and pointed out
it doesn't, which is what led to identifying Issue #4 before I'd even opened the
issue tracker.

**Where AI got it wrong and I had to verify.** For Issue #3 (search duplicates),
Claude's first read of `search_songs()` concluded that joining `Song` to
`song_tags` would cause songs with multiple tags to appear multiple times in
results, and told me to just remove the join. That reasoning sounded right and I
agreed. But when it actually ran the existing test suite against the *original,
unmodified* code before making the change, `test_search_no_duplicates_multi_tag_song`
— the test written specifically to catch this — passed. That contradiction forced a
second, deeper investigation: dumping the raw compiled SQL to prove the join really
does produce 3 rows for a 3-tag song at the database level, while separately
confirming that SQLAlchemy's `Query.all()` was automatically de-duplicating the
ORM objects before they reached `search_songs()`'s return value — which is why the
"obvious" bug wasn't actually observable in this environment. The eventual fix
(remove the join) ended up being the same code change, but the *reasoning*
changed completely, and the RCA entry documents both the wrong first theory and
the verification that corrected it. This is the clearest example from this project
of why "reproduce before fixing" matters even when a diagnosis sounds obviously
correct.

**Where I had to redirect a wrong assumption before it wasted time.** For Issue #2
(feed), before checking anything empirically, Claude first suspected the bug was a
timezone-naive-vs-aware datetime comparison issue (SQLite stripping tzinfo on
storage). It actually tested that theory directly against an in-memory database and
disproved it (filtering behaved correctly either way), then found the real cause by
reading `seed_data.py`'s own comments, which explicitly state which listening
events should and shouldn't appear as "listening now." I was initially skeptical
when it proposed changing `RECENT_THRESHOLD` from 24 hours to 30 minutes and said
so — asking it to justify the change more concretely before I'd agree — which is
what led to it walking through the exact seed data line numbers and time deltas
rather than just asserting the fix was correct.

---

## Codebase Map

### Main files and their roles

- **`app.py`** — Flask application factory (`create_app`). Sets up the SQLAlchemy `db`
  instance, configures the database URI (SQLite by default), and registers the four
  route blueprints (`songs`, `playlists`, `users`, `feed`).
- **`models.py`** — Defines the SQLAlchemy ORM models: `User`, `Tag`, `Song`,
  `ListeningEvent`, `Rating`, `Playlist`, `Notification`. Also defines three plain
  association tables for many-to-many relationships: `friendships` (symmetric
  user-to-user), `song_tags` (song-to-tag), and `playlist_entries` (playlist-to-song).
  `playlist_entries` is not a simple join table — it carries extra columns
  (`position`, `added_by`, `added_at`), which is a signal that playlists track
  explicit song order and provenance, not just membership.
- **`routes/`** — Thin Flask blueprints (`songs.py`, `playlists.py`, `users.py`,
  `feed.py`) that parse the request, call straight into a service function, and
  format the JSON response. No business logic lives here.
- **`services/`** — Where all business logic actually lives:
  - `playlist_service.py` — playlist creation and retrieval (including ordered
    song lookup).
  - `streak_service.py` — listening streak calculation.
  - `feed_service.py` — "Friends Listening Now" and general activity feed.
  - `search_service.py` — song search by title/artist.
  - `notification_service.py` — creating and retrieving notifications; also owns
    `rate_song` and `add_to_playlist`, since both of those actions can trigger a
    notification.
- **`seed_data.py`** — Populates the DB with 5 users, 13 songs, 3 playlists, 10 tags,
  friendships, and `ListeningEvent`s at a range of specific ages (minutes to days
  old), which is useful for testing anything that filters activity by recency.
- **`tests/`** — `test_playlists.py`, `test_search.py`, `test_streaks.py`,
  `test_feed.py`, each testing the matching service module.

### Pattern noticed

Every route is a thin wrapper: it pulls request data, calls one service function, and
returns JSON (or a 400/404 on `ValueError`). All real logic — validation, queries,
notification side effects — lives in `services/`. So when something looks wrong at an
endpoint, the fix is almost never in `routes/`; it's in the service function the route
calls.

### Data flow — retrieving a playlist's songs

`GET /playlists/<id>/songs` → `routes/playlists.py` → `playlist_service.get_playlist_songs(playlist_id)`:

1. Look up the `Playlist` by id; raise `ValueError` (→ 404 at the route level) if it
   doesn't exist.
2. Query `Song`, joined to the `playlist_entries` association table on
   `Song.id == playlist_entries.c.song_id`, filtered to this playlist's id.
3. Order by `playlist_entries.c.position` ascending — this is what gives a playlist
   an explicit, editable order instead of relying on database insertion order.
4. Convert each `Song` to a dict via `Song.to_dict()`. Note: `to_dict()` does **not**
   include `position` — the position column only controls sort order, it's never
   exposed to the client. The client just receives songs pre-sorted.

### Data flow — notifying a song's original sharer

Two actions can generate a notification for whoever originally shared a song:
`add_to_playlist(playlist_id, song_id, added_by_user_id)` and
`rate_song(user_id, song_id, score)`, both in `notification_service.py`.

`add_to_playlist` follows the full pattern: look up song/adder/playlist → add the
song to the playlist if not already present → if the adder isn't the original
sharer, call `create_notification(user_id=song.shared_by, notification_type=...,
body=...)`.

`rate_song` follows the same validation pattern (look up song, look up rater,
check/create the `Rating` row), ending at `db.session.commit()`.

---

## Issue Tracker & Initial Plan

Five open issues, per the README, each mapped to one service file:

| # | Title | Service |
|---|-------|---------|
| 1 | Listening streak keeps resetting | `streak_service.py` |
| 2 | Friends Listening Now shows people from yesterday | `feed_service.py` |
| 3 | Same song shows up twice in search | `search_service.py` |
| 4 | No notification when a friend rates my song | `notification_service.py` |
| 5 | Last song in a playlist never shows up | `playlist_service.py` |

Plan going in: attempt all 5 (stretch goal) rather than stopping at the 3 required.
All 5 ended up fixed — see the Root Cause Analysis entries below for each.

---

## Root Cause Analysis

### Issue #5 — The last song in a playlist never shows up

**How you reproduced it:** Ran the existing test suite (`pytest tests/`) before making
any changes. `test_playlist_returns_all_songs` failed with `assert 4 == 5` on a
seeded 5-song playlist, and `test_playlist_returns_songs_in_order` failed because
`"Track 5"` was missing from the returned titles. Both failures point to the same
symptom: the last item in the ordered list is missing.

**How you found the root cause:** Traced the route `GET /playlists/<id>/songs` into
`playlist_service.get_playlist_songs()`. The function builds `songs` as a fully
correct, ordered SQLAlchemy query result (joined to `playlist_entries`, sorted by
`position` ascending), so the query itself wasn't suspect. The very next line —
`return [song.to_dict() for song in songs[:-1]]` — was the moment it became obvious:
slicing `songs[:-1]` drops the final element of any list, regardless of its size.

**The root cause:** `get_playlist_songs()` iterates over `songs[:-1]` instead of
`songs` when building the return list. Python's `[:-1]` slice always excludes the
last element, so no matter how many songs a playlist has, the last one in position
order is silently dropped before being converted to a dict and returned to the
caller.

**Your fix and side-effect check:** Changed `songs[:-1]` to `songs`. Verified against
both failing tests (`test_playlist_returns_all_songs`, `test_playlist_returns_songs_in_order`)
and confirmed `get_playlist(playlist_id)` (metadata-only, no song list) and
`get_user_playlists()` are unaffected since neither calls `get_playlist_songs()`.

---

### Issue #1 — My listening streak keeps resetting

**How you reproduced it:** `pytest tests/` failed on `test_streak_increments_on_sunday`:
listening on Saturday (`streak = 1`) then again on Sunday should increment to `2`,
but the streak stayed at `1`.

**How you found the root cause:** Read `update_listening_streak()` in
`streak_service.py` top to bottom. The days-since-last-listen logic is:
```python
if days_since_last == 0:
    return
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
else:
    user.listening_streak = 1
```
The `and today.weekday() != 6` clause stood out immediately — nothing in the
docstring's stated streak rules ("increments on a consecutive day, resets if a day
is skipped") mentions any special case for a particular weekday.

**The root cause:** Python's `datetime.weekday()` returns `6` for Sunday. The
`days_since_last == 1 and today.weekday() != 6` condition means: increment the
streak on a consecutive day, *unless* that consecutive day happens to be a Sunday —
in which case it falls through to the `else` branch and resets the streak to `1`.
There's no legitimate reason a Sunday should behave differently from any other
day in a day-over-day streak; the `!= 6` check was an erroneous extra condition
with no basis in the documented streak rules.

**Your fix and side-effect check:** Removed `and today.weekday() != 6`, leaving
`elif days_since_last == 1:`. Verified `test_streak_increments_on_sunday` passes,
and re-checked the `days_since_last == 0` (same-day, no change) and `else`
(skip a day, reset to 1) branches are untouched — confirmed by re-running the full
`test_streaks.py` file, all cases pass including non-Sunday increments and resets.

---

### Issue #2 — Friends Listening Now shows people from yesterday

**How you reproduced it:** Seeded the DB via `seed_data.py`, which creates 3
`ListeningEvent`s at 10/15/20 minutes ago (labeled in a comment as the ones that
"should appear in listening now") and 8 more at 2, 10, 18, 26, 34, 42, 50, and 58
hours ago (labeled "should NOT appear ... after fix"). Calling
`get_friends_listening_now()` for each seeded user before the fix showed events up
to 18-20 hours old appearing in the results (e.g. a user's friend who listened 2
hours ago showed up as "listening now"), confirming stale activity was being
surfaced as current.

**How you found the root cause:** Read `feed_service.py`'s `get_friends_listening_now()`
end to end. The SQL query (`ListeningEvent.listened_at >= cutoff`, ordered
descending, deduplicated to one event per friend) is logically correct — verified
this directly by feeding it hand-picked 23-hour-old and 25-hour-old events and
confirming the 24-hour cutoff was applied precisely as written. That ruled out a
query bug. The mismatch was instead between the code's definition of "recent" and
the product's: `RECENT_THRESHOLD = timedelta(hours=24)` versus the seed data's own
comment stating events "within the past 30 minutes" are what should count as
"listening now."

**The root cause:** `RECENT_THRESHOLD` was set to 24 hours, which is far too wide a
window for a "currently listening" feature — a friend who listened at any point in
the last day would show up as though they were listening right now. The intended
threshold, per the seed data's documented expectations, is 30 minutes.

**Your fix and side-effect check:** Changed `RECENT_THRESHOLD` from
`timedelta(hours=24)` to `timedelta(minutes=30)`. Re-ran the manual reproduction
script against all 5 seeded users: only the 10/15/20-minute-old events now appear,
and every event 2 hours or older (including a friend's *only* listening event) is
correctly excluded. Also confirmed `get_activity_feed()` — which intentionally
ignores `RECENT_THRESHOLD` and just returns the most recent N events regardless of
age — was untouched, since it doesn't reference the constant.

**Regression test:** Added `tests/test_feed.py`, covering events at 10/20 minutes
ago (should show) and 2 hours/40 minutes ago (should not show). Verified this test
file actually catches the original bug: temporarily reverted `RECENT_THRESHOLD` back
to `timedelta(hours=24)` and reran the suite — the 2-hour and 40-minute tests failed
exactly as expected (`assert [...] == []` with the stale event still present), while
the 10/20-minute tests still passed. Restored the fix afterward; all 4 pass again.

---

### Issue #3 — The same song keeps showing up twice in search

**How you reproduced it:** This one didn't reproduce the way I expected, and that's
worth documenting honestly. `search_songs()` joins `Song` to the `song_tags`
association table, so my first hypothesis was that a song with multiple tags would
come back multiple times (once per tag row). I ran the existing test suite against
the *original, unmodified* code first — including `test_search_no_duplicates_multi_tag_song`,
which is written specifically to catch this — and it passed. I then manually
searched for every single letter a–z against the full seeded dataset (13 songs,
several with 3+ tags) using the unmodified function, and through the real
`/songs/search` HTTP endpoint via Flask's test client. Zero duplicates appeared in
any of it.

**How you found the root cause:** Since the "obvious" symptom wasn't observable, I
dumped the raw compiled SQL for the query (`str(query.statement)`) and executed it
directly against the database, bypassing the ORM's result-loading step. That raw
SQL execution returned **3 rows** for a song with 3 tags — proof that the join
really does fan out at the database level, exactly as the join structure would
suggest. But `search_songs()` itself, using `db.session.query(Song)...all()`,
returned only 1. The gap between those two results was the moment it clicked: the
duplication is real at the SQL layer, but something in between was silently
absorbing it.

**The root cause:** `search_songs()` performs `db.session.query(Song).outerjoin(song_tags, ...)`
but the `.filter()` only ever checks `Song.title`/`Song.artist` — nothing about the
join is used for filtering. The join's only effect is to multiply the raw SQL
result by however many tags a song has. In most SQLAlchemy usage this would produce
visible duplicate objects, but SQLAlchemy's legacy `Query.all()` API automatically
de-duplicates full-entity results by primary key (a documented difference from the
newer `session.execute(select(...))` style, which requires an explicit `.unique()`
call to get the same behavior). That's why the installed SQLAlchemy version (2.0.51,
same as what's pinned in this project's `.venv`) masks the bug: the duplication
happens, then gets quietly collapsed before `search_songs()` returns. The join was
still a real defect — dead weight relying on an implicit ORM behavior it never
asked for, rather than a query written to only do what it needs.

**Your fix and side-effect check:** Removed the `.outerjoin(song_tags, ...)` call
entirely (and the now-unused `Tag`/`song_tags` imports), since the query never
needed it — `Song.to_dict()` already fetches tags independently through the
`Song.tags` relationship. Confirmed tags are still present and correct in search
results (e.g. searching "Crown" still returns `['rap', 'hip-hop', 'boom bap']` for
"Crown Heights Anthem"). Re-ran the full existing test suite (13/13 pass) and the
exhaustive a–z manual search — no behavior change for any query, as expected, since
this removes dead weight rather than altering what should match. The side effect I
specifically checked for: that removing the join wouldn't cause the `tags` field to
disappear from results, since it now relies entirely on the `Song.tags` relationship
instead of the join — confirmed it did not.

**AI disclosure for this entry specifically:** My first answer to the user asking
"does removing the join fix this?" was an overconfident "yes" based on reading the
join and assuming it would cause visible duplicates, without first checking whether
it actually did. It was the *reproduction* step — reading the actual behavior
rather than reasoning about what "should" happen — that caught the error and led
to the real (more nuanced) explanation above.

---

### Issue #4 — I got notified when a friend added my song to a playlist but not when they rated it

**How you reproduced it:** Opened `flask shell` and called `get_notifications(sharer_id)`
before and after calling `rate_song(rater_id, song_id, score)` for a song shared by
someone other than the rater. Notification count was identical before and after
(`1, 1`) — rating a friend's song produced zero new notifications for its sharer.

**How you found the root cause:** Read every function in `notification_service.py`
top to bottom during initial codebase orientation (before opening the bug tracker),
specifically comparing `add_to_playlist()` against `rate_song()` since both are
supposed to notify a song's original sharer about an action a friend took. Both
functions have the same shape up through validating IDs and committing the primary
change (adding to the playlist / saving the rating). `add_to_playlist()` then has
one more step: `if song.shared_by != added_by_user_id: create_notification(...)`.
`rate_song()` has no equivalent step at all — it commits and returns immediately.
The moment of confidence was structural, not a single suspicious line: it's not
that `rate_song()` has a wrong condition, it's that it's missing an entire step
`add_to_playlist()` has.

**The root cause:** `rate_song()` never calls `create_notification()`. This isn't a
typo or off-by-one — the notification step simply was never built for this code
path, even though the pattern for "notify the sharer when a friend interacts with
their song" already existed and was working correctly for the playlist-add case.
Two functions in the same file implement the same conceptual feature
inconsistently: one calls the shared notification helper, the other doesn't.

**Your fix and side-effect check:** Added the same pattern used in
`add_to_playlist()` to the end of `rate_song()`, right before `return rating`:
skip notifying if the rater is the song's own sharer (`user_id != song.shared_by`),
otherwise call `create_notification()` with a `"song_rated"` type and a body
message built from `rater.username`, `song.title`, and `score` — all variables
already in scope in the function. Verified two cases directly: rating a friend's
song now produces exactly one new notification (`before: 1, after: 2`), and rating
your *own* song still produces none (`self-rate before: 2, after: 2`), matching
`add_to_playlist()`'s existing self-action exemption. Also confirmed updating an
*existing* rating (the `if existing: existing.score = score` branch) still runs
through the same new notification step without needing separate handling, since
the notification call sits after both branches of that if/else.
