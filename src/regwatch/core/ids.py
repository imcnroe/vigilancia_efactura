"""UUIDv7: identificador ordenable por tiempo (RFC 9562, seccion 5.7).

Se implementa aqui en vez de tirar de una dependencia porque son treinta lineas y
evita atarnos a un paquete de un solo autor para algo que esta en cada clave primaria
del sistema.

Disposicion de los 128 bits:
    48  timestamp Unix en milisegundos, big-endian
     4  version (0111)
    12  rand_a
     2  variant (10)
    62  rand_b
"""

from __future__ import annotations

import os
import time
from uuid import UUID


def uuid7() -> UUID:
    """Devuelve un UUIDv7 nuevo."""
    timestamp_ms = time.time_ns() // 1_000_000
    rand = int.from_bytes(os.urandom(10), "big")

    rand_a = (rand >> 62) & 0x0FFF
    rand_b = rand & 0x3FFF_FFFF_FFFF_FFFF

    value = (timestamp_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b

    return UUID(int=value)


def timestamp_ms_of(value: UUID) -> int:
    """Extrae el instante de creacion de un UUIDv7, en milisegundos Unix.

    Util para depurar y para ordenar sin tocar `created_at`. Falla si no es v7.
    """
    if value.version != 7:
        raise ValueError(f"no es un UUIDv7: {value}")
    return value.int >> 80
