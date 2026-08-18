#!/usr/bin/env python3
"""Standalone probe: index a handful of sample texts with fastembed and
search them with sqlite-vec, so retrieval quality can be sanity-checked
without going through the full wxPython app (no UI, no on-disk database -
everything runs in an in-memory SQLite connection).

Usage:
    pip install fastembed sqlite-vec
    python3 probes/index_and_search.py                  # runs a few built-in queries
    python3 probes/index_and_search.py "your own query"  # runs just that one

The built-in sample texts intentionally span a few unrelated topics (space,
cooking, Python, history) so a good embedding model should cleanly separate
them - a query about async programming should surface the Python entries at
the top and *not* the cooking ones, etc.

There are two independent experiments: the original English one (model,
sample texts, queries, and table names all suffixed `_en`) and a second one
in Spanish with accented characters (suffixed `_es`, using a multilingual
model since the English-only bge model wouldn't embed Spanish meaningfully).
Custom queries passed on the command line only override the English
experiment's queries - the Spanish one always runs with its own defaults, so
both experiments show something even when invoked as
`python3 probes/index_and_search.py "your own query"`.
"""
import sqlite3
import sys

import sqlite_vec
from fastembed import TextEmbedding

print('ALL MODELS', TextEmbedding.list_supported_models())


MODEL_NAME = "BAAI/bge-small-en-v1.5"
MODEL_NAME_ES = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"


SAMPLE_TEXTS = [
    ("space_1", "The James Webb Space Telescope observes the universe primarily in the infrared, "
                "letting it see through cosmic dust to galaxies formed shortly after the Big Bang."),
    ("space_2", "Mars has two small moons, Phobos and Deimos, thought to be captured asteroids "
                "rather than bodies formed alongside the planet."),
    ("cooking_1", "A roux is made by cooking equal parts flour and fat together; it's the classic "
                  "base for gravies, bechamel, and gumbo."),
    ("cooking_2", "Fermenting vegetables in a simple brine of salt and water encourages "
                  "lactobacillus bacteria to produce the tang typical of sauerkraut and kimchi."),
    ("python_1", "Python's asyncio event loop lets a single thread interleave many I/O-bound "
                 "tasks by suspending a coroutine at each `await` and resuming it once its "
                 "operation completes."),
    ("python_2", "List comprehensions in Python build a new list by evaluating an expression "
                 "for each item of an iterable, optionally filtered by an `if` clause."),
    ("history_1", "The printing press, developed by Johannes Gutenberg around 1440, dramatically "
                  "lowered the cost of producing books and accelerated the spread of literacy "
                  "across Europe."),
    ("history_2", "The Silk Road was not a single route but a network of trade paths connecting "
                  "China to the Mediterranean, carrying silk, spices, and ideas alike."),
]

DEFAULT_QUERIES = [
    "how do I cook onions until they thicken a sauce",
    "telescopes that look at infrared light",
    "async programming and coroutines",
    "old trade routes between Asia and Europe",
]

# Spanish counterpart of SAMPLE_TEXTS, deliberately full of accented characters
# (á, é, í, ó, ú, ñ) so encoding issues would show up immediately.
SAMPLE_TEXTS_ES = [
    ("espacio_1", "El telescopio espacial James Webb observa el universo principalmente en el "
                  "infrarrojo, lo que le permite ver a través del polvo cósmico galaxias formadas "
                  "poco después del Big Bang."),
    ("espacio_2", "Marte tiene dos pequeñas lunas, Fobos y Deimos, que se cree son asteroides "
                  "capturados en lugar de cuerpos formados junto al planeta."),
    ("cocina_1", "Un roux se prepara cocinando partes iguales de harina y grasa; es la base "
                 "clásica de salsas, bechamel y gumbo."),
    ("cocina_2", "Fermentar verduras en una salmuera sencilla de sal y agua favorece que las "
                 "bacterias lactobacilo produzcan el sabor ácido típico del chucrut y el kimchi."),
    ("python_1", "El bucle de eventos asyncio de Python permite que un solo hilo intercale "
                 "muchas tareas de entrada/salida suspendiendo una corrutina en cada `await` y "
                 "reanudándola cuando termina su operación."),
    ("python_2", "Las listas por comprensión en Python construyen una lista nueva evaluando una "
                 "expresión para cada elemento de un iterable, opcionalmente filtrado por una "
                 "cláusula `if`."),
    ("historia_1", "La imprenta, desarrollada por Johannes Gutenberg alrededor de 1440, redujo "
                   "drásticamente el costo de producir libros y aceleró la difusión de la "
                   "alfabetización en Europa."),
    ("historia_2", "La Ruta de la Seda no era una única vía sino una red de caminos comerciales "
                   "que conectaban China con el Mediterráneo, transportando seda, especias e "
                   "ideas por igual."),
]

