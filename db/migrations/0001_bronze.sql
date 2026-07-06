-- 0001: esquemas del medallón + capa bronze.
--
-- Usamos SCHEMAS de Postgres (bronze/silver/gold) en vez de prefijos en el
-- nombre de tabla: el namespace queda explícito en cada query
-- (silver.prices_daily) y se pueden dar permisos por capa si hiciera falta.

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;

-- BRONZE = el dato tal cual llegó, sin interpretar. Append-only: aquí nunca
-- se hace UPDATE ni dedup — es el registro de auditoría que permite
-- re-procesar silver si un parser tenía un bug. Una sola tabla genérica para
-- todas las fuentes: lo que varía (la forma del payload) va en jsonb.
CREATE TABLE bronze.raw_fetches (
    id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source     text        NOT NULL, -- 'yfinance' | 'fred' | 'frankfurter' | 'cefconnect'
    entity     text        NOT NULL, -- ticker o id de serie: 'WDI', 'BAMLH0A0HYM2'...
    payload    jsonb       NOT NULL, -- respuesta cruda del fetch (el batch completo)
    meta       jsonb       NOT NULL DEFAULT '{}'::jsonb, -- parámetros del fetch (rango pedido, etc.)
    fetched_at timestamptz NOT NULL DEFAULT now()
);

-- Consulta típica sobre bronze: "¿qué llegó de esta fuente/entidad y cuándo?"
CREATE INDEX raw_fetches_source_entity_idx
    ON bronze.raw_fetches (source, entity, fetched_at DESC);
