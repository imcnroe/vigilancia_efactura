#!/usr/bin/env python3
"""Descarga los XSD reales de Facturae y los congela como fixtures.

Las tres versiones estan publicadas simultaneamente en la misma pagina oficial, lo que
las convierte en el mejor par de prueba que vamos a encontrar: son reales, son
consecutivas, y el propio organismo publica el listado de cambios entre 3.2.1 y 3.2.2.
El detector se puede contrastar contra ese listado en vez de contra un oraculo que
hayamos escrito nosotros.

Los ficheros no van al repositorio por su tamano; el hash si. Si un hash deja de
coincidir es que el organismo ha resustituido un fichero sin cambiar la version, que es
exactamente el suceso que este producto existe para detectar.

**Sale a la red por el mismo camino que el pipeline**, con `HTTPFileCollector`, y no con
un cliente propio. Asi hereda `robots.txt`, el `User-Agent` identificable, el timeout y
los reintentos. Un script de desarrollo que descargue de un organismo publico por su
cuenta es un script que algun dia lo hace de una forma que no habriamos permitido en
produccion.

Uso:
    python scripts/fetch_fixtures.py            # descarga y verifica
    python scripts/fetch_fixtures.py --update   # reescribe los hashes esperados
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from regwatch.core.settings import Settings
from regwatch.ingest.collectors.base import CollectorError, FetchResult, NotModified
from regwatch.ingest.collectors.http_file import HTTPFileCollector

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "xsd"
MANIFEST = FIXTURES / "manifest.json"

#: Verificadas el 8 de septiembre de 2026 contra www.facturae.gob.es/formato/ultima-version.
#: Ojo: son XSD servidos con extension .xml. La extension no dice el formato.
SOURCES = {
    "facturae_3_2.xsd": "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2.xml",
    "facturae_3_2_1.xsd": "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_1.xml",
    "facturae_3_2_2.xsd": "https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_2.xml",
}


def download(collector: HTTPFileCollector, url: str) -> FetchResult:
    """Una descarga. `NotModified` no puede darse: no se envian validadores."""
    output = collector.fetch(url, {})
    if isinstance(output, NotModified):
        raise CollectorError(f"{url} respondio 304 sin enviarle validadores")
    if not output:
        raise CollectorError(f"{url} no devolvio ningun fichero")
    return output[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--update", action="store_true", help="reescribe los hashes esperados")
    args = parser.parse_args()

    # El mismo `User-Agent` que los colectores, leido del entorno o del `.env`: tener dos
    # sitios donde mantenerlo garantiza que uno de los dos se queda sin rellenar.
    settings = Settings()
    if problem := settings.user_agent_problem():
        print(problem, file=sys.stderr)
        return 2

    FIXTURES.mkdir(parents=True, exist_ok=True)
    expected: dict[str, str] = {}
    if MANIFEST.is_file():
        expected = json.loads(MANIFEST.read_text(encoding="utf-8"))

    actual: dict[str, str] = {}
    failures: list[str] = []

    collector = HTTPFileCollector(
        user_agent=settings.collector_user_agent,
        timeout_seconds=max(settings.collector_timeout_seconds, 60.0),
        max_attempts=settings.collector_max_attempts,
    )
    try:
        for filename, url in SOURCES.items():
            print(f"descargando {filename} ...", end=" ", flush=True)
            try:
                fetched = download(collector, url)
            except CollectorError as error:
                print("FALLO")
                print(f"  {error}", file=sys.stderr)
                return 1

            (FIXTURES / filename).write_bytes(fetched.content)
            digest = fetched.content_hash
            actual[filename] = digest
            print(f"{fetched.size_bytes:>7} bytes  sha256:{digest[:16]}")

            previous = expected.get(filename)
            if previous and previous != digest and not args.update:
                failures.append(
                    f"{filename}: el organismo ha cambiado el fichero\n"
                    f"    esperado {previous}\n"
                    f"    obtenido {digest}"
                )
    finally:
        collector.close()

    if args.update or not expected:
        MANIFEST.write_text(json.dumps(actual, indent=2) + "\n", encoding="utf-8")
        print(f"\nmanifiesto escrito en {MANIFEST}")

    if failures:
        print("\nCAMBIOS DETECTADOS EN LAS FUENTES:", file=sys.stderr)
        for failure in failures:
            print(f"  {failure}", file=sys.stderr)
        print(
            "\nEsto no es un error del script. Es el producto funcionando: revisa que "
            "ha cambiado y actualiza con --update.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
