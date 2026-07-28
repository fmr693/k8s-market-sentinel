#!/bin/sh
# Genera el .env local a partir del secreto CIFRADO del repo (fase 7a, #39).
#
# Cierra el último hueco de portabilidad: el .env nunca ha viajado por git (y no
# debe), pero todos sus valores viven ya cifrados en deploy/secrets/. Con la
# clave age —el único secreto que llevas encima— una máquina limpia se
# autoabastece: no hay que copiar credenciales a mano ni pedirlas a nadie.
#
#   ./scripts/env-from-secret.sh              # desde el secreto prod (Neon)
#   ./scripts/env-from-secret.sh local        # desde el secreto local (compose)
#   ./scripts/env-from-secret.sh prod --force # sobrescribe un .env existente
#
# Requisitos: sops en el PATH (o en ~/bin/sops.exe en Windows) y la clave age
# en la ruta estándar (%APPDATA%\sops\age\keys.txt · ~/.config/sops/age/keys.txt).
set -eu

ENTORNO="${1:-prod}"
FORCE="${2:-}"
SECRET="deploy/secrets/sentinel-env.${ENTORNO}.yaml"
DESTINO=".env"

[ -f "$SECRET" ] || { echo "ERROR: no existe $SECRET (entornos: prod | local)" >&2; exit 1; }

# sops del PATH; si no, el binario suelto que se usa en Windows (%USERPROFILE%\bin).
if command -v sops >/dev/null 2>&1; then
    SOPS=sops
elif [ -x "${USERPROFILE:-$HOME}/bin/sops.exe" ]; then
    SOPS="${USERPROFILE:-$HOME}/bin/sops.exe"
else
    echo "ERROR: no encuentro 'sops'. Instálalo: https://github.com/getsops/sops/releases" >&2
    exit 1
fi

if [ -f "$DESTINO" ] && [ "$FORCE" != "--force" ]; then
    echo "ERROR: $DESTINO ya existe. Usa --force para sobrescribirlo." >&2
    exit 1
fi

# stringData del Secret -> pares KEY=VALUE. El bloque termina en la siguiente
# clave de nivel raíz (type:, sops:...). Se quitan las comillas que pone SOPS.
# Las líneas de comentario (que SOPS conserva) se ignoran.
"$SOPS" -d "$SECRET" | awk '
    /^stringData:/ { dentro = 1; next }
    /^[^[:space:]]/ { dentro = 0 }
    !dentro { next }
    /^[[:space:]]*#/ { next }
    {
        linea = $0
        sub(/\r$/, "", linea)
        pos = index(linea, ":")
        if (pos == 0) next
        clave = substr(linea, 1, pos - 1)
        valor = substr(linea, pos + 1)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", clave)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", valor)
        if (clave !~ /^[A-Za-z_][A-Za-z0-9_]*$/) next
        n = length(valor)
        if (n >= 2 && substr(valor, 1, 1) == "\"" && substr(valor, n, 1) == "\"")
            valor = substr(valor, 2, n - 2)
        else if (n >= 2 && substr(valor, 1, 1) == "'\''" && substr(valor, n, 1) == "'\''")
            valor = substr(valor, 2, n - 2)
        print clave "=" valor
    }
' > "$DESTINO"

# El .env lleva credenciales: permisos restrictivos (no-op en Windows, sano en Linux).
chmod 600 "$DESTINO" 2>/dev/null || true

# Resumen SIN filtrar secretos: solo nombres de clave y a dónde apunta.
echo "$DESTINO generado desde $SECRET"
echo "  claves: $(grep -c '=' "$DESTINO")"
case "$(grep '^GRAFANA_DB_HOST=' "$DESTINO" | cut -d= -f2-)" in
    *neon.tech*) echo "  Grafana -> Neon (la nube)";;
    *)           echo "  Grafana -> Postgres local (compose)";;
esac
