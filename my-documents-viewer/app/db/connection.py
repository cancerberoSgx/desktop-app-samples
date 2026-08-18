import sqlite3

from .paths import db_path


def get_connection() -> sqlite3.Connection:
    # check_same_thread=False: this one connection is opened on the UI thread
    # in MainFrame's composition root but is also used from AsyncTaskRunner's
    # worker thread - DocumentRepository.index_paths and .hybrid_search are
    # both blocking calls that run there by design (see CLAUDE.md). sqlite3
    # defaults to forbidding cross-thread use of a connection; since
    # AsyncTaskRunner only ever runs one job at a time per page (the `_busy`
    # flag) and pages don't issue their own blocking DB calls while a job is
    # in flight, plain reuse of this connection across threads is safe here
    # without adding a lock.
    conn = sqlite3.connect(db_path(), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    load_vec_extension(conn)
    return conn


def load_vec_extension(conn: sqlite3.Connection) -> bool:
    """Load the sqlite-vec extension into `conn` so `vec0` virtual tables
    (see repositories.DocumentRepository._ensure_vec_table) can be created
    and queried on it. Returns True if vector search is available on this
    connection, False if it isn't - callers must treat that as a soft
    degradation (fall back to full-text-only search), not an error:

    - the `sqlite-vec` package may simply not be installed (it's an
      optional dependency in requirements.txt in spirit, even though it's
      pinned there, precisely so a broken/offline install of it doesn't
      brick the rest of the app), or
    - this Python build may not support loadable extensions at all (some
      distro-packaged or Windows Store Python builds omit
      `sqlite3.Connection.enable_load_extension` entirely) - that raises
      AttributeError, not an ImportError, so it's caught separately.
    """
    try:
        import sqlite_vec
    except ImportError:
        return False

    try:
        conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)
        finally:
            conn.enable_load_extension(False)
    except AttributeError:
        return False
    return True


def vector_search_available(conn: sqlite3.Connection) -> bool:
    """Whether `conn` actually has a working sqlite-vec extension loaded -
    call this once at startup (after get_connection()) and thread the result
    into DocumentRepository(vector_enabled=...) so the rest of the app can
    degrade to full-text-only search instead of raising OperationalError
    from every vec0 table access."""
    try:
        conn.execute("SELECT vec_version()")
    except sqlite3.OperationalError:
        return False
    return True
