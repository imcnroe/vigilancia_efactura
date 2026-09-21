"""Construye el almacen de artefactos que dicta la configuracion."""

from __future__ import annotations

from typing import Any

from regwatch.core.settings import Settings
from regwatch.ingest.storage.artifact_store import (
    ArtifactStore,
    LocalArtifactStore,
    S3ArtifactStore,
)


class StoreUnavailableError(RuntimeError):
    """No se pudo hablar con el almacen de artefactos al arrancar.

    Existe para que un MinIO apagado se cuente en una linea en vez de en una traza de
    `urllib3`: es la causa mas frecuente de que la CLI no arranque en local.
    """


def build_store(settings: Settings) -> ArtifactStore:
    if settings.artifact_store == "local":
        return LocalArtifactStore(settings.artifact_local_root)

    import boto3
    from botocore.exceptions import BotoCoreError

    client = boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint_url,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        region_name=settings.s3_region,
    )
    try:
        ensure_bucket(client, settings.s3_bucket, settings.s3_region)
    except BotoCoreError as error:
        raise StoreUnavailableError(
            f"no se pudo acceder al almacen de artefactos en "
            f"{settings.s3_endpoint_url or 'AWS S3'}: {error}\n"
            f"Si trabajas en local, levantalo con `docker compose up -d`. Para trabajar "
            f"sin contenedores, pon ARTIFACT_STORE=local en el .env."
        ) from error
    return S3ArtifactStore(client, settings.s3_bucket)


def ensure_bucket(client: Any, bucket: str, region: str) -> None:
    """Crea el bucket si no existe y le activa el versionado.

    Un MinIO recien levantado no tiene buckets, y el primer `put` fallaria con
    `NoSuchBucket` en mitad de una ingesta. El versionado es la segunda linea de
    defensa del historico: el codigo no puede ser la unica.
    """
    from botocore.exceptions import ClientError

    try:
        client.head_bucket(Bucket=bucket)
        return
    except ClientError as error:
        if error.response.get("Error", {}).get("Code") not in ("404", "NoSuchBucket"):
            raise

    create: dict[str, Any] = {"Bucket": bucket}
    if region != "us-east-1":
        # AWS exige la region explicita fuera de us-east-1; MinIO la ignora.
        create["CreateBucketConfiguration"] = {"LocationConstraint": region}
    client.create_bucket(**create)
    client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
