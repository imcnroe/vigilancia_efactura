"""Colector `HTTP_HTML_INDEX`: vigila una pagina de listado de publicaciones.

**Captura la pagina, no lo que la pagina enlaza.** Es la decision de diseno de este
colector y conviene justificarla, porque la alternativa —bajar de una pasada todos los
ficheros enlazados— parece mas util y no lo es:

1. **La linea base del diff es por fuente.** Si una sola fuente guardase el XSD 3.2, el
   3.2.1, el 3.2.2 y tres PDF, el detector compararia cada artefacto nuevo con el
   anterior de *esa fuente*, que es otro documento distinto. Saldria un diff enorme
   entre dos esquemas que no tienen nada que ver.
2. **Las `source` son el producto curado.** Dar de alta una fuente a partir de un enlace
   raspado es justo lo que el apartado 0 prohibe: una URL plausible que nadie ha
   comprobado. Descubrir no es capturar.
3. **Cortesia.** Una pasada diaria es 1 peticion, no 30.

El valor esta en el normalizador `INDEX_ENTRIES`, que reduce la pagina a su lista de
entradas: asi el detector dice «hay una publicacion nueva, apunta aqui» en vez de «la
pagina ha cambiado», que es lo unico que se puede decir vigilando su SHA-256 y que
seria verdad todos los dias por el banner de turno.

Cuando aparece una entrada nueva, el `change_event` la nombra con su URL y un operador
decide si merece una `source` propia. Esa decision es humana a proposito.
"""

from __future__ import annotations

from regwatch.ingest.collectors.http_file import HTTPFileCollector


class HTTPHtmlIndexCollector(HTTPFileCollector):
    """Descarga la pagina de indice. Hereda `robots.txt`, reintentos y condicionales.

    No redefine nada: bajar una pagina HTML es bajar un fichero, y duplicar la maquinaria
    HTTP significaria tener dos formas distintas de salir a la red. Lo que la distingue
    de `HTTP_FILE` es el `collector_type`, y ese nombre hace dos cosas: elige esta clase
    en el registro y deja escrito en el catalogo que la fuente es un indice, que es lo
    que el operador necesita ver en `source list`.
    """

    collector_type = "HTTP_HTML_INDEX"
