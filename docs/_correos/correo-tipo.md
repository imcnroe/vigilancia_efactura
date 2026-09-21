# Correo tipo para avisar a un organismo

Se envía **antes** de que el cron empiece a correr solo, no después. Un aviso previo
convierte un bot desconocido en una petición razonable a la que alguien puede decir que
no; un aviso posterior es una disculpa.

Rellena lo que va entre `<>`. Las cifras son las de hoy: si el catálogo crece, actualiza
el número de peticiones antes de enviarlo, porque es el dato que sostiene todo lo demás.

Sugerencias de redacción, por si tienes que retocarlo:

- **Corto.** Quien lo lee gestiona una infraestructura y recibe decenas de correos.
  Números concretos, frases cortas, sin marketing.
- **La pregunta al final, y que sea fácil decir que no.** No estás pidiendo permiso para
  algo dudoso: estás avisando de algo que ya es legítimo y ofreciendo pararlo.
- **No adjuntes nada.** Un adjunto de un desconocido va a la papelera.

---

## Versión base

> **Asunto:** Aviso previo: programa de consulta automática de documentación técnica
> pública (proyecto personal de I+D)

Buenos días:

Les escribo para avisarles de una actividad automatizada contra sus servidores, antes de
ponerla en marcha de forma regular, por si prefieren que no se haga o que se haga de otra
manera.

Soy `<nombre y apellidos>` y mantengo un proyecto personal de I+D, sin ánimo de lucro y
sin servicio publicado, que vigila cuándo cambian las especificaciones técnicas de
facturación electrónica. El programa comprueba una vez al día si han cambiado unos
documentos públicos concretos y guarda una copia cuando cambian.

Los datos concretos:

- **Documentos consultados:** `<enumerar las URL exactas, una por línea>`
- **Frecuencia:** una comprobación diaria de cada uno.
- **Volumen total:** `<n>` peticiones al día a sus servidores.
- **Identificación:** todas las peticiones llevan la cabecera
  `User-Agent: <cadena exacta del User-Agent>`, cuya URL explica qué es el programa y
  cómo pararlo.

Sobre el comportamiento del programa: respeta `robots.txt` y no solicita ninguna ruta
excluida; usa peticiones condicionales (`If-None-Match` / `If-Modified-Since`), de modo
que si el documento no ha cambiado la respuesta es un `304` y no se transfiere contenido;
tiene timeout y reintentos con retroceso exponencial; no rellena formularios, no accede a
áreas autenticadas y no trata datos personales. Sólo consulta las URL que he dado de alta
a mano tras comprobarlas: no hay rastreo ni recorrido automático de enlaces.

Si prefieren que no lo haga, que lo haga con otra frecuencia o a través de otro canal,
respondan a este correo y lo ajusto o lo paro. Si no reciben respuesta mía a una petición
suya, asuman que es un fallo del correo y no una negativa: `<teléfono o segundo contacto,
opcional>`.

Gracias por el trabajo de publicar esta documentación de forma abierta, que es lo que
hace posible el proyecto.

Un saludo,
`<nombre y apellidos>`
`<correo de contacto>`
`<URL del repositorio>`

---

## Variante para la AEAT

Igual que la base, pero añadiendo la petición concreta. El portal de desarrolladores
(`www.agenciatributaria.es/AEAT.desarrolladores/...`) excluye a los agentes automáticos en
su `robots.txt`, así que el programa **no lo consulta**; hoy se vigila el índice técnico de
la sede, que sí es accesible. Merece la pena preguntar por la vía buena en vez de dar por
buena la que hemos encontrado nosotros.

Inserta esto antes de la despedida:

> Aprovecho para una consulta. El portal de desarrolladores
> (`www.agenciatributaria.es/AEAT.desarrolladores/`) excluye a los agentes automáticos en
> su `robots.txt`, por lo que el programa **no lo consulta en ningún caso**. Para obtener
> los esquemas consulto el índice técnico de la sede electrónica
> (`sede.agenciatributaria.gob.es/.../informacion-tecnica/esquemas.html`) y los ficheros
> alojados en `sede.agenciatributaria.gob.es/static_files/`, que sí responden a un agente
> que respeta `robots.txt`.
>
> ¿Es ésa la vía correcta para consultar los esquemas de forma programada, o existe otra
> que prefieran —un canal de publicación, una suscripción o un repositorio— que les
> suponga menos carga? Si la hay, la uso y dejo de consultar las páginas.

---

## Variante para una diputación foral (TicketBAI / Batuz)

Igual que la base. Merece la pena añadir el motivo por el que se vigilan las tres
haciendas por separado, porque es una pregunta que probablemente se hagan:

> Consulto la documentación técnica de las tres diputaciones por separado porque el mismo
> campo del mismo esquema no siempre tiene la misma obligatoriedad en las tres, y quería
> preguntarles si los ficheros que publican son los mismos que los de las otras
> diputaciones o difieren.