DEFAULT_QUERIES_ES = [
    "cómo cocinar cebolla hasta que espese una salsa",
    "telescopios que observan luz infrarroja",
    "programación asíncrona y corrutinas",
    "antiguas rutas comerciales entre Asia y Europa",
]

TOP_K = 3


def build_index(conn: sqlite3.Connection, model: TextEmbedding, sample_texts, suffix: str) -> int:
    """Embed every sample text, store it in a plain table plus a matching
    sqlite-vec vec0 table (both named with `suffix`, so multiple experiments
    can share one connection), and return the vector dimension used."""
    conn.execute(f"CREATE TABLE docs_{suffix} (id INTEGER PRIMARY KEY, key TEXT, text TEXT)")

    vectors = list(model.embed([text for _key, text in sample_texts]))
    dim = len(vectors[0])
    conn.execute(f"CREATE VIRTUAL TABLE vec_docs_{suffix} USING vec0(embedding float[{dim}])")

    for row_id, ((key, text), vector) in enumerate(zip(sample_texts, vectors), start=1):
        conn.execute(f"INSERT INTO docs_{suffix} (id, key, text) VALUES (?, ?, ?)", (row_id, key, text))
        conn.execute(
            f"INSERT INTO vec_docs_{suffix} (rowid, embedding) VALUES (?, ?)",
            (row_id, sqlite_vec.serialize_float32(vector)),
        )
    conn.commit()
    return dim


def search(conn: sqlite3.Connection, model: TextEmbedding, query: str, suffix: str, k: int = TOP_K):
    query_vector = list(model.embed([query]))[0]
    return conn.execute(
        f"""
        SELECT docs_{suffix}.key, docs_{suffix}.text, vec_docs_{suffix}.distance
        FROM vec_docs_{suffix}
        JOIN docs_{suffix} ON docs_{suffix}.id = vec_docs_{suffix}.rowid
        WHERE vec_docs_{suffix}.embedding MATCH ? AND k = ?
        ORDER BY vec_docs_{suffix}.distance
        """,
        (sqlite_vec.serialize_float32(query_vector), k),
    ).fetchall()


def run_experiment(conn, model_name, sample_texts, queries, suffix, label):
    print(f"Loading fastembed model {model_name!r} (first run downloads it - can take a minute)...")
    model = TextEmbedding(model_name=model_name)

    dim = build_index(conn, model, sample_texts, suffix)
    print(f"[{label}] Indexed {len(sample_texts)} texts at {dim} dimensions.\n")

    for query in queries:
        print(f'[{label}] Query: "{query}"')
        for key, text, distance in search(conn, model, query, suffix):
            print(f"  [{distance:.4f}] {key}: {text}")
        print()


def main() -> None:
    queries = sys.argv[1:] or DEFAULT_QUERIES

    conn = sqlite3.connect(":memory:")
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)

    run_experiment(conn, MODEL_NAME, SAMPLE_TEXTS, queries, "en", "EN")
    run_experiment(conn, MODEL_NAME_ES, SAMPLE_TEXTS_ES, DEFAULT_QUERIES_ES, "es", "ES")


if __name__ == "__main__":
    main()
