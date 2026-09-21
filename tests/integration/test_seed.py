"""La semilla del catalogo, comprobada contra el esquema real.

No basta con que la migracion se aplique sin error: lo que importa es que despues de
aplicarla se pueda **dar de alta una fuente**, que es lo que la fase 1 tiene que
demostrar. Estos tests corren sobre la base temporal del `conftest`, ya migrada a
`head`, asi que verifican la semilla tal y como queda en produccion.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session, sessionmaker

from regwatch.catalog.models import DocumentType, Jurisdiction, RegulationFamily
from regwatch.catalog.repository import (
    UnknownDocumentTypeError,
    UnknownFamilyError,
    create_source,
    find_document_type,
    find_family,
)
from regwatch.core.enums import SubscriptionStatus

pytestmark = pytest.mark.integration

#: Los tres idiomas de la interfaz. Si falta una traduccion se muestra el original
#: marcado, nunca cadena vacia, pero en el catalogo semilla no deberia faltar ninguna.
LOCALES = ("es", "fr", "en")

#: Las jurisdicciones que crea la semilla. Sirve para distinguirlas de las que dan de
#: alta otros modulos de test sobre la misma base.
SEEDED_JURISDICTIONS = ("ES", "ES-VI", "ES-BI", "ES-SS", "ES-NA", "FR", "EU")


def test_the_five_spanish_regulation_families_are_seeded(
    session_factory: sessionmaker[Session],
) -> None:
    """Las familias del apartado 2, cada una localizable por `JURISDICCION/FAMILIA`."""
    expected = {
        "ES/VERIFACTU",
        "ES/SII",
        "ES/FACTURAE",
        "ES/B2B_CREA_CRECE",
        "ES/FACE",
        "ES-VI/TICKETBAI",
        "ES-BI/TICKETBAI",
        "ES-SS/TICKETBAI",
        "ES-NA/NAVARRA",
    }
    with session_factory() as session:
        for ref in expected:
            family = find_family(session, ref)
            assert family.code == ref.split("/", 1)[1]


def test_the_french_families_and_peppol_are_seeded(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as session:
        for ref in ("FR/FR_EINVOICING", "FR/E_REPORTING", "FR/CHORUS_PRO", "EU/PEPPOL"):
            assert find_family(session, ref) is not None


def test_the_three_basque_provinces_hang_from_spain(
    session_factory: sessionmaker[Session],
) -> None:
    """Son tres jurisdicciones distintas, no una: el mismo campo del mismo esquema es
    obligatorio en Gipuzkoa y opcional en las otras dos."""
    with session_factory() as session:
        spain = session.scalar(select(Jurisdiction).where(Jurisdiction.code == "ES"))
        assert spain is not None
        assert spain.parent_id is None

        children = {child.code for child in spain.children}
        assert children == {"ES-VI", "ES-BI", "ES-SS", "ES-NA"}
        for child in spain.children:
            assert child.country_code == "ES"

        # Cada foral tiene su propia familia TicketBAI, con su propia autoridad.
        authorities = {
            find_family(session, f"{code}/TICKETBAI").authority
            for code in ("ES-VI", "ES-BI", "ES-SS")
        }
        assert len(authorities) == 3, authorities


def test_peppol_hangs_from_a_transversal_jurisdiction(
    session_factory: sessionmaker[Session],
) -> None:
    """Peppol afecta a ambos paises. Colgarlo de `EU` evita duplicar la familia y que un
    cambio en una lista de codigos genere dos avisos identicos."""
    with session_factory() as session:
        peppol = find_family(session, "EU/PEPPOL")
        assert peppol.jurisdiction.code == "EU"
        assert peppol.jurisdiction.parent_id is None
        assert peppol.jurisdiction.country_code == "EU"


def test_every_seeded_name_is_translated_into_the_three_interface_languages(
    session_factory: sessionmaker[Session],
) -> None:
    """El cliente frances no compra un producto que solo habla español (apartado 3).

    Se comprueban solo las filas de la semilla: la base de tests es compartida por la
    sesion y otros modulos dan de alta jurisdicciones de prueba con un solo idioma.
    """
    with session_factory() as session:
        jurisdictions = session.scalars(
            select(Jurisdiction).where(Jurisdiction.code.in_(SEEDED_JURISDICTIONS))
        ).all()
        assert len(jurisdictions) == len(SEEDED_JURISDICTIONS)

        families = session.scalars(
            select(RegulationFamily)
            .join(Jurisdiction, RegulationFamily.jurisdiction_id == Jurisdiction.id)
            .where(Jurisdiction.code.in_(SEEDED_JURISDICTIONS))
        ).all()
        assert len(families) == 16

        document_types = session.scalars(
            select(DocumentType)
            .join(RegulationFamily, DocumentType.regulation_family_id == RegulationFamily.id)
            .join(Jurisdiction, RegulationFamily.jurisdiction_id == Jurisdiction.id)
            .where(Jurisdiction.code.in_(SEEDED_JURISDICTIONS))
        ).all()
        assert len(document_types) == 6

        rows: list[Any] = [*jurisdictions, *families, *document_types]
        for row in rows:
            for locale in LOCALES:
                value = row.name_i18n.get(locale)
                assert value, f"{type(row).__name__} {row.code}: falta {locale}"


def test_official_url_is_only_set_where_it_was_verified(
    session_factory: sessionmaker[Session],
) -> None:
    """El apartado 0 prohibe inventar URLs de organismos. Solo las de nivel A del
    catalogo semilla, descargadas e inspeccionadas, llevan `official_url`."""
    with session_factory() as session:
        with_url = {
            family.code: family.official_url
            for family in session.scalars(select(RegulationFamily)).all()
            if family.official_url is not None
        }
        assert set(with_url) == {"FACTURAE", "VERIFACTU"}
        assert with_url["FACTURAE"].startswith("https://www.facturae.gob.es/")
        assert with_url["VERIFACTU"].startswith("https://sede.agenciatributaria.gob.es/")


def test_document_types_cover_both_countries(session_factory: sessionmaker[Session]) -> None:
    with session_factory() as session:
        facturae = find_family(session, "ES/FACTURAE")
        for code in ("FACTURAE_32", "FACTURAE_321", "FACTURAE_322"):
            assert find_document_type(session, facturae, code).code == code

        french = find_family(session, "FR/FR_EINVOICING")
        for code in ("FACTUR_X", "UBL_INVOICE", "CII_INVOICE"):
            assert find_document_type(session, french, code).code == code

        # Verifactu se vigila sin tipo de documento: sus esquemas existen pero sus
        # nombres no estan verificados, y `source.document_type_id` admite nulo.
        with pytest.raises(UnknownDocumentTypeError):
            find_document_type(session, find_family(session, "ES/VERIFACTU"), "SUMINISTRO_LR")


def test_a_source_can_be_registered_on_the_seeded_catalogue(
    session_factory: sessionmaker[Session],
) -> None:
    """El criterio de la fase 1: sin esto, `regwatch source add` no tiene donde colgar
    nada y el catalogo curado no puede existir."""
    with session_factory() as session, session.begin():
        family = find_family(session, "ES/FACTURAE")
        document_type = find_document_type(session, family, "FACTURAE_322")
        source = create_source(
            session,
            family=family,
            name="XSD Facturae 3.2.2",
            url="https://www.facturae.gob.es/x/Facturaev3_2_2.xml",
            source_kind="SCHEMA",
            collector_type="HTTP_FILE",
            check_frequency="0 6 * * *",
            priority="HOT",
            document_type=document_type,
        )
        assert source.regulation_family_id == family.id
        assert source.document_type_id == document_type.id
        # Vencida desde el alta: la primera pasada de `run-due` la recoge.
        assert source.next_check_at is None
        session.rollback()


def test_family_lookup_reports_what_is_wrong(session_factory: sessionmaker[Session]) -> None:
    """El mensaje tiene que decir por que no la encuentra, no repetir que no esta."""
    with session_factory() as session:
        # Minuscula y espacios: el operador teclea, no copia.
        assert find_family(session, " es/facturae ").code == "FACTURAE"

        with pytest.raises(UnknownFamilyError, match="JURISDICCION/FAMILIA"):
            find_family(session, "FACTURAE")

        # El caso frecuente: se teclea ES/TICKETBAI pensando en España, pero vive en las
        # forales. El error dice donde si esta.
        with pytest.raises(UnknownFamilyError, match=r"sino en ES-BI, ES-SS, ES-VI"):
            find_family(session, "ES/TICKETBAI")

        # Jurisdiccion inexistente: se listan las que hay.
        with pytest.raises(UnknownFamilyError, match=r"no existe la jurisdiccion PT"):
            find_family(session, "PT/FACTURAE")

        # Jurisdiccion correcta, familia inventada: se listan las suyas.
        with pytest.raises(UnknownFamilyError, match=r"Las que tiene:.*FACTURAE"):
            find_family(session, "ES/NO_EXISTE")


def test_the_three_plans_are_seeded_with_limits_in_data(
    session_factory: sessionmaker[Session],
) -> None:
    """Los limites viven en datos, nunca en codigo: debe ser posible cambiar el modelo de
    negocio editando registros, sin desplegar (apartado 4.5)."""
    with session_factory() as session:
        plans = {
            row.code: row
            for row in session.execute(
                text("SELECT code, price_monthly, price_yearly, limits, is_public FROM plan")
            ).all()
        }
        assert set(plans) == {"FREE", "PRO", "TEAM"}

        free = plans["FREE"]
        assert (free.price_monthly, free.price_yearly) == (0, 0)
        assert free.limits["max_jurisdictions"] == 1
        assert free.limits["webhooks_enabled"] is False
        assert free.limits["history_days"] == 30

        # Precio anual con dos meses de descuento.
        for code in ("PRO", "TEAM"):
            plan = plans[code]
            assert plan.price_yearly == plan.price_monthly * 10, code

        # Histórico completo en los planes de pago: sin límite, no un número grande.
        assert plans["PRO"].limits["history_days"] is None
        assert plans["TEAM"].limits["max_users"] is None
        assert plans["TEAM"].limits["api_access"] is True


def test_trial_config_exists_as_a_single_editable_row(
    session_factory: sessionmaker[Session],
) -> None:
    """Cambiar la prueba de 14 a 30 dias, o desactivarla, no puede requerir un despliegue
    (criterio de aceptacion 10)."""
    with session_factory() as session:
        rows = session.execute(
            text(
                "SELECT enabled, duration_days, trial_plan_code, requires_card, "
                "fallback_plan_code FROM trial_config"
            )
        ).all()
        assert len(rows) == 1
        (config,) = rows
        assert config.enabled is True
        assert config.duration_days == 14
        assert config.trial_plan_code == "PRO"
        assert config.requires_card is False
        assert config.fallback_plan_code == "FREE"

        # Los planes que nombra por codigo tienen que existir de verdad.
        for code in (config.trial_plan_code, config.fallback_plan_code):
            assert session.execute(
                text("SELECT 1 FROM plan WHERE code = :code"), {"code": code}
            ).scalar_one_or_none()


def test_seeded_plan_codes_match_the_subscription_state_machine(
    session_factory: sessionmaker[Session],
) -> None:
    """`TRIALING` es el estado inicial de una organizacion, y el plan de prueba y el de
    caida tienen que estar dados de alta antes de que nadie se registre."""
    with session_factory() as session:
        assert SubscriptionStatus.TRIALING.value == "TRIALING"
        codes = set(session.scalars(text("SELECT code FROM plan")).all())  # type: ignore[arg-type]
        assert {"PRO", "FREE"} <= codes
