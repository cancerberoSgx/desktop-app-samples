from typing import List


def serialize_vector(vector: List[float]) -> bytes:
    """Pack a Python float list into the compact little-endian float32 blob
    sqlite-vec's `vec0` tables expect for both inserts and MATCH queries."""
    import sqlite_vec

    return sqlite_vec.serialize_float32(vector)
