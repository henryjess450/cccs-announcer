# CCCS Announcer

Text-to-speech announcements over the school PA system. A staff member types an
announcement on any computer on the school network; the machine wired into the
PA plays a chime and speaks it aloud.

**Phase 2** — accounts and roles, the audit trail, rate limiting, and
browser-only preview, on top of the Phase 1 core loop (compose → queue → chime
→ speech → speakers, with the single-playback guarantee, live status, stop, and
loud failures).

If you are installing this on the PA machine: download the single file
**`ANNOUNCER.bat`** from this repository and double-click it. It fetches
everything else itself. It installs everything, sets the machine up,
pulls the latest code, and starts the announcer — printing the sign-in details
and the address staff should use. **[DEPLOYMENT.md](DEPLOYMENT.md)** covers the two
settings it cannot do for you. This file is for developing on it.

---

## How it works

```
Staff browser  ──HTTP──►  FastAPI  ──INSERT──►  SQLite queue
      ▲                                              │
      └────────── Server-Sent Events ◄──┐            │ claim
                                        │            ▼
                                   status │      Player thread
                                        │            │
                                        └────────────┤ chime → gap → speech
                                                     ▼
                                              Audio device → amplifier → PA
```

Everything except the sign-in page, `/health`, and the static assets requires a
signed-in account. There are no anonymous announcements: the audit trail is the
main thing preventing misuse, and it only works if every announcement has a
name on it.

Two rules shape everything else:

1. **Exactly one player thread exists, and it is the only code that opens the
   audio device.** Web requests can only insert rows. Concurrency in the web
   layer can therefore never produce two overlapping announcements. A
   single-instance lock (`app/singleton.py`) stops a *second process* from
   creating a second player thread against the same database.
2. **The queue is on disk, not in memory.** If the process dies mid-announcement
   the queue survives, and the interrupted item is marked and logged at startup
   rather than disappearing.

## Layout

| Path | What it is |
|---|---|
| `app/main.py` | FastAPI routes, status snapshots, `/health` |
| `app/player.py` | The player thread. **Read this first.** |
| `app/db.py` | SQLite schema and the queue claim query |
| `app/normalize.py` | Text → speakable text. Pure and heavily tested. |
| `app/numbers.py` | Number/ordinal/digit spelling |
| `app/chimes.py` | The chime library |
| `app/events.py` | SSE fan-out |
| `app/singleton.py` | Refuses to run two copies against one data folder |
| `app/netinfo.py` | Works out the LAN address to give staff |
| `app/schedules.py` | School time vs UTC, and when a schedule next fires |
| `app/presets.py` | Ready-made announcements, slots, and the seed drills |
| `app/sounds.py` | Sound clips: storing, converting, and fetching |
| `scripts/enable_autostart.ps1` | Sets up and checks unattended restart |
| `app/accounts.py` | Users, sessions, first-run setup, lockout, security trail |
| `app/auth.py` | Session, CSRF, and role checks for the web layer |
| `app/security.py` | Password hashing (stdlib scrypt) and tokens |
| `app/ratelimit.py` | Per-user submission limits |
| `app/tts/` | `TTSEngine` interface → `PiperEngine`, `MockTTSEngine` |
| `app/audio/` | `AudioBackend` interface → `SoundDeviceBackend`, `MockAudioBackend` |
| `app/static/` | The whole front end. No build step, no CDN. |
| `scripts/` | Setup, chime generation, audio device listing |
| `tests/` | Including the concurrency proof |

## Running it locally

