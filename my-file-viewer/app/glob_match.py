import fnmatch
from typing import List

"""Pure glob-pattern matching for the right sidebar's Patterns filter (see
FolderTreeCtrl.set_glob_pattern) - a small, dependency-free module in the
same spirit as formatting.py: no filesystem I/O of its own, so it doesn't
belong in FileSystemService (that's reserved for actual blocking OS calls -
see CLAUDE.md's "service" pattern).

A pattern is matched against a path *relative to the currently open
folder* (FolderTreeCtrl._rel_segments), never an absolute path - "src" is
always the top of the pattern's world, whatever folder happens to be open.
Matching is segment-by-segment (split on "/"), with two special rules
layered on top of plain per-segment globbing (`*`, `?`, `[seq]`, all via
fnmatch - case-insensitively, same convention as quick search):

- `**` matches zero or more *whole* path segments (the same semantics as
  Python's own `glob.glob(pattern, recursive=True)`), which is what lets
  "src/**/*.py" match a `.py` file directly inside `src` (zero
  intermediate segments) just as well as one several folders deeper.
- A pattern with no "/" at all is implicitly anchored with a leading
  "**/", so "*.py"/"*foo*" match a name at *any* depth rather than only at
  the top level - the same convention .gitignore and `ripgrep --glob` use
  for a bare, unslashed pattern.
"""


def normalize_pattern(pattern: str) -> List[str]:
    """Splits `pattern` into its "/"-separated segments, ready for
    full_match/could_match_descendant. Leading/trailing "/" are stripped
    (a pattern is always relative to the open folder, never absolute)."""
    pattern = pattern.strip().strip("/")
    if "/" not in pattern:
        pattern = f"**/{pattern}"
    return pattern.split("/")


def _segment_matches(name: str, segment_pattern: str) -> bool:
    return fnmatch.fnmatch(name.lower(), segment_pattern.lower())


def full_match(path_segments: List[str], pattern_segments: List[str]) -> bool:
    """True if `path_segments` (a file or folder's whole path, relative to
    the open folder) completely satisfies `pattern_segments` - "completely"
    meaning every segment is accounted for, not just a prefix. This is
    what decides whether a given row itself is a match (as opposed to
    merely a navigable ancestor of one - see could_match_descendant)."""
    if not pattern_segments:
        return not path_segments
    head = pattern_segments[0]
    if head == "**":
        # ** matches zero segments (skip it and try the rest of the
        # pattern right here) or one-more-than-however-many-it-already-
        # matched (consume one path segment and stay on **) - trying both
        # is what makes "**" absorb any number of directory levels,
        # including none at all.
        if full_match(path_segments, pattern_segments[1:]):
            return True
        return bool(path_segments) and full_match(path_segments[1:], pattern_segments)
    if not path_segments:
        return False
    return _segment_matches(path_segments[0], head) and full_match(path_segments[1:], pattern_segments[1:])


def could_match_descendant(path_segments: List[str], pattern_segments: List[str]) -> bool:
    """True if `path_segments` (a folder's own path, relative to the open
    folder) is still a viable, extendable prefix of `pattern_segments` -
    i.e. whether something *deeper* than this folder could still satisfy
    the pattern, so the folder is worth keeping visible/expandable even
    though it doesn't fully match itself (see full_match). This needs no
    loaded children at all: it's a pure function of the pattern text and
    the folder's own path, which is what lets a pattern like
    "src/**/*.py" keep "src" (and only "src") visible and expandable at
    the top level *before* it's ever been expanded - unlike quick search,
    whose own "keep this folder as a match's ancestor" logic can only
    look at children it's already fetched.

    Once a "**" is reached in the pattern while path segments remain,
    the answer is unconditionally True: "**" can absorb any number of
    further segments, so nothing below can ever be structurally ruled out
    from that point on."""
    if not path_segments:
        return True
    if not pattern_segments:
        return False
    head = pattern_segments[0]
    if head == "**":
        return True
    return _segment_matches(path_segments[0], head) and could_match_descendant(
        path_segments[1:], pattern_segments[1:]
    )
