"""Git-based versioning for Tokyo Notes notes.

Provides GitVersionController which manages a git repository inside
the user's notes directory. Supports auto-commit on save, manual
snapshots, history browsing, diff viewing, and restore.
"""

from __future__ import annotations

import datetime
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import git
from gi.repository import GLib

from core.performance import slow_callback

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_AUTO_COMMIT_DELAY_MS = 30_000

_GITIGNORE_CONTENT = """\
# Tokyo Notes git versioning
*.tmp
._*
.tokyo-notes/assistant/
"""


@dataclass
class CommitInfo:
    hexsha: str
    message: str
    timestamp: datetime.datetime
    summary: str = ""


class AutoCommitScheduler:
    """Coalesce frequent note saves into bounded Git history batches.

    The scheduler is owned by the GTK thread. Git work is submitted to the
    application's serial I/O executor, preserving ordering with async saves.
    """

    def __init__(
        self,
        controller: GitVersionController,
        submit: Callable[[Callable], None],
        delay_ms: int = _AUTO_COMMIT_DELAY_MS,
    ) -> None:
        self.controller = controller
        self._submit = submit
        self._delay_ms = delay_ms
        self._pending: dict[str, str | None] = {}
        self._timer_id = 0

    @property
    def pending_notes(self) -> set[str]:
        return set(self._pending)

    def mark_dirty(self, note_name: str, old_name: str | None = None) -> None:
        """Mark *note_name* for the next batch, preserving rename ancestry."""
        if not note_name:
            return
        original_name = old_name
        if old_name and old_name in self._pending:
            original_name = self._pending.pop(old_name) or old_name
        existing = self._pending.get(note_name)
        self._pending[note_name] = existing or original_name
        if self._timer_id == 0:
            self._timer_id = GLib.timeout_add(self._delay_ms, self._on_timeout)

    def flush_note(self, note_name: str) -> None:
        """Submit one pending note immediately, normally on note exit."""
        if note_name not in self._pending:
            return
        old_name = self._pending.pop(note_name)
        self._submit_commit(note_name, old_name)
        if not self._pending:
            self._cancel_timer()

    def flush_all(self) -> None:
        """Submit every pending note as one history batch."""
        pending = list(self._pending.items())
        self._pending.clear()
        self._cancel_timer()
        for note_name, old_name in pending:
            self._submit_commit(note_name, old_name)

    def clear(self) -> None:
        """Forget pending entries after a manual snapshot committed them."""
        self._pending.clear()
        self._cancel_timer()

    def request_maintenance(self) -> None:
        """Queue safe automatic repository maintenance after pending commits."""
        if self.controller.is_available():
            self._submit(self.controller.optimize_repository)

    def _on_timeout(self) -> bool:
        self._timer_id = 0
        self.flush_all()
        return False

    def _submit_commit(self, note_name: str, old_name: str | None) -> None:
        def commit() -> None:
            if old_name and old_name != note_name:
                self.controller.rename_note(old_name, note_name)
            self.controller.auto_commit(note_name)

        self._submit(commit)

    def _cancel_timer(self) -> None:
        if self._timer_id > 0:
            timer_id = self._timer_id
            self._timer_id = 0
            GLib.source_remove(timer_id)