Python 3.11 or newer. **`.env` is optional** — every setting has a working
default, and Piper is found automatically in `piper/` and `voices/`.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python scripts/seed.py
```

`seed.py` creates `.env` from `.env.example`, sets up `data/`, and generates the
chime files.

### Real speech on a Mac or Linux laptop

The deployment guide installs Piper from its Windows release. For development,
pip is easier:

```bash
.venv/bin/pip install piper-tts
.venv/bin/python -m piper.download_voices en_US-lessac-medium --data-dir voices
```

Then point `.env` at it (use absolute paths):

```
PA_TTS_ENGINE=piper
PIPER_BINARY=/full/path/to/.venv/bin/piper
PIPER_MODEL=/full/path/to/voices/en_US-lessac-medium.onnx
```

### Without a voice model

```
PA_TTS_ENGINE=mock
```

Mock "speech" is a quiet test tone of realistic length — useful for checking
timing, but **it hums rather than speaking**. The page shows a yellow *Test
mode* banner whenever a mock engine or mock device is active, so this is never
mistaken for a broken PA. Add `PA_AUDIO_BACKEND=mock` to run with no sound card
at all (CI, or a machine with no audio).

```bash
.venv/bin/python run.py
```

Open <http://localhost:8080>.

### The chime

One chime plays before every announcement, set by `PA_DEFAULT_CHIME`
(`two_tone_bell`). Staff do not choose one — there is no chooser on the page,
and a `chime` value in a request body is ignored, so nothing can change what
plays. The other generated chimes stay in `data/chimes` for the Phase 3 admin
panel.

## Accounts

**First start creates an administrator for you.** On a database with no
accounts, the announcer makes one (username `admin`), prints the password in a
block on the console, and writes it to `data/FIRST-LOGIN.txt`. Sign in with it
and the browser immediately asks for your own name, username and password; the
file is deleted the moment that is done.

The password is *generated*, not a fixed default. A known default password on a
machine reachable from the school network is an open door for as long as it
takes somebody to sign in for the first time — and whoever is installing this
is standing at the machine, so reading a generated one costs them nothing.
`PA_BOOTSTRAP_PASSWORD` sets a fixed one if an install genuinely needs it.

While that account is unclaimed it can do nothing except set itself up, and
`/health` reports `degraded` with `accounts.setup_pending: true`.

After that, administrators add staff from the admin page in the browser. The
CLI does the same things and is the way back in if nobody can sign in:

```bash
.venv/bin/python scripts/manage_users.py list
.venv/bin/python scripts/manage_users.py reset jsmith
.venv/bin/python scripts/manage_users.py unlock jsmith
```

### How sign-in works

- **Passwords** are hashed with scrypt from the standard library — no crypto
  dependency. Format is self-describing (`scrypt$n$r$p$salt$hash`) so the
  parameters can be raised later without invalidating existing passwords.
- **Sessions live in the database**, not in a signed self-contained token.
  Deactivating an account has to take effect immediately: a teacher who leaves
  at lunchtime must not still be able to address the school at two o'clock.
  Only the SHA-256 of the cookie is stored, so reading the database does not
  hand anyone a usable session.
- **Two expiry limits**: an idle window (`PA_SESSION_IDLE_MINUTES`, 30 by
  default) and an absolute maximum (`PA_SESSION_MAX_HOURS`). The idle window is
  what stops an unattended logged-in computer being an open microphone.
- **CSRF**: every write must echo the session's own token in an `X-CSRF-Token`
  header. A cookie alone is not enough, because browsers attach cookies to
  requests started by other sites.
- **Roles**: `staff` can announce and stop their own; `admin` can stop
  anyone's, manage accounts, see everyone's log, and is exempt from the rate
  limit. The last active administrator cannot be demoted or turned off.
- **No forced password change.** Staff keep whatever password they are handed
  and can announce immediately; changing it is offered, never required. The
  `must_change_password` gate still exists but now applies only to the
  first-run administrator account, which genuinely has to be claimed before
  the announcer is finished installing.

## Tests

```bash
.venv/bin/python -m pytest
```

The suite runs entirely on the mock TTS engine and mock audio backend, so it
needs no sound card and makes no noise. What it covers:

- **`test_normalize.py`** — every text rule, the pronunciation dictionary, and
  the injection/control-character guards.
- **`test_queue_order.py`** — FIFO, priority insertion, claim atomicity,
  cancellation, and crash recovery.
- **`test_playback_lock.py`** — the important one. Twenty simultaneous HTTP
  submissions and fifteen concurrent direct enqueues, asserting that no two
  playback windows overlap and that nothing is dropped. Also: priority plays
  next without interrupting, stop cuts the current item and records who, and
  stopping during the chime skips the speech.
- **`test_failures.py`** — a missing device holds the announcement instead of
  losing it; a speech failure marks it failed and shows it; neither wedges the
  queue; the player thread survives an unexpected error.
- **`test_singleton.py`** — a second process is refused.
- **`test_bootstrap.py`** — the first-run administrator: created once, with a
  generated password, unable to do anything until claimed, and fully retired
  (old credentials dead, other sessions ended, file deleted) once it is.
- **`test_api.py`** — validation, attribution, the SSE stream, caching.
- **`test_auth.py`** — password hashing, sign-in, lockout, both session expiry
  limits, deactivation ending sessions immediately, CSRF, and the forced
  first-sign-in password change.
- **`test_permissions.py`** — staff can stop their own and only their own;
  admins can stop anyone's; every admin endpoint refuses staff; the last
  administrator cannot be removed.
- **`test_ratelimit.py`** — the limit holds, is per-person, counts failed
  announcements, and exempts admins.
- **`test_preview.py`** — the negative property that matters: preview never
  reaches the speakers, never enters the queue, and never appears in the log.

## Adding a TTS engine or an audio backend

Implement the interface, add one branch to the factory, add one `.env` value.
Nothing else changes.

```python
# app/tts/base.py
class TTSEngine:
    def describe(self) -> str: ...
    def check_ready(self) -> None: ...          # raise TTSError
    def synthesize(self, text, out_path): ...   # write a PCM WAV

