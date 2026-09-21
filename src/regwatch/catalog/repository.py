"""Consultas del catalogo que comparten la CLI y el servicio de ingesta.

Funciones sobre una `Session` abierta, sin transaccion propia: quien llama decide los
limites del commit. Asi el servicio de ingesta puede dar de alta y ejecutar en la misma
transaccion cuando le convenga.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import String, cast, select
from sqlalchemy.orm import Session

from regwatch.catalog.models import DocumentType, Jurisdiction, RegulationFamily, Source
from regwatch.core.enums import CollectorType, Priority, SourceKind
from regwatch.ingest.schedule import validate_cron


class UnknownSourceError(LookupError):
    """No hay ninguna fuente con ese identificador."""


class AmbiguousSourceError(LookupError):
    """El prefijo de UUID coincide con mas de una fuente."""


class UnknownFamilyError(LookupError):
    """No existe la familia normativa `JURISDICCION/FAMILIA`."""


class UnknownDocumentTypeError(LookupError):
    """La familia existe pero no tiene ese tipo de documento."""


def find_source(session: Session, ref: str | uuid.UUID) -> Source:
    """Localiza una fuente por UUID completo o por un fragmento de el.

    **No vale buscar solo por prefijo.** Estos identificadores son UUIDv7, y sus
    primeros 12 caracteres hexadecimales son el instante de creacion en milisegundos:
    dos fuentes dadas de alta el mismo dia comparten casi todo el prefijo. Lo que
    discrimina es la cola aleatoria, asi que se busca el fragmento en cualquier
    posicion. A la escala de este catalogo (decenas de fuentes) el recorrido secuencial
    no importa.
    """
    if isinstance(ref, uuid.UUID):
        source = session.get(Source, ref)
        if source is None:
            raise UnknownSourceError(f"no hay fuente con id {ref}")
        return source

    ref = ref.strip().lower()
    try:
        return find_source(session, uuid.UUID(ref))
    except ValueError:
        pass

    if len(ref) < 4:
        raise UnknownSourceError(f"fragmento demasiado corto: {ref!r} (minimo 4 caracteres)")

    matches = session.scalars(
        select(Source).where(cast(Source.id, String).like(f"%{ref}%")).limit(4)
    ).all()
    if not matches:
        raise UnknownSourceError(f"ninguna fuente contiene {ref!r} en su identificador")
    if len(matches) > 1:
        listed = "\n".join(f"  {match.id}  {match.name}" for match in matches)
        raise AmbiguousSourceError(
            f"{ref!r} coincide con varias fuentes:\n{listed}\n"
            f"Los UUIDv7 comparten prefijo: usa la cola del identificador, que es la "
            f"parte aleatoria."
        )
    return matches[0]


def list_sources(session: Session, *, include_inactive: bool = False) -> list[Source]:
    """Fuentes del catalogo ordenadas por familia y nombre. Las borradas no salen nunca."""
    query = (
        select(Source)
        .join(RegulationFamily, Source.regulation_family_id == RegulationFamily.id)
        .join(Jurisdiction, RegulationFamily.jurisdiction_id == Jurisdiction.id)
        .where(Source.deleted_at.is_(None))
        .order_by(Jurisdiction.code, RegulationFamily.code, Source.name)
    )
    if not include_inactive:
        query = query.where(Source.is_active.is_(True))
    return list(session.scalars(query).all())


def find_family(session: Session, ref: str) -> RegulationFamily:
    """`ES/FACTURAE` -> la familia FACTURAE de la jurisdiccion ES.

    Se limpia el espacio de cada componente por separado: estas referencias se pegan
    desde el catalogo semilla o desde un correo, y arrastran espacios que no son culpa
    de quien las teclea.
    """
    try:
        jurisdiction_code, family_code = (part.strip() for part in ref.upper().split("/", 1))
    except ValueError:
        raise UnknownFamilyError(
            f"la familia se indica como JURISDICCION/FAMILIA, por ejemplo ES/FACTURAE; "
            f"recibido {ref!r}"
        ) from None

    family = session.scalar(
        select(RegulationFamily)
        .join(Jurisdiction, RegulationFamily.jurisdiction_id == Jurisdiction.id)
        .where(Jurisdiction.code == jurisdiction_code, RegulationFamily.code == family_code)
    )
    if family is None:
        raise UnknownFamilyError(_why_no_family(session, jurisdiction_code, family_code))
    return family


def _why_no_family(session: Session, jurisdiction_code: str, family_code: str) -> str:
    """Explica por que no se encontro, en vez de repetir que no esta.

    Se comprueba primero la jurisdiccion y despues la familia, en ese orden: si la
    jurisdiccion no existe, eso es lo que hay que arreglar, y decir donde vive la familia
    seria un dato cierto que despista.
    """
    if not session.scalar(select(Jurisdiction.id).where(Jurisdiction.code == jurisdiction_code)):
        known = session.scalars(select(Jurisdiction.code).order_by(Jurisdiction.code)).all()
        if not known:
            return (
                "el catalogo esta vacio: falta aplicar la migracion semilla "
                "(`alembic upgrade head`)."
            )
        return f"no existe la jurisdiccion {jurisdiction_code}. Las que hay: {', '.join(known)}."

    # El caso frecuente: se teclea `ES/TICKETBAI` pensando en España, pero TicketBAI vive
    # en las tres haciendas forales. Decir donde si esta ahorra el viaje.
    elsewhere = session.scalars(
        select(Jurisdiction.code)
        .join(RegulationFamily, RegulationFamily.jurisdiction_id == Jurisdiction.id)
        .where(RegulationFamily.code == family_code)
        .order_by(Jurisdiction.code)
    ).all()
    if elsewhere:
        return (
            f"la familia {family_code} no esta en {jurisdiction_code}, sino en "
            f"{', '.join(elsewhere)}."
        )

    available = session.scalars(
        select(RegulationFamily.code)
        .join(Jurisdiction, RegulationFamily.jurisdiction_id == Jurisdiction.id)
        .where(Jurisdiction.code == jurisdiction_code)
        .order_by(RegulationFamily.code)
    ).all()
    return (
        f"{jurisdiction_code} no tiene la familia {family_code}. "
        f"Las que tiene: {', '.join(available) or 'ninguna'}."
    )


def find_document_type(session: Session, family: RegulationFamily, code: str) -> DocumentType:
    document_type = session.scalar(
        select(DocumentType).where(
            DocumentType.regulation_family_id == family.id, DocumentType.code == code.upper()
        )
    )
    if document_type is None:
        raise UnknownDocumentTypeError(
            f"la familia {family.code} no tiene el tipo de documento {code.upper()}"
        )
    return document_type


def create_source(
    session: Session,
    *,
    family: RegulationFamily,
    name: str,
    url: str,
    source_kind: str,
    collector_type: str,
    check_frequency: str,
    priority: str = Priority.WARM.value,
    document_type: DocumentType | None = None,
    notes: str | None = None,
    is_active: bool = True,
    collector_config: dict[str, Any] | None = None,
) -> Source:
    """Da de alta una fuente validando lo que la base no puede validar (el cron)."""
    source = Source(
        regulation_family_id=family.id,
        document_type_id=document_type.id if document_type is not None else None,
        name=name.strip(),
        url=url.strip(),
        source_kind=SourceKind(source_kind.upper()).value,
        collector_type=CollectorType(collector_type.upper()).value,
        collector_config=dict(collector_config or {}),
        check_frequency=validate_cron(check_frequency.strip()),
        priority=Priority(priority.upper()).value,
        is_active=is_active,
        notes=notes,
        # Nulo: vencida desde el primer momento, para que la primera pasada de
        # `collect run-due` la recoja sin esperar al siguiente cron.
        next_check_at=None,
    )
    session.add(source)
    session.flush()
    return source
