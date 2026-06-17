from __future__ import annotations

from ._shared import *  # noqa: F401,F403


class NotesMixin:
    def set_notes(self, text: str):
        # Internal use only (seeding/migration). Agents must go through
        # edit_notes — see the API layer.
        self.data["notes"] = text

    def edit_notes(self, old_string: str, new_string: str) -> str:
        """Replace one occurrence of old_string with new_string in the notes.

        Mirrors the Edit-tool pattern: the caller must already know the
        current contents (old_string must match verbatim and uniquely),
        which prevents blind overwrites. The <immutable>…</immutable>
        header is protected — any edit that would alter or drop it is
        rejected.

        Exception: when notes are still empty there is nothing to match
        against, so an empty old_string seeds them with new_string.
        """
        notes = self.data.get("notes", "")
        if old_string == new_string:
            return notes
        if not old_string:
            # Seeding case: empty notes have nothing to match against, so an
            # empty old_string is treated as the initial insert. Once notes are
            # non-empty an empty old_string stays rejected (it would be an
            # ambiguous blind overwrite).
            if notes == "":
                self.data["notes"] = new_string
                return new_string
            raise NotesOldStringNotFound("old_string must not be empty")
        count = notes.count(old_string)
        if count == 0:
            if notes == "":
                raise NotesOldStringNotFound(
                    "notes are currently empty — pass an empty old_string to seed them"
                )
            raise NotesOldStringNotFound(
                "old_string not found in current notes — re-read GET /api/v1/notes and try again"
            )
        if count > 1:
            raise NotesOldStringNotUnique(
                f"old_string matches {count} locations; include more surrounding "
                "context so the match is unique",
                count,
            )
        new_notes = notes.replace(old_string, new_string, 1)
        if _immutable_blocks(new_notes) != _immutable_blocks(notes):
            raise NotesImmutableRegionViolation(
                "edit would alter the <immutable>…</immutable> header — that region is read-only"
            )
        self.data["notes"] = new_notes
        return new_notes