class GitVersionController:
    """Manages a git repository in the notes directory.

    All public methods are safe to call from the GTK main thread.
    Long-running operations (commit, log, diff) should be dispatched
    to a thread pool via the *executor* callback.
    """

    def __init__(
        self,
        notes_dir: str | Path,
        executor: Callable[[Callable], None] | None = None,
    ) -> None:
        self.notes_dir = Path(notes_dir)
        self._executor = executor
        self._git_dir: Path = self.notes_dir / ".git"
        self.repo: git.Repo | None = None
        self._init_done = False
        self._init_check()

    def _init_check(self) -> None:
        """Check if the notes directory is already a git repo."""
        try:
            if self._git_dir.is_dir():
                self.repo = git.Repo(str(self.notes_dir))
                self._init_done = True
                logger.info("Git repo found at %s", self.notes_dir)
        except (git.InvalidGitRepositoryError, git.NoSuchPathError):
            logger.warning("Invalid git repo at %s", self.notes_dir)
            self.repo = None

    @staticmethod
    def is_git_installed() -> bool:
        return shutil.which("git") is not None

    def is_repo(self) -> bool:
        return self._init_done and self.repo is not None

    def is_available(self) -> bool:
        return self.is_git_installed() and self.is_repo()

    @property
    def _repo(self) -> git.Repo:
        """Guaranteed non-None repo reference.

        Callers must check is_available() first.
        """
        assert self.repo is not None
        return self.repo

    def init_repo(self) -> bool:
        """Initialize a new git repository in the notes directory.

        Creates .gitignore, stages all existing notes, and creates
        an initial commit. Returns True on success.
        """
        if self.is_repo():
            return True
        if not self.is_git_installed():
            logger.error("Cannot init repo: git not installed")
            return False
        try:
            self._ensure_gitignore()
            self.repo = git.Repo.init(str(self.notes_dir))
            self._init_done = True
            self._stage_all()
            r: git.Repo = self.repo
            if r.is_dirty(index=True):
                r.index.commit("Initial commit")
            self._ensure_empty_tree()
            logger.info("Git repo initialized at %s", self.notes_dir)
            return True
        except (git.GitCommandError, OSError) as e:
            logger.error("Failed to init git repo: %s", e)
            self.repo = None
            self._init_done = False
            return False

    def _ensure_gitignore(self) -> None:
        """Create .gitignore in the notes directory if it doesn't exist."""
        gitignore_path = self.notes_dir / ".gitignore"
        if not gitignore_path.exists():
            try:
                gitignore_path.write_text(_GITIGNORE_CONTENT, encoding="utf-8")
                logger.debug("Created .gitignore at %s", gitignore_path)
            except OSError as e:
                logger.warning("Could not create .gitignore: %s", e)

    def _stage_all(self) -> None:
        """Stage all files in the notes directory."""
        repo = self.repo
        if not repo:
            return
        try:
            repo.git.add("--all")  # type: ignore[reportOptionalMemberAccess]
        except git.GitCommandError as e:
            logger.warning("Could not stage files: %s", e)

    def _git_execute(self, *args: str, timeout: int = 10) -> str:
        """Run a low-level git command with a hard timeout.

        Wraps ``self._repo.git.execute`` so that runaway git operations
        (e.g. background gc, slow index lock contention) cannot stall the
        I/O thread pool indefinitely.  Raises ``git.GitCommandError`` on
        failure or timeout so callers can handle both the same way.
        """
        try:
            return self._repo.git.execute(
                ["git"] + list(args),
                kill_after_timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            logger.warning("git command timed out (%ds): %s", timeout, args)
            raise git.GitCommandError(list(args), 128, stderr=str(exc)) from exc

    @slow_callback("git-auto-commit")
    def auto_commit(self, note_name: str) -> bool:
        """Stage and commit a single note file.

        Skips if the file has no changes since the last commit.
        Returns True if a commit was created.
        """
        if not self.is_available():
            return False
        try:
            filename = self._note_filename(note_name)
            filepath = self.notes_dir / filename
            if not filepath.exists():
                logger.debug("File '%s' does not exist, skipping auto-commit", filename)
                return False

            rel_path = str(filepath.relative_to(self.notes_dir))
            has_head = bool(self._repo.heads)

            if has_head:
                tracked = self._git_execute("ls-tree", "-r", "HEAD", rel_path).strip()
                if tracked:
                    diff = self._git_execute("diff", "HEAD", "--no-color", rel_path)
                    if not diff:
                        logger.debug("No changes to commit for '%s'", note_name)
                        return False

            self._repo.index.add([rel_path])
            self._repo.index.commit(f"auto: {note_name}")
            logger.debug("Auto-committed '%s'", note_name)
            return True
        except (git.GitCommandError, OSError, ValueError) as e:
            logger.error("Auto-commit failed for '%s': %s", note_name, e)
            return False

    @slow_callback("git-maintenance")
    def optimize_repository(self) -> None:
        """Pack loose objects when Git decides maintenance is warranted."""
        if not self.is_available():
            return
        try:
            self._git_execute("gc", "--auto", "--quiet", timeout=60)
        except git.GitCommandError as exc:
            logger.warning("Automatic Git maintenance failed: %s", exc)

    def commit_deletion(self, note_name: str, enc: bool = False) -> bool:
        """Commit the deletion of a note file."""
        if not self.is_available():
            return False
        try:
            ext = ".md.enc" if enc else ".md"
            filename = f"{note_name}{ext}"
            # Also try to remove the opposite extension from the index in case the
            # plain/encrypted variant was previously tracked.
            alt_variant = f"{note_name}.md" if enc else f"{note_name}.md.enc"
            try:
                self._repo.index.remove([alt_variant], working_tree=False)
            except (git.GitCommandError, ValueError):
                pass

            self._repo.index.remove([filename], working_tree=False)
            self._repo.index.commit(f"delete: {note_name}")
            logger.debug("Committed deletion of '%s'", note_name)
            return True
        except (git.GitCommandError, OSError, ValueError) as e:
            logger.error("Delete commit failed for '%s': %s", note_name, e)
            return False

    def snapshot(self, message: str = "") -> bool:
        """Stage all changes and create a manual snapshot commit.

        Args:
            message: Optional user-provided message appended to the commit.

        Returns True if a commit was created.
        """
        if not self.is_available():
            return False
        try:
            self._repo.git.add("--all")
            if not self._repo.is_dirty(index=True, working_tree=True):
                logger.debug("Nothing to snapshot")
                return False
            msg = f"snapshot: {message}" if message else "snapshot"
            self._repo.index.commit(msg)
            logger.debug("Snapshot commit created")
            return True
        except (git.GitCommandError, OSError) as e:
            logger.error("Snapshot failed: %s", e)
            return False

    def history(self, note_name: str, max_count: int = 50) -> list[CommitInfo]:
        """Return commit history for a specific note file.

        Returns a list of CommitInfo ordered newest-first.
        Follows renames so history is preserved across name changes.

        If the note was deleted and later recreated (at any point in its
        path history), only commits from the most recent Add commit onward
        are returned — history from before the deletion is excluded.
        """
        if not self.is_available():
            return []
        try:
            filename = self._note_filename(note_name)
            rel_path = filename

            # Find the birth of the current note life — the most recent
            # commit where this file (or its rename ancestor) was Added.
            # If the note was never deleted this will be the original
            # creation commit; if it was deleted and recreated it will
            # be the recreate commit.
            birth_hexsha = self._repo.git.log(
                "--follow",
                "--diff-filter=A",
                "--format=%H",
                "--max-count=1",
                "--",
                rel_path,
            ).strip()

            log_output = self._repo.git.log(
                "--follow",
                "--format=%H%x00%ct%x00%s",
                f"--max-count={max_count}",
                "--",
                rel_path,
            )
            if not log_output.strip():
                return []

            commits: list[CommitInfo] = []
            hit_birth = False
            for line in log_output.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\0", 2)
                if len(parts) < 3:
                    continue
                hexsha, timestamp_str, message = parts
                dt = datetime.datetime.fromtimestamp(int(timestamp_str))
                commits.append(
                    CommitInfo(
                        hexsha=hexsha,
                        message=message.strip(),
                        timestamp=dt,
                    )
                )
                if birth_hexsha and hexsha == birth_hexsha:
                    hit_birth = True
                    break

            # If we hit the birth commit, the commits list already ends at the
            # start of the current life — return as-is.
            # If we never hit it (no birth found or it's the only A, which is
            # the original creation and we walked past it), return all commits.
            if hit_birth or not birth_hexsha:
                return commits
            # birth found but wasn't hit in the output (walked past it) —
            # shouldn't happen with --follow, but guard anyway
            return commits
        except (git.GitCommandError, ValueError) as e:
            logger.error("History lookup failed for '%s': %s", note_name, e)
            return []

    _EMPTY_TREE = "4b825dc642cb6eb9a060e54bf899d153036e18d7"

    def _ensure_empty_tree(self) -> None:
        """Ensure the empty tree object exists in the repo's object store."""
        try:
            self._repo.git.rev_parse(self._EMPTY_TREE)
        except git.GitCommandError:
            self._repo.git.hash_object("-t", "tree", "-w", "--stdin", input="")

    def _resolve_path_at(self, commit_hexsha: str, current_path: str) -> str | None:
        """Find the file path at a specific git commit, following renames.

        Walks the full ``--follow`` history from HEAD so that renames that
        happened **after** *commit_hexsha* are detected (unlike ``git log
        --follow -1 <commit>`` which walks backward from that commit and
        never sees forward renames).
        """
        try:
            output = self._repo.git.log(
                "--follow", "--format=%H", "--name-only", "--", current_path
            )
            lines = output.splitlines()

            # Find the target commit hash in the output
            target_idx: int | None = None
            for i, line in enumerate(lines):
                if line.rstrip() == commit_hexsha:
                    target_idx = i
                    break
            if target_idx is None:
                return None

            # Skip blank lines after the hash
            j = target_idx + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            # Collect file paths until the next blank line or next commit hash
            paths: list[str] = []
            while j < len(lines):
                stripped = lines[j].strip()
                if not stripped:
                    break
                if len(stripped) == 40 and all(
                    c in "0123456789abcdef" for c in stripped
                ):
                    break
                paths.append(stripped)
                j += 1

            if paths:
                basename = current_path.rsplit("/", 1)[-1]
                matching = [
                    p for p in paths if p.endswith(f"/{basename}") or p == basename
                ]
                return (matching or paths)[-1]
        except git.GitCommandError:
            pass
        return None

    def diff(self, commit_hexsha: str, note_name: str) -> str:
        """Return the diff for a note file at a given commit (vs its parent).

        Returns a string with the diff output.  Handles root commits (no parent)
        by diffing against the empty tree so every line appears as an addition.
        Follows renames so pre-rename commits produce a meaningful diff.
        """
        if not self.is_available():
            return ""
        try:
            filename = self._note_filename(note_name)
            # Binary diff of encrypted ciphertext is not human-readable
            if filename.endswith(".md.enc"):
                return ""
            rel_path = filename

            commit_obj = self._repo.commit(commit_hexsha)
            parent = (
                commit_obj.parents[0].hexsha if commit_obj.parents else self._EMPTY_TREE
            )

            if commit_obj.parents:
                diff_text = self._repo.git.diff(
                    f"{parent}..{commit_hexsha}", "--", rel_path
                )
            else:
                self._ensure_empty_tree()
                diff_text = self._repo.git.diff(
                    self._EMPTY_TREE, commit_hexsha, "--", rel_path
                )

            if not diff_text:
                old_path = self._resolve_path_at(commit_hexsha, rel_path)
                if old_path and old_path != rel_path:
                    if commit_obj.parents:
                        diff_text = self._repo.git.diff(
                            f"{parent}..{commit_hexsha}", "--", old_path
                        )
                    else:
                        self._ensure_empty_tree()
                        diff_text = self._repo.git.diff(
                            self._EMPTY_TREE, commit_hexsha, "--", old_path
                        )
            return diff_text
        except (git.GitCommandError, ValueError) as e:
            logger.error("Diff failed for '%s' at %s: %s", note_name, commit_hexsha, e)
            return ""

    def restore(self, commit_hexsha: str, note_name: str) -> str | bytes | None:
        """Return the content of a note file as it was at a given commit.

        Returns str for plain-text notes and bytes for encrypted notes,
        or None on error.

        Follows renames so pre-rename commits can be restored after a
        folder rename or note rename.
        """
        if not self.is_available():
            return None
        try:
            filename = self._note_filename(note_name)
            return self._show_at_commit(commit_hexsha, filename)
        except (git.GitCommandError, ValueError) as e:
            logger.error(
                "Restore failed for '%s' at %s: %s", note_name, commit_hexsha, e
            )
            return None

    def _show_at_commit(self, commit_hexsha: str, filename: str) -> str | bytes:
        """'git show' a file at a commit, following renames if needed."""
        try:
            if filename.endswith(".md.enc"):
                return self._repo.git.show(
                    f"{commit_hexsha}:{filename}", stdout_as_string=False
                )
            return self._repo.git.show(f"{commit_hexsha}:{filename}")
        except git.GitCommandError:
            pass
        # Current path doesn't exist at this commit (pre-rename).
        # Resolve the historical path via full --follow walk from HEAD.
        old_path = self._resolve_path_at(commit_hexsha, filename)
        if old_path and old_path != filename:
            if filename.endswith(".md.enc"):
                return self._repo.git.show(
                    f"{commit_hexsha}:{old_path}", stdout_as_string=False
                )
            return self._repo.git.show(f"{commit_hexsha}:{old_path}")
        raise git.GitCommandError(
            "show", f"Could not restore {filename} at {commit_hexsha}"
        )

    def _note_filename(self, note_name: str) -> str:
        """Return the on-disk filename for a note, preferring .md over .md.enc."""
        plain = f"{note_name}.md"
        enc = f"{note_name}.md.enc"
        if (self.notes_dir / enc).exists():
            return enc
        return plain

    def rename_note(self, old_name: str, new_name: str) -> None:
        """Record a rename in git (stages the rename).

        Stages the deletion of the old file and the addition of the new
        file so git can detect the rename on the next commit.
        """
        if not self.is_available():
            return
        try:
            new_file = self._note_filename(new_name)

            # Always stage the new file (it exists on disk after
            # storage.rename_note has run).
            self._repo.index.add([new_file])

            # Remove old file from index regardless of whether it still
            # exists on disk.  Try both .md and .md.enc variants since we
            # don't know which was previously tracked.
            for variant in (f"{old_name}.md", f"{old_name}.md.enc"):
                try:
                    self._repo.index.remove([variant], working_tree=False)
                except (git.GitCommandError, ValueError):
                    pass

            logger.debug("Git rename staged: %s -> %s", old_name, new_name)
        except (git.GitCommandError, ValueError) as e:
            logger.warning("Git rename failed: %s", e)