# app/audio/base.py
class AudioBackend:
    def describe(self) -> str: ...
    def check_available(self) -> None: ...      # raise AudioUnavailable
    def open_session(self): ...                 # context manager, exclusive
```

Both error types carry two strings: `message` (plain language, shown to staff)
and `detail` (technical, log only). Keep that split — the whole UI depends on
never showing a staff member the word "ALSA".

## Static assets and caching

`/` is served `no-store`, and the CSS/JavaScript URLs carry `?v=<fingerprint>`
where the fingerprint is a hash of those files' **contents**, computed at
request time and cached against their timestamps.

It used to be the application version, which only works if somebody remembers
to bump it. When they forget, browsers keep serving last month's JavaScript
against this month's HTML — which presents as a feature that renders but does
nothing, not as a caching problem. Do not replace this with a manual version
string.

## Conventions

- Boring libraries. Four runtime dependencies, all pinned.
- The player and audio code is commented heavily. That is where the next
  maintainer will get stuck.
- Anything a staff member can read must be jargon-free.
- Failures are loud. Nothing is ever silently dropped.

## Database migrations

`app/db.py` holds a version 1 baseline (safe to re-run) plus a numbered
`_MIGRATIONS` dict. `initialize()` runs on every start and applies only what
the file has not seen, tracked by the `schema_version` setting. Never edit a
migration that has shipped — add a new one.

## Scheduled announcements

`app/schedules.py` owns the one distinction that matters: staff type times in
the school's timezone, everything is stored in UTC, and the conversion happens
in exactly one place. Candidate times are built in LOCAL time and then
converted, which is what keeps "3:10 PM every day" at 3:10 PM across a clock
change — the UTC time it fires at moves instead. There are tests on both the
March and November boundaries.

A schedule more than `PA_SCHEDULE_GRACE_MINUTES` late is skipped rather than
fired: being that late means the announcer was off, and replaying a backlog
into a building that has moved on is worse than missing it.

## What is not built yet

- **Phase 3** — presets, the admin pronunciation editor, chime uploads,
  quiet hours, searchable/filterable log with CSV export.
- **Phase 4** — zones.

The database already carries `zone` and `priority` so those phases add features
rather than reshape the schema.
