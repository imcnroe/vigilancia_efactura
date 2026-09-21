# A quién enviarlo — verificado el 19 de septiembre de 2026

**Ninguna dirección de esta lista está inventada.** Cada una lleva su nivel de evidencia,
con el mismo criterio que el [catálogo de fuentes](../catalogo-fuentes-semilla.md), porque
un correo enviado a una dirección plausible pero falsa no rebota: lo lee quien no debe, o
nadie, y te quedas creyendo que avisaste.

| Nivel | Significado |
|---|---|
| **A** | Leída en una página oficial del propio organismo, en esta sesión |
| **B** | Publicada por el organismo en otro sitio, no comprobada hoy |
| **C** | Citada por un tercero; el organismo no la ha confirmado. **No enviar sin comprobar** |

---

## Prioridad 1 — los dos organismos a los que ya se les pide algo

### AEAT — `sede.agenciatributaria.gob.es`

**A · `atenusu@correo.aeat.es`**
Buzón de incidencias informáticas de la Sede Electrónica. La propia página lo acota:
«Solo para PROBLEMAS INFORMÁTICOS».
Fuente: [Comprobaciones básicas y forma de contacto](https://sede.agenciatributaria.gob.es/Sede/ayuda/consultas-informaticas/informacion-general-sobre-presentacion-internet-tecnica/comprobaciones-basicas-forma-contacto.html)

Es el canal que existe con seguridad, pero **no es exactamente el destinatario ideal**: su
cometido son incidencias, y esto no lo es. Aun así, un aviso educado en el buzón técnico
es mejor que ningún aviso, y desde ahí suelen reencaminar.

La misma página avisa de tres cosas que conviene tener en cuenta: el correo no garantiza
la identidad del remitente, no es un medio seguro para información confidencial y no
garantiza la entrega. Para este aviso ninguna importa —no se manda nada confidencial—,
pero por eso el correo tipo incluye un segundo contacto opcional.

**C · `verifactu@correo.aeat.es`**
Circula por blogs de terceros como buzón de consultas técnicas de VERI\*FACTU, con un
plazo de respuesta de dos días. **No lo he encontrado en ninguna página oficial.**
Si existe, es el destinatario correcto; si no, el correo se pierde. Antes de usarlo,
búscalo en el portal de desarrolladores o pregunta por él en el mensaje a `atenusu`.

**No usar: `aducat@correo.aeat.es`.** Es de aplicaciones de comercio exterior, nada que
ver.

---

### Facturae — `www.facturae.gob.es`

**Sin dirección localizada.** La página de contacto que devuelven los buscadores
(`/paginas/contacto.aspx`) responde **404**, igual que `/contacto`. El portal parece haber
migrado a rutas limpias y la de contacto no la he encontrado.

Qué hacer: abre [www.facturae.gob.es](https://www.facturae.gob.es/) y busca el enlace de
contacto en el pie. Cuando lo tengas, pásamelo y lo añado con nivel A.

**C · `soporteface@red.es`** — probablemente **el destinatario equivocado**. Es el soporte
de FACe (el punto de entrada de facturas), y el propio FACe declara que no atiende
cuestiones del formato Facturae ni de generación de facturas. Lo dejo anotado para que no
lo uses por descarte.

Dicho esto: Facturae publica sus esquemas en abierto y sin restricción en `robots.txt`, y
el volumen es de 5 peticiones diarias. Es el organismo donde el aviso es menos urgente.

---

## Prioridad 2 — todavía no se les pide nada

Estos entran en el catálogo más adelante. No hace falta escribirles hasta que haya una
fuente activa apuntando a ellos.

| Organismo | Familia | Contacto |
|---|---|---|
| Diputación Foral de Gipuzkoa | TicketBAI | Sin localizar. Está en `gipuzkoa.eus/.../ticketbai` |
| Diputación Foral de Bizkaia | Batuz / LROE | Sin localizar. Está en `batuz.eus` |
| Diputación Foral de Álava | TicketBAI | Sin localizar, y **la propia URL de la documentación técnica está sin verificar** |
| DGFiP / AIFE (Francia) | Facturación B2B | Sin localizar. Está en `impots.gouv.fr` |

---

## Antes de enviar cualquiera de ellos

1. **Que el `User-Agent` sea el definitivo.** El correo declara la cadena exacta; si luego
   cambia, el aviso deja de servir para identificarte.
2. **Que la URL del `User-Agent` responda** y lleve a una página que explique el proyecto.
   El [README](../../README.md) tiene la sección escrita para eso; lo que falta es
   publicarlo y que la URL exista.
3. **Que el número de peticiones del correo sea el real.** Hoy son 7 al día: 5 a
   `facturae.gob.es` y 2 a `sede.agenciatributaria.gob.es`.
4. **Guarda la respuesta.** Si alguna vez hay una queja, la conversación previa es la
   diferencia entre un malentendido y un incidente.
