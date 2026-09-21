"""Configuracion por variables de entorno.

Los secretos no van al repositorio (apartado 11): todo sale del entorno o de un `.env`
local que esta en `.gitignore`. Se valida al construir el objeto, de modo que una
variable mal escrita falla en el primer segundo y no a las tres de la manana dentro de
un job de cron.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

#: Marca que dejan `.env.example` y el codigo en los valores que hay que rellenar a
#: mano. Mientras siga en el User-Agent, el servicio se niega a salir a la red.
PLACEHOLDER: str = "TODO_VERIFICAR"

#: Formas de contacto que se aceptan en el `User-Agent`. Basta con una.
_CONTACT_URL = re.compile(r"https?://\S+\.\S+")
_CONTACT_EMAIL = re.compile(r"[^\s(<]+@[^\s)>]+\.[A-Za-z]{2,}")


class Settings(BaseSettings):
    """Valores de configuracion. Los nombres coinciden con `.env.example`."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+psycopg://regwatch:regwatch@localhost:5432/regwatch"

    #: `s3` es el almacen de verdad (MinIO en local). `local` escribe en disco y sirve
    #: para trabajar sin contenedores; no es una opcion de produccion.
    artifact_store: Literal["s3", "local"] = "s3"
    artifact_local_root: Path = Path("var/artifacts")

    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "regwatch-artifacts"
    s3_region: str = "us-east-1"

    collector_user_agent: str = f"regwatch/0.1 (+https://{PLACEHOLDER}; contacto@{PLACEHOLDER})"
    collector_timeout_seconds: float = 30.0
    collector_max_attempts: int = 3

    #: Permite descargar con un `User-Agent` sin datos de contacto. **Solo para
    #: descargas manuales y supervisadas durante el desarrollo.** La exigencia del
    #: apartado 7.1 existe por la vigilancia diaria desatendida: un cron que baja
    #: ficheros cada manana sin que nadie mire es lo que alguien puede querer parar, y
    #: para eso tiene que saber a quien escribir. Bajar un fichero una vez, mirando, es
    #: indistinguible de abrirlo en el navegador.
    #:
    #: No sirve para saltarse el `TODO_VERIFICAR`: eso significa "sin rellenar", no
    #: "decidido que no hace falta". Y cada uso deja un aviso en el log.
    collector_allow_anonymous: bool = False

    #: Fallos consecutivos a partir de los cuales se abre una incidencia (apartado 7.1).
    heartbeat_failure_threshold: int = 2

    log_level: str = "INFO"

    def user_agent_problem(self) -> str | None:
        """Motivo por el que este `User-Agent` no sirve para salir a la red, o nulo.

        El apartado 7.1 pide un `User-Agent` **identificable**, no solo distinto del
        valor de ejemplo. Comprobar que no queda el `TODO_VERIFICAR` no basta:
        `regwatch/0.1` lo pasa y sigue siendo un agente anonimo, y quien lo vea en los
        logs de la AEAT no tiene a quien escribir para pedir que pare. Que nos bloqueen
        mata el producto, asi que la exigencia es que haya forma de contactar.
        """
        agent = self.collector_user_agent.strip()
        if not agent:
            return "COLLECTOR_USER_AGENT esta vacio."
        if PLACEHOLDER in agent:
            return (
                f"COLLECTOR_USER_AGENT sigue con {PLACEHOLDER}: hay que poner la URL de "
                f"contacto y el correo de verdad."
            )
        if not _CONTACT_URL.search(agent) and not _CONTACT_EMAIL.search(agent):
            if self.collector_allow_anonymous:
                return None
            return (
                f"COLLECTOR_USER_AGENT no identifica a nadie: {agent!r} no lleva URL de "
                f"contacto ni correo. El apartado 7.1 exige un agente identificable y "
                f"honesto. Formato habitual:\n"
                f"  regwatch/0.1 (+https://tu-dominio/tu-pagina-del-bot; buzon@tu-dominio)\n"
                f"Para una descarga manual y supervisada durante el desarrollo, "
                f"COLLECTOR_ALLOW_ANONYMOUS=true."
            )
        return None

    def user_agent_is_anonymous(self) -> bool:
        """Cierto si se va a salir a la red sin forma de contacto, por la bandera."""
        agent = self.collector_user_agent
        return not _CONTACT_URL.search(agent) and not _CONTACT_EMAIL.search(agent)
