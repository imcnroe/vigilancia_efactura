# Catálogo semilla de fuentes — verificación del 8 de septiembre de 2026

Cada fuente lleva su nivel de evidencia. **Solo el nivel A puede darse de alta sin
volver a comprobar.** Los niveles B y C se cargan con `is_active = false` hasta que
`source test` confirme la respuesta.

| Nivel | Significado |
|---|---|
| **A** | URL descargada en esta sesión; contenido inspeccionado |
| **B** | URL enlazada desde una página de nivel A, no descargada |
| **C** | URL citada por un tercero; el organismo no la ha confirmado |

---

## España — Facturae

### A · Índice de versiones Facturae
`https://www.facturae.gob.es/formato/ultima-version`
`collector_type: HTTP_HTML_INDEX` · `source_kind: INDEX` · `priority: WARM`

Página descargada íntegra. Enlaza los tres XSD vigentes, los historiales de versión
en PDF y la descripción de campos. Los enlaces cuelgan de `/content/dam/...`, ruta
estable de AEM.

### A · XSD Facturae 3.2.1
`https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_1.xml`
`collector_type: HTTP_FILE` · `source_kind: SCHEMA` · `document_type: FACTURAE_321`

Descargado y verificado. Contenido real: `<xs:schema ... version="3.2.1">`, con
`targetNamespace` que incluye la versión. Un `xs:import` externo a
`http://www.w3.org/TR/xmldsig-core/xmldsig-core-schema.xsd`. Contiene `xs:choice`,
tipos anónimos inline, `xs:enumeration`, `xs:pattern` y `xs:maxLength`: cubre casi
todo lo que el normalizador tiene que saber tratar.

Ojo con la extensión: el fichero es un XSD servido como `.xml` y con
`mime_type: application/xml`. El colector no puede deducir el tipo de la extensión.

### B · XSD Facturae 3.2 y 3.2.2
`.../versiones/Facturaev3_2.xml` y `.../versiones/Facturaev3_2_2.xml`
Enlazados desde la página de nivel A. Misma ruta y patrón que el 3.2.1.

### B · Historiales de versiones (PDF)
`.../versiones/HistorialVersiones_Facturae_32.pdf`, `_321.pdf`, `_322.pdf`
`source_kind: NARRATIVE`. Son el documento que el analista (7.4) necesita para citar
la fecha de entrada en vigor.

---

## España — Verifactu / RRSIF (AEAT)

### A · Índice técnico de esquemas (sede)
`https://sede.agenciatributaria.gob.es/Sede/iva/sistemas-informaticos-facturacion-verifactu/informacion-tecnica/esquemas.html`
`collector_type: HTTP_HTML_INDEX` · `priority: HOT`

Descargada. Al pie declara «Página actualizada: 26/marzo/2026», dato aprovechable
como `declared_version` para fuentes narrativas. El índice lateral enumera las diez
páginas de la sección técnica, todas candidatas a fuente propia.

### **Problema serio: el portal de desarrolladores bloquea robots**

La página anterior no aloja los XSD: redirige a
`https://www.agenciatributaria.es/AEAT.desarrolladores/...`, y **ese dominio devuelve
`ROBOTS_DISALLOWED`**. El apartado 7.1 obliga a respetar `robots.txt`, así que la
fuente española más importante del producto no es accesible con un colector conforme
por esa vía. Tres salidas, en orden de preferencia:

1. **Vigilar el índice de `sede.agenciatributaria.gob.es`** (sí accesible) y descargar
   los XSD del host de ficheros estáticos, no del portal de desarrolladores. Verifiqué
   que `https://prewww2.aeat.es/static_files/.../SuministroLR.xsd` responde y devuelve
   contenido. Falta confirmar el host de producción y su `robots.txt`.
2. **Pedir permiso a la AEAT** por escrito, identificando el `User-Agent`. Es un
   servicio legítimo de vigilancia normativa y la petición es razonable.
3. Descartado: ignorar el `robots.txt`. Que nos bloqueen mata el producto.

**Esto hay que resolverlo en la fase 1, no más tarde.** Condiciona el diseño del
colector y afecta a la familia normativa con más valor comercial.

### B · Resto de la sección técnica
Diseños de registro, WSDL, validaciones y errores, algoritmo del hash,
especificaciones de firma, características del QR. Todas bajo
`.../informacion-tecnica/*.html`, enlazadas desde la página de nivel A.

### B · FAQ para empresas de desarrollo (PDF)
`https://sede.agenciatributaria.gob.es/static_files/AEAT_Desarrolladores/EEDD/IVA/VERI-FACTU/FAQs-Desarrolladores.pdf`
Enlazado desde la página de nivel A, con fecha de actualización visible (04-12-2025).
Host `static_files`, distinto del portal bloqueado.

---

## España — TicketBAI (tres jurisdicciones)

### B · Gipuzkoa — documentación y normativa
`https://www.gipuzkoa.eus/es/web/ogasuna/ticketbai/documentacion-y-normativa`
Publica los XSD de envío y anulación en ZIP, más listados de validaciones y errores
en PDF con fecha de actualización explícita.

### B · Bizkaia — Batuz, documentación técnica
`https://www.batuz.eus/es/documentacion-tecnica`
Esquemas del LROE en ZIP y especificaciones de firma. Batuz es un subsistema propio de
Bizkaia: confirma que las tres diputaciones no comparten superficie técnica.

### C · Álava — documentación técnica
`web.araba.eus/es/hacienda/ticketbai/documentacion-tecnica`
Citada por un tercero, sin esquema en la cita y sin confirmar. **`TODO_VERIFICAR`.**

---

