# Trabajar en Windows

El proyecto es multiplataforma, pero hay tres detalles que conviene conocer.

## `make` no viene en Windows

El `Makefile` es un atajo, no un requisito. Los comandos equivalentes:

```powershell
pip install -e ".[dev]"          # make install
docker compose up -d             # make up
alembic upgrade head             # make migrate
pytest -q                        # make test
ruff check src tests scripts     # make lint
mypy
```

Si prefieres los atajos, `winget install GnuWin32.Make` o trabajar dentro de WSL.

## Docker Desktop

`docker compose up -d` levanta PostgreSQL 16 y MinIO. Necesita Docker Desktop en
marcha. Alternativa sin Docker: instalar PostgreSQL nativo y apuntar `DATABASE_URL` a
él; los tests de integración funcionan igual, y los unitarios no necesitan base.

## Finales de línea

`.gitattributes` fuerza LF en todo el código. Los XSD de `tests/fixtures/xsd/` van
marcados como binarios a propósito: son artefactos descargados de organismos oficiales
y cualquier alteración cambia su SHA-256, que es justamente lo que el manifiesto
verifica.

## Variables de entorno

La CLI (`regwatch`) y Alembic leen el `.env` del directorio actual automáticamente.
Una variable definida en la sesión manda sobre el `.env`. En PowerShell:

```powershell
$env:DATABASE_URL = "postgresql+psycopg://regwatch:regwatch@localhost:5432/regwatch"
```

`pytest` no lee el `.env`: los tests de integración solo miran `DATABASE_URL` en el
entorno y, si no está, usan la URL del `docker-compose.yml`.

## Docker Desktop no arranca solo

`docker compose up -d` falla con `open //./pipe/dockerDesktopLinuxEngine` si Docker
Desktop no está en marcha. Hay que abrirlo antes (o `Start-Process "C:\Program
Files\Docker\Docker\Docker Desktop.exe"`) y esperar a que el icono deje de animarse.
Si la descarga de imágenes corta con `tls: bad record MAC`, es la red: repetir
`docker pull postgres:16` hasta que termine.

## Python

El proyecto pide Python 3.12 o superior y está probado con 3.14. Crear el entorno con
`py -3.12 -m venv .venv` si está instalado, o `python -m venv .venv` con el que haya.
