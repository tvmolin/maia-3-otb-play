import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from dotenv import dotenv_values

if os.name == "nt":
    import msvcrt
else:
    import fcntl


ROOT = Path(__file__).resolve().parent
LOGGER = logging.getLogger(__name__)
GAME_ID = re.compile(r"^[A-Za-z0-9]{8}$")
ANALYSIS_URL = re.compile(r"^https://lichess\.org/[A-Za-z0-9]{8}/analysis$")


def read_status(pgn_path):
    sidecar = Path(pgn_path).with_suffix(".pgn.lichess.json")
    if not sidecar.exists():
        return None
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("status") not in (
        "pending", "uploading", "failed", "imported"
    ):
        raise ValueError("Invalid Lichess import status.")
    if data["status"] == "imported" and (
        not isinstance(data.get("url"), str) or not ANALYSIS_URL.fullmatch(data["url"])
    ):
        raise ValueError("Invalid Lichess analysis URL.")
    return data


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def import_game(token, pgn):
    request = Request(
        "https://lichess.org/api/import",
        data=urlencode({"pgn": pgn}).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "User-Agent": "maia-3-otb-play (https://github.com/tvmolin/maia-3-otb-play)",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with build_opener(NoRedirects()).open(request, timeout=15) as response:
            data = json.load(response)
    except HTTPError as error:
        raise RuntimeError(f"Lichess returned HTTP {error.code}.") from error
    except (URLError, TimeoutError, OSError, HTTPException) as error:
        raise RuntimeError("Could not reach Lichess; the import outcome may be unknown.") from error
    except (ValueError, UnicodeError) as error:
        raise RuntimeError("Lichess returned an invalid response; the import outcome may be unknown.") from error
    game_id = data.get("id") if isinstance(data, dict) else None
    if not isinstance(game_id, str) or not GAME_ID.fullmatch(game_id):
        raise RuntimeError("Lichess returned an invalid game ID; the import outcome may be unknown.")
    return f"https://lichess.org/{game_id}/analysis"


class LichessImporter:
    def __init__(self, token, games_dir):
        self.token = token
        self.games_dir = Path(games_dir).resolve()
        self.lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lichess-import")
        self._resume()

    def _sidecar(self, pgn_path):
        path = Path(pgn_path).resolve()
        if path.parent != self.games_dir or path.suffix != ".pgn":
            raise ValueError("PGN must be in the games directory.")
        return path.with_suffix(".pgn.lichess.json")

    def _write(self, sidecar, state):
        temporary = sidecar.with_suffix(sidecar.suffix + ".tmp")
        try:
            temporary.write_text(json.dumps(state) + "\n", encoding="utf-8")
            temporary.replace(sidecar)
        finally:
            if temporary.exists():
                temporary.unlink()

    @contextmanager
    def _job_lock(self, sidecar):
        lock_path = sidecar.with_suffix(sidecar.suffix + ".lock")
        with lock_path.open("a+b") as lock_file:
            if os.name == "nt":
                if lock_file.tell() == 0:
                    lock_file.write(b"\0")
                    lock_file.flush()
                while True:
                    try:
                        lock_file.seek(0)
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
                        break
                    except PermissionError:
                        time.sleep(0.1)
            else:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if os.name == "nt":
                    lock_file.seek(0)
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def status(self, pgn_path):
        self._sidecar(pgn_path)
        with self.lock:
            return read_status(pgn_path)

    def remove_local_game_files(self, pgn_path):
        path = Path(pgn_path).resolve()
        sidecar = self._sidecar(path)
        with self._job_lock(sidecar):
            with self.lock:
                path.unlink(missing_ok=True)
                sidecar.unlink(missing_ok=True)

    def enqueue(self, pgn_path, retry=False, fallback_pgn=None):
        path = Path(pgn_path).resolve()
        sidecar = self._sidecar(path)
        with self._job_lock(sidecar):
            with self.lock:
                state = self.status(path)
                if state is not None and (not retry or state.get("status") != "failed"):
                    return False
                if not path.is_file():
                    if not isinstance(fallback_pgn, str) or not fallback_pgn.strip():
                        raise FileNotFoundError("The saved PGN is unavailable.")
                    with path.open("x", encoding="utf-8") as pgn_file:
                        pgn_file.write(fallback_pgn.rstrip() + "\n")
                self._write(sidecar, {"status": "pending"})
                self.executor.submit(self._run, path)
                return True

    def _resume(self):
        if not self.games_dir.exists():
            return
        for sidecar in self.games_dir.glob("*.pgn.lichess.json"):
            path = sidecar.with_name(sidecar.name.removesuffix(".lichess.json"))
            try:
                state = self.status(path)
                if state.get("status") in ("pending", "uploading"):
                    self.executor.submit(self._run, path)
            except (OSError, ValueError, json.JSONDecodeError):
                LOGGER.exception("Could not restore a Lichess import job for %s", path.name)

    def _run(self, path):
        sidecar = self._sidecar(path)
        try:
            with self._job_lock(sidecar):
                with self.lock:
                    state = self.status(path)
                    if state is None or state["status"] in ("failed", "imported"):
                        return
                    if state["status"] == "uploading":
                        self._write(sidecar, {
                            "status": "failed",
                            "error": "The app stopped during import; check Lichess before retrying.",
                        })
                        return
                    self._write(sidecar, {"status": "uploading"})
                try:
                    pgn = path.read_text(encoding="utf-8")
                    with self._job_lock(self.games_dir / ".lichess-api"):
                        url = import_game(self.token, pgn)
                    with self.lock:
                        self._write(sidecar, {"status": "imported", "url": url})
                    LOGGER.info("Imported %s to Lichess: %s", path.name, url)
                except (RuntimeError, OSError, UnicodeError, ValueError) as error:
                    LOGGER.error("Lichess import failed for %s: %s", path.name, error)
                    with self.lock:
                        self._write(sidecar, {"status": "failed", "error": str(error)})
        except (RuntimeError, OSError, UnicodeError) as error:
            LOGGER.error("Could not process Lichess import for %s: %s", path.name, error)

    def close(self):
        self.executor.shutdown(wait=True)


def configured_importer(games_dir=None):
    token = os.environ.get("LICHESS_API_TOKEN")
    if token is None:
        token = dotenv_values(ROOT / ".env").get("LICHESS_API_TOKEN")
    if not token:
        return None
    return LichessImporter(token, games_dir or ROOT / "games")