## Francia — DGFiP / AIFE

### B · Spécifications externes B2B
`https://www.impots.gouv.fr/specifications-externes-b2b`
`collector_type: HTTP_HTML_INDEX` · `priority: HOT`

Publica el paquete completo en ZIP: documento de especificaciones, anexos, ejemplos,
**XSD y swaggers**. Versión vigente 3.2, de 30/04/2026. La página **conserva las
versiones anteriores**, lo que da histórico desde el primer día.

Implicación para el colector: la unidad descargable es un ZIP con varios artefactos
dentro. El `artifact` guarda el ZIP íntegro (es lo que publica el organismo), y el
normalizador extrae y normaliza cada XSD por separado. Hay que decidir si cada XSD
interno genera su propio `normalized_form` colgando del mismo artefacto — creo que sí,
con el `path` dentro del ZIP como parte de la clave.

**Contexto de calendario:** las primeras obligaciones de la reforma francesa entraron
en vigor el 1 de septiembre de 2026, hace una semana. La actividad normativa está en
su punto más alto justo ahora.

---

## Los dos hallazgos que importan

### 1. El problema de los fixtures está resuelto

Facturae publica **3.2, 3.2.1 y 3.2.2 simultáneamente en la misma página oficial**.
Son tres versiones reales y consecutivas del mismo esquema, descargables hoy. Además,
el organismo publica el listado de cambios de 3.2.1 a 3.2.2: campos nuevos de cesión
de factoring, `InvoiceIssueDate` para rectificativas, `InvoiceDescription`, el bloque
de pago en especie, y dos altas en enumerados (KWh como unidad de medida, HTML como
formato de adjunto admitido).

Eso es un **oráculo**: el detector se ejecuta sobre 3.2.1 → 3.2.2 y su salida se
contrasta contra una lista publicada por el propio organismo. No es un test sintético
donde nosotros escribimos la pregunta y la respuesta.

Cubre `FIELD_ADDED_OPTIONAL` y `ENUM_VALUE_ADDED` con datos reales. Los siete tipos
restantes siguen necesitando pares sintéticos.

### 2. La tesis del producto queda verificada contra un caso real

Dos hechos encontrados en la vigilancia de hoy:

**Gipuzkoa publicó un XSD de alta mejorado manteniendo la versión interna en 1.2.** El
propio aviso dice que quien no necesite las mejoras no tiene que cambiar nada. Es
decir: **el esquema cambió y el número de versión no.** Cualquier vigilancia basada en
comparar versiones declaradas no habría visto nada. El SHA-256 del apartado 7.1 sí lo
ve. Esto es exactamente el argumento de venta, y ya ha ocurrido de verdad.

**El cambio de TicketBAI 1.1 a 1.2** es un caso de prueba redondo para el clasificador:
se añadió el campo Dirección en Destinatarios —**obligatorio en Gipuzkoa y opcional en
las otras dos haciendas**—, el Código Postal se amplió a alfanumérico de 20, y el
importe unitario de línea pasó a ocho decimales.

El primero de los tres es el hallazgo interesante: **el mismo campo del mismo esquema
tiene severidad distinta según la jurisdicción**. `BLOCKING` en Gipuzkoa, `INFO` en
Álava y Bizkaia. El apartado 7.3 asume una severidad por cambio, no por jurisdicción.
Si dos haciendas comparten esquema pero no obligatoriedad, el modelo de datos necesita
o bien tres `source` distintas apuntando al mismo fichero, o bien severidad por
jurisdicción dentro del `structural_diff`. Hay que decidirlo antes de escribir el
detector.

---

---

## Verificaciones del 19 de septiembre de 2026

Cuatro URLs suben de nivel. Todas se comprobaron con el colector del proyecto, que
respeta `robots.txt`, no con un navegador.

| URL | Antes | Ahora | Respuesta |
|---|---|---|---|
| `.../versiones/Facturaev3_2.xml`, `_3_2_1`, `_3_2_2` | A / B | **A** | `200`, SHA-256 congelado en `manifest.json` |
| `.../versiones/HistorialVersiones_Facturae_322.pdf` | B | **A** | `200 application/pdf`, 430 KB |
| `sede.../informacion-tecnica/esquemas.html` | A | **A**, capturada | `200`, 9 entradas normalizadas |
| `sede.../static_files/.../FAQs-Desarrolladores.pdf` | B | **A** | `200 application/pdf`, 692 KB |

**El host `static_files` de la sede sí es accesible.** Es la salida número 1 de las tres
que planteaba el problema del `robots.txt`, y queda confirmada: el índice técnico de la
sede se vigila y los ficheros se bajan de `sede.agenciatributaria.gob.es`, no del portal
de desarrolladores bloqueado.

### Un hallazgo del propio índice de Facturae

El enlace al historial de versiones 3.2.2 lleva por texto
`HistorialVersiones_Facturae_321`, mientras que el fichero que descarga es
`HistorialVersiones_Facturae_322.pdf`. Es un error de etiquetado del organismo, no del
colector, y es la clase de detalle que justifica guardar el texto del enlace además de la
URL: quien redacte la ficha necesita saber que la página miente.

---

## Qué falta

- Confirmar la URL de Álava.
- Pedir acceso por escrito al portal de desarrolladores de la AEAT. La vía de la sede
  funciona, pero depender de una sola es frágil.
- Descargar los ZIP franceses para congelarlos como fixtures, con su hash anotado. Los
  tres XSD de Facturae ya lo están.
- Afinar el `version_pattern` del índice de Facturae: los historiales se llaman `_321.pdf`
  y el patrón actual, pensado para `v3_2_1`, los deja sin versión.
