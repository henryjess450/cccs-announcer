"""Sound clips that play over the PA instead of speech.

Sirens for a drill, a countdown for an assembly, a piece of music for an
event. Anything that is not somebody talking.

Everything is stored as plain WAV in data/sounds, because that is what the
playback path reads with nothing but the standard library. Whatever gets
uploaded is converted on the way in, once, rather than teaching the audio
thread a second format -- that path plays to a whole school and stays as
simple as it can be.

Fetching audio from a link needs two extra programs the announcer does not
install for you (yt-dlp and ffmpeg). Everything else works without them.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
import unicodedata
import uuid
from pathlib import Path
from typing import List, Optional, Tuple

from .audio.wavio import UnsupportedAudioFile, duration_seconds, read_wav

log = logging.getLogger(__name__)

# Formats worth accepting at all. Anything not WAV needs ffmpeg to convert.
ACCEPTED_SUFFIXES = (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".flac",
                     ".mp4", ".webm")


class SoundError(Exception):
    """Something is wrong with the clip. `message` is shown to the person who
    tried to add it, so it says what to do about it."""

    def __init__(self, message: str, detail: str = ""):
        super().__init__(message)
        self.message = message
        self.detail = detail


def safe_title(value: str, fallback: str = "Sound") -> str:
    cleaned = unicodedata.normalize("NFKC", value or "").strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = "".join(c for c in cleaned if unicodedata.category(c)[0] != "C")
    return cleaned[:80] or fallback


class SoundLibrary:
    def __init__(
        self,
        directory: Path,
        *,
        max_seconds: float = 300.0,
        max_bytes: int = 25 * 1024 * 1024,
        ffmpeg: str = "ffmpeg",
        ytdlp: str = "yt-dlp",
    ):
        self.directory = Path(directory)
        self.max_seconds = max_seconds
        self.max_bytes = max_bytes
        self.ffmpeg = ffmpeg
        self.ytdlp = ytdlp
        self.directory.mkdir(parents=True, exist_ok=True)

    # -- what is installed ---------------------------------------------------

    def ffmpeg_path(self) -> Optional[str]:
        return shutil.which(self.ffmpeg)

    def ytdlp_path(self) -> Optional[str]:
        return shutil.which(self.ytdlp)

    def path_for(self, filename: Optional[str]) -> Optional[Path]:
        """Resolve a stored filename. Refuses anything with a path in it.

        Filenames come from the database, but a database is not a trust
        boundary -- one bad row must not be able to read a file elsewhere on
        the machine.
        """
        if not filename or "/" in filename or "\\" in filename or filename.startswith("."):
            return None
        candidate = (self.directory / filename).resolve()
        try:
            candidate.relative_to(self.directory.resolve())
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    # -- adding --------------------------------------------------------------

    def add_bytes(self, data: bytes, original_name: str) -> Tuple[str, float]:
        """Store an uploaded file. Returns (stored filename, seconds)."""
        if not data:
            raise SoundError("That file is empty.")
        if len(data) > self.max_bytes:
            raise SoundError(
                f"That file is too big. Keep it under "
                f"{self.max_bytes // (1024 * 1024)} MB."
            )

        suffix = Path(original_name or "").suffix.lower()
        if suffix not in ACCEPTED_SUFFIXES:
            raise SoundError(
                "That is not a sound file. Use a WAV or MP3.",
                f"unsupported suffix {suffix!r}",
            )

        scratch = self.directory / f"incoming-{uuid.uuid4().hex}{suffix or '.bin'}"
        scratch.write_bytes(data)
        try:
            return self._store(scratch)
        finally:
            scratch.unlink(missing_ok=True)

    def add_from_link(self, url: str) -> Tuple[str, float, str]:
        """Fetch the audio from a link. Returns (filename, seconds, title).

        Needs yt-dlp and ffmpeg on the machine; neither is installed by the
        announcer. Video is discarded -- only the sound is kept.
        """
        if not re.match(r"^https?://", (url or "").strip(), re.IGNORECASE):
            raise SoundError("That does not look like a link.")

        ytdlp = self.ytdlp_path()
        if ytdlp is None:
            raise SoundError(
                "This computer cannot fetch sound from links. Tell IT.",
                "yt-dlp is not installed; see DEPLOYMENT.md",
            )
        if self.ffmpeg_path() is None:
            raise SoundError(
                "This computer cannot convert sound from links. Tell IT.",
                "ffmpeg is not installed; see DEPLOYMENT.md",
            )

        stem = self.directory / f"incoming-{uuid.uuid4().hex}"
        command = [
            ytdlp,
            "--no-playlist",             # a link inside a playlist means ONE clip
            "--extract-audio",
            "--audio-format", "wav",
            "--max-filesize", str(self.max_bytes),
            "--output", f"{stem}.%(ext)s",
            "--print-to-file", "%(title)s", f"{stem}.title",
            "--no-progress",
            "--quiet",
            url.strip(),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            self._sweep(stem)
            raise SoundError("That took too long to fetch. Try a shorter clip.")

        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", "replace").strip()[:1500]
            self._sweep(stem)
            raise SoundError(
                "That link could not be fetched. Check it plays in a browser.",
                detail,
            )

        produced = next(iter(sorted(self.directory.glob(f"{stem.name}.wav"))), None)
        if produced is None:
            self._sweep(stem)
            raise SoundError("Nothing came back from that link.")

        title_file = Path(f"{stem}.title")
        title = safe_title(
            title_file.read_text(encoding="utf-8", errors="replace").strip()
            if title_file.exists() else "",
            fallback="Sound from a link",
        )
        try:
            filename, seconds = self._store(produced)
        finally:
            self._sweep(stem)
        return filename, seconds, title

    # -- internals -----------------------------------------------------------

    def _sweep(self, stem: Path) -> None:
        for leftover in self.directory.glob(f"{stem.name}*"):
            leftover.unlink(missing_ok=True)

    def _store(self, source: Path) -> Tuple[str, float]:
        """Convert if needed, check the length, and file it away."""
        target = self.directory / f"sound-{uuid.uuid4().hex}.wav"

        if source.suffix.lower() == ".wav" and self._is_readable(source):
            shutil.copyfile(source, target)
        else:
            self._convert(source, target)

        try:
            seconds = duration_seconds(target)
        except Exception as exc:
            target.unlink(missing_ok=True)
            raise SoundError("That file could not be read as sound.", repr(exc))

        if seconds <= 0.05:
            target.unlink(missing_ok=True)
            raise SoundError("There is no sound in that file.")
        if seconds > self.max_seconds:
            target.unlink(missing_ok=True)
            raise SoundError(
                f"That is {int(seconds // 60)} minutes long. Keep clips under "
                f"{int(self.max_seconds // 60)} minutes -- everything else waits "
                f"behind it."
            )
        return target.name, seconds

    def _is_readable(self, path: Path) -> bool:
        try:
            read_wav(path)
            return True
        except (UnsupportedAudioFile, Exception):
            return False

    def _convert(self, source: Path, target: Path) -> None:
        ffmpeg = self.ffmpeg_path()
        if ffmpeg is None:
            raise SoundError(
                "This computer can only take WAV files. Convert it first, or "
                "ask IT to install ffmpeg.",
                "ffmpeg is not installed",
            )
        command = [
            ffmpeg, "-nostdin", "-y", "-i", str(source),
            "-ac", "2", "-ar", "44100", "-c:a", "pcm_s16le",
            "-vn",                       # never keep video
            str(target),
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, timeout=180, check=False)
        except subprocess.TimeoutExpired:
            target.unlink(missing_ok=True)
            raise SoundError("That file took too long to convert.")
        if completed.returncode != 0 or not target.exists():
            target.unlink(missing_ok=True)
            raise SoundError(
                "That file could not be converted to sound.",
                completed.stderr.decode("utf-8", "replace").strip()[:1500],
            )

    def remove(self, filename: str) -> None:
        path = self.path_for(filename)
        if path is not None:
            path.unlink(missing_ok=True)

    def orphans(self, known: List[str]) -> List[Path]:
        """Files on disk that no row points at."""
        keep = set(known)
        return [p for p in self.directory.glob("sound-*.wav") if p.name not in keep]
