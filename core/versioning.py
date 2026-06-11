"""Git-based versioning for Tokyo Notes notes.

Provides GitVersionController which manages a git repository inside
the user's notes directory. Supports auto-commit on save, manual
snapshots, history browsing, diff viewing, and restore.
"""

from __future__ import annotations

import datetime
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import git

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

_GITIGNORE_CONTENT = """\
# Tokyo Notes git versioning
*.tmp
._*
"""


@dataclass
class CommitInfo:
    hexsha: str
    message: str
    timestamp: datetime.datetime
    summary: str = ""


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
                tracked = self._repo.git.ls_tree("-r", "HEAD", rel_path).strip()
                if tracked:
                    diff = self._repo.git.diff("HEAD", rel_path, no_color=True)
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

    def commit_deletion(self, note_name: str) -> bool:
        """Commit the deletion of a note file."""
        if not self.is_available():
            return False
        try:
            filename = self._note_filename(note_name)

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
        """
        if not self.is_available():
            return []
        try:
            filename = self._note_filename(note_name)
            rel_path = filename
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

    def diff(self, commit_hexsha: str, note_name: str) -> str:
        """Return the diff for a note file at a given commit (vs its parent).

        Returns a string with the diff output.  Handles root commits (no parent)
        by diffing against the empty tree so every line appears as an addition.
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
            return diff_text
        except (git.GitCommandError, ValueError) as e:
            logger.error("Diff failed for '%s' at %s: %s", note_name, commit_hexsha, e)
            return ""

    def restore(self, commit_hexsha: str, note_name: str) -> str | bytes | None:
        """Return the content of a note file as it was at a given commit.

        Returns str for plain-text notes and bytes for encrypted notes,
        or None on error.
        """
        if not self.is_available():
            return None
        try:
            filename = self._note_filename(note_name)
            if filename.endswith(".md.enc"):
                content: str | bytes = self._repo.git.show(
                    f"{commit_hexsha}:{filename}", stdout_as_string=False
                )
            else:
                content = self._repo.git.show(f"{commit_hexsha}:{filename}")
            return content
        except (git.GitCommandError, ValueError) as e:
            logger.error(
                "Restore failed for '%s' at %s: %s", note_name, commit_hexsha, e
            )
            return None

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
            old_file = self._note_filename(old_name)
            new_file = self._note_filename(new_name)
            old_path = self.notes_dir / old_file
            new_path = self.notes_dir / new_file

            if old_path.exists():
                self._repo.index.add([old_file])
                self._repo.index.remove([old_file], working_tree=True)
            elif not old_path.exists() and new_path.exists():
                self._repo.index.add([new_file])

            logger.debug("Git rename staged: %s -> %s", old_name, new_name)
        except (git.GitCommandError, ValueError) as e:
            logger.warning("Git rename failed: %s", e)
