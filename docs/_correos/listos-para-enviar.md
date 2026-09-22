# Listos para copiar y pegar — 22 de septiembre de 2026

Los datos ya están puestos: nombre, correo, repositorio, cadena exacta del `User-Agent`,
volumen **por organismo** (no el total: a la AEAT son 2 peticiones al día, no 7) y las URL
concretas que se consultan en cada uno.

Si cambias algo del `User-Agent` o del catálogo, estos textos dejan de ser ciertos. Lo que
declaran sale de [`destinatarios.md`](destinatarios.md), que es donde hay que actualizarlo.

---

## 1 · AEAT — listo para enviar

**Para:** `atenusu@correo.aeat.es`
**Asunto:** Aviso previo: consulta automática de documentación técnica pública (proyecto personal de I+D)

> Buenos días:
>
> Les escribo para avisarles de una actividad automatizada contra sus servidores, antes de
> ponerla en marcha de forma regular, por si prefieren que no se haga o que se haga de otra
> manera.
>
> Soy Ismael Álvarez y mantengo un proyecto personal de I+D, sin ánimo de lucro y sin
> servicio publicado, que vigila cuándo cambian las especificaciones técnicas de
> facturación electrónica. El programa comprueba una vez al día si han cambiado unos
> documentos públicos concretos y guarda una copia cuando cambian.
>
> Los datos concretos:
>
> - **Documentos consultados**, los dos únicos:
>   `https://sede.agenciatributaria.gob.es/Sede/iva/sistemas-informaticos-facturacion-verifactu/informacion-tecnica/esquemas.html`
>   `https://sede.agenciatributaria.gob.es/static_files/AEAT_Desarrolladores/EEDD/IVA/VERI-FACTU/FAQs-Desarrolladores.pdf`
> - **Frecuencia:** una comprobación diaria de cada uno.
> - **Volumen total:** 2 peticiones al día a sus servidores.
> - **Identificación:** todas las peticiones llevan la cabecera
>   `User-Agent: regwatch/0.1 (proyecto de I+D personal; +https://github.com/imcnroe/vigilancia_efactura; imcnroe@hotmail.com)`,
>   cuya URL lleva a una página que explica qué es el programa y cómo pararlo.
>
> Sobre el comportamiento del programa: respeta `robots.txt` y no solicita ninguna ruta
> excluida; usa peticiones condicionales (`If-None-Match` / `If-Modified-Since`), de modo
> que si el documento no ha cambiado la respuesta es un `304` y no se transfiere contenido;
> tiene timeout y reintentos con retroceso exponencial; no rellena formularios, no accede a
> áreas autenticadas y no trata datos personales. Sólo consulta las dos URL anteriores, que
> he dado de alta a mano tras comprobarlas: no hay rastreo ni recorrido automático de
> enlaces.
>
> Aprovecho para una consulta. El portal de desarrolladores
> (`www.agenciatributaria.es/AEAT.desarrolladores/`) excluye a los agentes automáticos en su
> `robots.txt`, por lo que el programa **no lo consulta en ningún caso**. Para obtener los
> esquemas consulto el índice técnico de la sede electrónica y los ficheros alojados en
> `sede.agenciatributaria.gob.es/static_files/`, que sí responden a un agente que respeta
> `robots.txt`.
>
> ¿Es ésa la vía correcta para consultar los esquemas de forma programada, o existe otra que
> prefieran —un canal de publicación, una suscripción o un repositorio— que les suponga
> menos carga? Si la hay, la uso y dejo de consultar las páginas.
>
> Si prefieren que no lo haga, que lo haga con otra frecuencia o a través de otro canal,
> respondan a este correo y lo ajusto o lo paro.
>
> Si esta consulta corresponde a otro departamento —he visto citado un buzón
> `verifactu@correo.aeat.es` que no he podido confirmar en su web—, les agradecería que me
> indicaran a quién dirigirla.
>
> Gracias por el trabajo de publicar esta documentación de forma abierta, que es lo que hace
> posible el proyecto.
>
> Un saludo,
> Ismael Álvarez
> imcnroe@hotmail.com
> https://github.com/imcnroe/vigilancia_efactura

La última pregunta mata dos pájaros: si `verifactu@correo.aeat.es` existe, te lo confirman
ellos y deja de ser una dirección de nivel C sacada de un blog.

---

## 2 · Facturae — listo salvo el destinatario

**Para:** ⚠️ **sin localizar.** Ver [`destinatarios.md`](destinatarios.md): la página de
contacto del portal devuelve 404 en las dos rutas que probamos.
**Asunto:** Aviso previo: consulta automática de documentación técnica pública (proyecto personal de I+D)

> Buenos días:
>
> Les escribo para avisarles de una actividad automatizada contra sus servidores, antes de
> ponerla en marcha de forma regular, por si prefieren que no se haga o que se haga de otra
> manera.
>
> Soy Ismael Álvarez y mantengo un proyecto personal de I+D, sin ánimo de lucro y sin
> servicio publicado, que vigila cuándo cambian las especificaciones técnicas de
> facturación electrónica. El programa comprueba una vez al día si han cambiado unos
> documentos públicos concretos y guarda una copia cuando cambian.
>
> Los datos concretos:
>
> - **Documentos consultados**, los cinco únicos:
>   `https://www.facturae.gob.es/formato/ultima-version`
>   `https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2.xml`
>   `https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_1.xml`
>   `https://www.facturae.gob.es/content/dam/facturae/formato/versiones/Facturaev3_2_2.xml`
>   `https://www.facturae.gob.es/content/dam/facturae/formato/versiones/HistorialVersiones_Facturae_322.pdf`
> - **Frecuencia:** una comprobación diaria de cada uno.
> - **Volumen total:** 5 peticiones al día a sus servidores.
> - **Identificación:** todas las peticiones llevan la cabecera
>   `User-Agent: regwatch/0.1 (proyecto de I+D personal; +https://github.com/imcnroe/vigilancia_efactura; imcnroe@hotmail.com)`,
>   cuya URL lleva a una página que explica qué es el programa y cómo pararlo.
>
> Sobre el comportamiento del programa: respeta `robots.txt` y no solicita ninguna ruta
> excluida; usa peticiones condicionales (`If-None-Match` / `If-Modified-Since`), de modo
> que si el documento no ha cambiado la respuesta es un `304` y no se transfiere contenido;
> tiene timeout y reintentos con retroceso exponencial; no rellena formularios, no accede a
> áreas autenticadas y no trata datos personales. Sólo consulta las cinco URL anteriores,
> que he dado de alta a mano tras comprobarlas: no hay rastreo ni recorrido automático de
> enlaces.
>
> Si prefieren que no lo haga, que lo haga con otra frecuencia o a través de otro canal,
> respondan a este correo y lo ajusto o lo paro.
>
> Aprovecho para señalarles una errata, por si les es útil: en la página de versiones del
> formato, el enlace que descarga `HistorialVersiones_Facturae_322.pdf` aparece rotulado
> como «HistorialVersiones_Facturae_321».
>
> Gracias por el trabajo de publicar esta documentación de forma abierta, que es lo que hace
> posible el proyecto.
>
> Un saludo,
> Ismael Álvarez
> imcnroe@hotmail.com
> https://github.com/imcnroe/vigilancia_efactura

Lo de la errata no es relleno: demuestra que alguien está leyendo de verdad lo que
publican, y es lo que separa este correo de un aviso automático.
