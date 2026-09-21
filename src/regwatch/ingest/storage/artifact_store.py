"""Almacen de artefactos.

Las claves van **direccionadas por contenido**: `sha256/ab/cd/<hash>`. Eso hace la
escritura idempotente por construccion (si el objeto ya existe no se reescribe) y
convierte la deduplicacion en un efecto secundario gratuito.

Consecuencia que hay que tener presente: dos fuentes que descarguen el mismo fichero
comparten objeto. Por eso **borrar una fila de `artifact` no puede borrar nunca el
objeto**. De todos modos no se borra ninguna de las dos cosas.
"""

from __future__ import annotations

import abc
import hashlib
from pathlib import Path
from typing import Any


def content_key(content: bytes) -> str:
    """Clave direccionada por contenido, con dos niveles de prefijo.

    El troceado en subdirectorios evita listados de un millon de entradas en un solo
    prefijo, que en S3 no importa pero en un sistema de ficheros local si.
    """
    digest = hashlib.sha256(content).hexdigest()
    return f"sha256/{digest[:2]}/{digest[2:4]}/{digest}"


class ArtifactStore(abc.ABC):
    """Almacen de solo escritura y lectura. No expone borrado a proposito."""

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def put(self, content: bytes, *, content_type: str | None = None) -> str:
        """Guarda el contenido y devuelve su clave. Idempotente."""

    @abc.abstractmethod
    def get(self, key: str) -> bytes: ...


class LocalArtifactStore(ArtifactStore):
    """Implementacion en disco, para tests y desarrollo sin MinIO."""

    def __init__(self, root: Path) -> None:
        self._root = root
        self._root.mkdir(parents=True, exist_ok=True)

    def _path_of(self, key: str) -> Path:
        return self._root / key

    def exists(self, key: str) -> bool:
        return self._path_of(key).is_file()

    def put(self, content: bytes, *, content_type: str | None = None) -> str:
        del content_type
        key = content_key(content)
        path = self._path_of(key)
        if path.is_file():
            return key
        path.parent.mkdir(parents=True, exist_ok=True)
        # Escritura atomica: si el proceso muere a medias no queda un fichero truncado
        # bajo una clave que afirma ser el hash de su contenido.
        temporary = path.with_suffix(".partial")
        temporary.write_bytes(content)
        temporary.replace(path)
        return key

    def get(self, key: str) -> bytes:
        path = self._path_of(key)
        if not path.is_file():
            raise KeyError(key)
        return path.read_bytes()


class S3ArtifactStore(ArtifactStore):
    """Implementacion sobre S3 o compatible (MinIO en local).

    El bucket debe tener versionado activado y, si el proveedor lo soporta, object
    lock. El codigo no puede ser la unica defensa del histórico.
    """

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def exists(self, key: str) -> bool:
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except Exception:
            return False
        return True

    def put(self, content: bytes, *, content_type: str | None = None) -> str:
        key = content_key(content)
        if self.exists(key):
            return key
        extra: dict[str, str] = {}
        if content_type:
            extra["ContentType"] = content_type
        self._client.put_object(Bucket=self._bucket, Key=key, Body=content, **extra)
        return key

    def get(self, key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=key)
        data: bytes = response["Body"].read()
        return data
