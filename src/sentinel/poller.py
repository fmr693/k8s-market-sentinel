"""El poller intradía: el primer proceso de LARGA VIDA del proyecto (fase 4).

Anatomía (decisión 4.2), pieza a pieza:

  - CRASH-ONLY: al arrancar hace gap-fill (recupera lo perdido), así que morir
    es barato y el camino de recuperación ES el camino de arranque normal.
    Errores por tick → log y a esperar el siguiente; errores persistentes →
    crashear sin vergüenza y que K8s reinicie con su backoff.
  - SUEÑO INTERRUMPIBLE: jamás time.sleep(); siempre stop.wait(n) sobre un
    threading.Event que el manejador de SIGTERM activa. Da igual que sea
    sábado a las 3 AM: la señal lo despierta y muere limpio en milisegundos,
    dentro de los ~30s de gracia de K8s.
  - SIESTAS CON LATIDO: cerrado el mercado, duerme hasta next_open() pero en
    tramos de ≤15 min, tocando el fichero-latido en cada vuelta. Un proceso
    mudo 65h es indistinguible de uno colgado; el latido alimenta la liveness
    probe (si el bucle se cuelga, el fichero envejece → K8s reinicia: el
    zombi se cura solo).
  - TICK POR RELOJ DE PARED: el objetivo del próximo tick se fija ANTES de
    trabajar (ahora + intervalo); tarde lo que tarde el trabajo, la cadencia
    no deriva.

Conexión a BD POR TICK, no persistente (decisión tomada aquí): Neon
autosuspende con la inactividad — una conexión abierta durante el fin de
semana (65h) estaría muerta el lunes. Abrir/cerrar por tick cuesta un
handshake cada ~75s (nada) y deja a Neon dormir fuera de horario, que además
es amable con el free tier.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import tempfile
import threading
from pathlib import Path

from prometheus_client import Counter, Gauge

from . import db
from .config import Universe
from .ingest.intraday import ingest_intraday
from .market_hours import is_market_open, next_open

log = logging.getLogger(__name__)

# En el contenedor tempfile.gettempdir() es /tmp → /tmp/heartbeat, la ruta
# que mira la liveness probe. HEARTBEAT_FILE permite moverlo si hiciera falta.
HEARTBEAT_FILE = Path(os.environ.get("HEARTBEAT_FILE") or Path(tempfile.gettempdir()) / "heartbeat")

NAP_CAP_SECONDS = 15 * 60  # tope de siesta: el latido más lento legítimo

# Fallos SEGUIDOS tolerados antes de crashear (~40 min a cadencia de 75s).
# Un fallo suelto de Yahoo es rutina; 30 seguidos es "algo está roto de
# verdad" → mejor morir y que K8s reinicie (quizá a un pod/nodo más sano)
# que quedarse de zombi logueando errores con el latido fresco.
MAX_CONSECUTIVE_FAILURES = 30

# Reintentos del gap-fill de arranque (ver run()). 4 intentos con base 5s
# esperan 5+10+20 = 35s en el peor caso: de sobra para que CoreDNS levante
# (medido: fallaba a los 5s y resolvía antes de 25s) y lo bastante corto para
# no retrasar el arranque si el fallo fuera permanente.
GAPFILL_MAX_ATTEMPTS = 4
GAPFILL_BACKOFF_BASE_S = 5.0

# Puerto del /metrics que scrapea Prometheus (fase 9). Env-overridable como el
# latido; el Service y el Deployment apuntan a este mismo 8000.
METRICS_PORT = int(os.environ.get("METRICS_PORT") or 8000)

# --- Métricas Prometheus (fase 9) --------------------------------------------
# El poller es un proceso VIVO → modelo pull: expone esto por HTTP y Prometheus
# lo scrapea. El servidor HTTP se arranca en la CAPA DE PROCESO (cli.py), no
# aquí: run() solo TOCA los contadores (operación pura, sin abrir sockets), así
# el bucle sigue siendo testeable sin puertos. Convención Prometheus: los
# contadores acumulativos acaban en _total.
TICKS = Counter("sentinel_poller_ticks_total", "Ticks de mercado ejecutados con éxito")
TICK_FAILURES = Counter("sentinel_poller_tick_failures_total", "Ticks que fallaron")
CANDLES = Counter(
    "sentinel_poller_candles_upserted_total",
    "Velas 1m upserteadas por el poller (gap-fill de arranque incluido)",
)
CONSECUTIVE_FAILURES = Gauge(
    "sentinel_poller_consecutive_failures",
    f"Fallos consecutivos actuales (el proceso crashea al llegar a {MAX_CONSECUTIVE_FAILURES})",
)
LAST_TICK = Gauge(
    "sentinel_poller_last_tick_timestamp_seconds",
    "Momento (epoch Unix) del último tick con éxito; su antigüedad delata un poller mudo",
)
MARKET_OPEN = Gauge("sentinel_market_open", "1 si el mercado XNYS está abierto, 0 si no")
GAPFILL_CANDLES = Gauge(
    "sentinel_poller_gapfill_candles",
    "Velas recuperadas en el último gap-fill de arranque (mide el tamaño del apagón)",
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _beat() -> None:
    HEARTBEAT_FILE.touch()


def nap_seconds(now: dt.datetime, opens_at: dt.datetime, cap: float = NAP_CAP_SECONDS) -> float:
    """Cuánto dormir con el mercado cerrado: hasta la apertura, pero nunca
    más de `cap` (el latido debe seguir sonando). Pura → testeable."""
    remaining = (opens_at - now).total_seconds()
    return max(0.0, min(remaining, cap))


def gapfill_backoff_seconds(attempt: int, base: float = GAPFILL_BACKOFF_BASE_S) -> float:
    """Espera antes del reintento nº `attempt` (1-indexado) del gap-fill.

    Exponencial (base, 2·base, 4·base…): cubre de sobra el arranque de CoreDNS
    sin castigar a la fuente si el fallo fuera de otra clase. Pura → testeable.
    """
    return base * (2 ** (attempt - 1))


def run(stop: threading.Event, universe: Universe) -> None:
    """Bucle principal. Sale limpiamente cuando `stop` se activa (SIGTERM)."""
    interval = universe.poll_interval_seconds
    log.info("poller: arrancando (cadencia %ds, latido en %s)", interval, HEARTBEAT_FILE)

    # Gap-fill de arranque: la ventana máxima de 1m que da yfinance.
    #
    # Se reintenta DENTRO del proceso, con backoff exponencial y un tope de
    # intentos. Matiz importante: seguimos SIN crashear, porque reintentar "en
    # bucle de reinicios" sí amplificaría un 429 de Yahoo (cada reinicio = otra
    # petición 7d). Lo que motiva el reintento in-process es otro fallo, medido
    # y reproducible: en TODO arranque en frío del clúster el gap-fill moría a
    # los ~5 s con "Temporary failure in name resolution" porque CoreDNS aún no
    # resolvía el host de Neon. Es transitorio y se cura solo en segundos, pero
    # como el gap-fill solo se intentaba UNA vez, el intradía perdido no se
    # recuperaba hasta reiniciar el pod a mano.
    #
    # La espera usa stop.wait(): un SIGTERM durante los reintentos sale limpio
    # en vez de hacer esperar a K8s hasta el kill (decisión #24, crash-only).
    for attempt in range(1, GAPFILL_MAX_ATTEMPTS + 1):
        try:
            with db.connect() as conn:
                n = ingest_intraday(conn, universe, period="7d", save_bronze=True)
            log.info("gap-fill de arranque: %d velas upserteadas (ventana 7d)", n)
            GAPFILL_CANDLES.set(n)
            CANDLES.inc(n)
            break
        except Exception:
            if attempt == GAPFILL_MAX_ATTEMPTS:
                log.exception(
                    "gap-fill de arranque falló tras %d intentos; los ticks 1d cubrirán hoy",
                    attempt,
                )
                break
            espera = gapfill_backoff_seconds(attempt)
            log.warning(
                "gap-fill de arranque falló (intento %d/%d); reintento en %.0fs",
                attempt, GAPFILL_MAX_ATTEMPTS, espera,
            )
            if stop.wait(espera):
                log.info("SIGTERM durante los reintentos del gap-fill; salida limpia")
                return

    failures = 0
    while not stop.is_set():
        _beat()
        now = _utcnow()

        open_now = is_market_open(now)
        MARKET_OPEN.set(1 if open_now else 0)
        if not open_now:
            opens_at = next_open(now)
            log.info("mercado cerrado; abre %s (siesta ≤%d min)",
                     opens_at.isoformat(timespec="minutes"), NAP_CAP_SECONDS // 60)
            stop.wait(nap_seconds(now, opens_at))
            continue

        target = now + dt.timedelta(seconds=interval)  # reloj de pared: se fija ANTES
        try:
            with db.connect() as conn:  # conexión por tick (ver docstring)
                n = ingest_intraday(conn, universe, period="1d")
            failures = 0
            TICKS.inc()
            CANDLES.inc(n)
            CONSECUTIVE_FAILURES.set(0)
            LAST_TICK.set(_utcnow().timestamp())
            log.info("tick: %d velas upserteadas", n)
        except Exception:
            failures += 1
            TICK_FAILURES.inc()
            CONSECUTIVE_FAILURES.set(failures)
            log.exception("tick fallido (%d seguidos)", failures)
            if failures >= MAX_CONSECUTIVE_FAILURES:
                raise RuntimeError(
                    f"{failures} ticks fallidos seguidos: esto ya no es un bache, "
                    "muero para que K8s me reinicie"
                )
        stop.wait(max(0.0, (target - _utcnow()).total_seconds()))

    log.info("poller: SIGTERM recibido, salida limpia")
