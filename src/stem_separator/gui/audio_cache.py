import logging
from pathlib import Path
import re
from shutil import rmtree
import stat
from uuid import uuid4

from PySide6.QtCore import QLockFile


logger = logging.getLogger(__name__)
SESSION_NAME = re.compile(r"job-[0-9a-f]{32}")


class AudioCache:
    """Own the temporary WAV files and playback data for one separation."""

    def __init__(self, root: Path) -> None:
        root.mkdir(parents=True, exist_ok=True)
        self.path = root / f"job-{uuid4().hex}"
        self._lock = self._lock_for(self.path)
        # Acquire the lock before the directory becomes visible to other instances.
        if not self._lock.tryLock(0):
            raise OSError(f"Could not lock audio cache: {self._lock.error().name}")
        try:
            self.path.mkdir()
        except OSError:
            self._lock.unlock()
            raise

    def close(self) -> None:
        try:
            self._remove(self.path)
        finally:
            self._lock.unlock()

    @classmethod
    def recover(cls, root: Path) -> None:
        """Remove abandoned results while allowing other instances to keep playing."""
        try:
            for path in root.iterdir():
                if not SESSION_NAME.fullmatch(path.name):
                    continue
                lock = cls._lock_for(path)
                if lock.tryLock(0):
                    try:
                        cls._remove(path)
                    finally:
                        lock.unlock()
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not scan temporary audio cache %s", root, exc_info=True)

    @staticmethod
    def _lock_for(path: Path) -> QLockFile:
        lock = QLockFile(str(path.with_suffix(".lock")))
        lock.setStaleLockTime(0)
        return lock

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            info = path.lstat()
            # Exclude symlinks and Windows directory junctions.
            if stat.S_ISDIR(info.st_mode) and not getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                rmtree(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("Could not remove temporary audio cache %s", path, exc_info=True)
