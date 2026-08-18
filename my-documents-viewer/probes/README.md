# Probes

Small standalone scripts for sanity-checking the embedding/vector-search
stack outside the full wxPython app - no UI, no on-disk database, quick to
run and quick to read.

## `index_and_search.py`

Embeds a handful of short sample texts spanning a few unrelated topics
(space, cooking, Python, history) with `fastembed`, stores them in an
in-memory SQLite `vec0` table via `sqlite-vec`, and runs a few example
queries against it (or a query you pass on the command line) so you can
eyeball whether the top result for e.g. "async programming and coroutines"
is actually the Python entry and not, say, cooking.

```bash
pip install fastembed sqlite-vec
python3 probes/index_and_search.py
python3 probes/index_and_search.py "your own query here"
```

Lower distance = more similar. If the ranking looks wrong (unrelated topics
outranking the obviously relevant one), that's a signal to try a different
`MODEL_NAME` in the script before assuming the app's indexing pipeline is at
fault - this probe uses the exact same embed-then-vec0-search steps the main
app's `DocumentRepository` does, just without the file reading, chunking, or
FTS5 side of it.
