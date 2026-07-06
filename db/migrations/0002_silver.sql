-- 0002: capa silver — dato limpio, tipado y DEDUPLICADO.
-- Aquí vive el contrato del upsert idempotente (decisión #3 del brief):
-- cada tabla tiene una clave natural como PRIMARY KEY y los ingestores hacen
-- INSERT ... ON CONFLICT DO UPDATE contra ella. Re-ejecutar nunca duplica.

-- Velas DIARIAS (grano = fecha de negociación). Va separada de la intradía a
-- propósito: distinto grano temporal (date vs timestamptz), distinta clave
-- natural y distinta retención futura. Forzarlas en una sola tabla obligaría
-- a inventar timestamps falsos para las velas diarias.
CREATE TABLE silver.prices_daily (
    ticker       text NOT NULL,
    trading_date date NOT NULL,
    open         numeric(18, 6),
    high         numeric(18, 6),
    low          numeric(18, 6),
    close        numeric(18, 6) NOT NULL, -- cierre SIN ajustar: el descuento compara precio real vs NAV publicado
    volume       bigint,
    source       text NOT NULL DEFAULT 'yfinance',
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, trading_date) -- la clave del upsert idempotente
);

-- Para queries "todo el universo en la fecha X" (dashboards, gold):
CREATE INDEX prices_daily_date_idx ON silver.prices_daily (trading_date);

-- Ticks INTRADÍA (grano = instante). La rellenará el poller en fase 4;
-- se define ya para dejar fijado el contrato completo del medallón.
CREATE TABLE silver.prices_intraday (
    ticker      text NOT NULL,
    ts          timestamptz NOT NULL, -- siempre UTC; la conversión a CET es cosa de la capa de presentación
    price       numeric(18, 6) NOT NULL,
    volume      bigint,
    source      text NOT NULL DEFAULT 'yfinance',
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ts)
);

CREATE INDEX prices_intraday_ts_idx ON silver.prices_intraday (ts);

-- NAV diario por CEF (lo rellenará el ingestor de NAV, más adelante).
-- quality etiqueta la confianza (brief: el NAV intradía es estimado):
--   'definitivo' = publicado por la fuente tras el cierre
--   'estimado'   = derivado/aproximado (p. ej. NAV de ayer usado hoy)
--   'rancio'     = no se ha podido refrescar en N días
CREATE TABLE silver.navs (
    ticker      text NOT NULL,
    nav_date    date NOT NULL,
    nav         numeric(18, 6) NOT NULL,
    quality     text NOT NULL DEFAULT 'definitivo'
                CHECK (quality IN ('definitivo', 'estimado', 'rancio')),
    source      text NOT NULL, -- 'cefconnect' | 'yfinance-xTICKER' | gestora
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, nav_date)
);

-- Series macro (FRED, BCE...). Una tabla genérica serie/fecha/valor: todas
-- las fuentes macro comparten esa forma y añadir una serie nueva es solo
-- tocar config, no crear tablas.
CREATE TABLE silver.macro_series (
    series_id   text NOT NULL, -- 'BAMLH0A0HYM2', 'DGS10', 'GDP', 'EURUSD_ECB'...
    obs_date    date NOT NULL,
    value       numeric(20, 6) NOT NULL, -- FRED marca huecos con '.'; esos se OMITEN, no se inventan
    source      text NOT NULL,
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (series_id, obs_date)
);
