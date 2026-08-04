# Demo en una máquina limpia

Cómo levantar este proyecto en un PC que no es el tuyo, sin copiar credenciales
a mano. Dos caminos: el **rápido** (dashboards en ~3 min) y el **completo** (la
plataforma entera en Kubernetes, ~20 min).

> **Lo único que tienes que llevar encima: la clave privada age.**
> Todo lo demás viaja: el repo es público, los secretos van **cifrados** dentro
> de él (SOPS+age), la imagen está en GHCR (pública) y los datos en Neon (nube).
> Ese es el reparto de la decisión #39: el texto cifrado puede ser público, la
> llave viaja contigo.

---

## 0. Antes de salir de casa

- [ ] Copia la clave privada age (`%APPDATA%\sops\age\keys.txt`) a tu **gestor
      de contraseñas**. Sin ella no se descifra NADA.
- [ ] Comprueba que la public key del gestor coincide con la de `.sops.yaml`
      (`age1gqgan8...`). Si no coincide, es otra clave y no sirve.

---

## Plan A — Dashboards en 3 minutos (docker compose)

Enseña el producto: descuentos, z-scores, backtest, calidad de dato — con datos
**reales** de Neon. Mínima superficie de fallo: solo Docker.

### Requisitos
| Herramienta | Windows | Linux |
|---|---|---|
| Docker Desktop | [docker.com](https://www.docker.com/products/docker-desktop/) | `apt install docker.io docker-compose-v2` |
| git | ya suele estar | `apt install git` |
| sops | descargar el `.exe` de [releases](https://github.com/getsops/sops/releases) a `%USERPROFILE%\bin\` | descargar el binario a `/usr/local/bin/sops` (+`chmod +x`) |

### Pasos

```bash
# 1. El repo (público: no hace falta autenticarse)
git clone https://github.com/fmr693/k8s-market-sentinel.git
cd k8s-market-sentinel

# 2. La clave age, en la ruta estándar de SOPS
#    Windows: %APPDATA%\sops\age\keys.txt
#    Linux:   ~/.config/sops/age/keys.txt
mkdir -p ~/.config/sops/age && vi ~/.config/sops/age/keys.txt   # pegar la clave

# 3. El .env, generado DESDE el secreto cifrado (aquí está la gracia)
./scripts/env-from-secret.sh prod

# 4. Grafana (+ Prometheus, Pushgateway y un Postgres local de cortesía)
docker compose -f docker-compose.dev.yml up -d
```

Abre **http://localhost:3000** · usuario `admin`.
La password: `grep GRAFANA_ADMIN_PASSWORD .env`

### Qué enseñar (en este orden)
1. **CEF Sentinel** — la tabla del universo ordenada por z-score: la columna
   **Signal** traduce el número a "🟢 Historically cheap / ⚪ Normal…". Semáforos
   macro arriba (HY spread, VIX, Buffett, EUR/USD).
2. **Signal backtest** (abajo del mismo dashboard) — qué pasó tras cada cruce de
   z-score bajo −2, con el panel de *caveats* al lado: la muestra está dominada
   por un único episodio de mercado y se dice abiertamente.
3. **Pipeline Health** — frescura por fuente y los paneles de **Data Quality**
   (fase 10): el veredicto de cada check y el cross-check del NAV entre dos
   fuentes independientes.

> Los 5 paneles de proceso de *Pipeline Health* (ticks del poller, jobs de
> ingesta) enseñarán **"No data"**: aquí hay Prometheus, pero no corre el poller
> que produce esas métricas. Es esperado. Se encienden si levantas el poller
> (`sentinel poller`) o en el Plan B, donde corre dentro del clúster.

### Al terminar
```bash
docker compose -f docker-compose.dev.yml down
```

---

## Plan B — La plataforma completa (k3d + ArgoCD)

Enseña la ingeniería: GitOps, observabilidad, secretos cifrados, autorreparación.

### Requisitos añadidos
`k3d`, `kubectl` y `helm` ([k3d.io](https://k3d.io) ·
`winget install Kubernetes.kubectl` · `winget install Helm.Helm`).

> `helm` solo hace falta para el despliegue **a mano**. Por GitOps no: el
> repo-server de ArgoCD trae el suyo embebido y renderiza el chart él solo.

> **Arrancar Docker NO arranca Kubernetes.** El clúster k3d son contenedores
> Docker que, al apagar Docker Desktop, mueren y **se quedan parados**: no
> vuelven solos. Son dos pasos distintos. Mira primero si el clúster ya existe:
>
> ```bash
> k3d cluster list      # SERVERS 0/1 = existe pero está PARADO
> ```

```bash
# 1. Clúster.
#    a) Si ya existe (lo normal en tu máquina de siempre): arrancarlo.
k3d cluster start sentinel
#    b) Si no existe (máquina nueva): crearlo. El --api-port NO es decorativo:
#       sin él, k3d coge un puerto aleatorio que en Windows suele caer en un
#       rango reservado por WinNAT y el clúster no arranca tras reiniciar Docker.
k3d cluster create sentinel --api-port 6550

# 2. La app (la imagen se baja sola de GHCR: es pública)
helm install sentinel . -n sentinel --create-namespace

# 3. El Secret, descifrado al vuelo (nunca toca disco en claro)
sops -d deploy/secrets/sentinel-env.prod.yaml | kubectl apply -f -

# 4. El esquema (idempotente: si ya está aplicado, no hace nada)
helm template . --set migrations.autoRun=true -s templates/job-migrate.yaml \
  | kubectl -n sentinel create -f -

# 5. Comprobar
kubectl -n sentinel get pods,cronjobs,pvc
```

Grafana: `kubectl -n sentinel port-forward svc/grafana 3000:3000`
Prometheus: `kubectl -n sentinel port-forward svc/prometheus 9090:9090` → `/targets`

### Qué esperar al arrancar en frío (medido el 2026-07-29)

Partiendo del clúster parado, `k3d cluster start sentinel`:

| Momento | t |
|---|---|
| Nodo `Ready` | ~22 s |
| Poller `Running` | ~20 s |
| Prometheus y Pushgateway listos | ~30 s |
| Grafana lista y sirviendo | ~45 s |

A los ~45 s: los 3 targets de Prometheus **UP**, ArgoCD **Synced/Healthy** solo, y
Grafana leyendo Neon sin tocar nada (el Secret no cambió, así que **no** hace
falta `rollout restart`; solo si acabas de cambiar el secreto).

> **El gap-fill del poller y el arranque en frío — resuelto en la 0.8.0.** El
> poller intenta su gap-fill ~5 s después de nacer, cuando CoreDNS todavía no
> resuelve el host de Neon → `Temporary failure in name resolution`. Desde la
> **0.8.0** reintenta dentro del proceso con backoff exponencial (4 intentos,
> 5+10+20 s), interrumpible por SIGTERM: ya no hace falta tocar nada al arrancar
> en frío.
>
> Solo si el clúster corriera una imagen **≤0.7.0** (compruébalo con
> `kubectl -n sentinel get deploy poller -o jsonpath='{..image}'`) seguiría el
> comportamiento viejo, y tras un parón largo haría falta
> `kubectl -n sentinel rollout restart deploy/poller`.

### GitOps (opcional, +5 min) — "desplegar = hacer commit"
```bash
kubectl create namespace argocd
# --server-side: el CRD applicationsets pasa de 256KB y un apply normal lo rechaza
kubectl apply --server-side -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl -n argocd create secret generic sops-age --from-file=keys.txt=$HOME/.config/sops/age/keys.txt
kubectl -n argocd patch deployment argocd-repo-server --patch-file deploy/gitops/argocd/repo-server-ksops-patch.yaml
kubectl -n argocd patch configmap argocd-cm --type merge -p '{"data":{"kustomize.buildOptions":"--enable-alpha-plugins --enable-exec"}}'
kubectl apply -f deploy/gitops/argocd/application-sentinel-prod.yaml
kubectl -n argocd get applications   # Synced/Healthy
```

### Refrescar el dato si el clúster estuvo apagado

Los CronJobs no recuperan las noches perdidas (solo lo que cae dentro de
`startingDeadlineSeconds`, 1 h). Pero el **backfill es idempotente**: una sola
ejecución tapa el hueco, sean 2 días o 2 semanas.

```bash
for cj in ingest-prices ingest-nav ingest-macro ingest-fx \
          ingest-distributions ingest-nav-proxy; do
  kubectl -n sentinel create job --from=cronjob/$cj refresh-$cj
done
kubectl -n sentinel create job --from=cronjob/check-quality refresh-check-quality
```

Comprobar que quedó al día (debe dar 0):

```bash
kubectl -n sentinel logs job/refresh-check-quality | tail -10
```

### Demos que lucen
- **Autorreparación:** `kubectl -n sentinel delete pod -l app.kubernetes.io/name=poller`
  → nace uno nuevo en segundos y su gap-fill recupera las velas perdidas.
- **Calidad de dato:** `kubectl -n sentinel create job --from=cronjob/check-quality demo-q`
  → si algún check falla, el Job queda **Failed**: la calidad del dato es un
  contrato que K8s entiende.
- **Observabilidad:** en `/targets` de Prometheus, los 3 targets UP.

---

## Enseñarlo a distancia — el interruptor de demo

Para que alguien vea los dashboards **sin estar en tu red**: un pod de
`cloudflared` abre una conexión **saliente** a Cloudflare y publica la Grafana
en una URL `https` aleatoria. No se abre ningún puerto del router, no hace falta
IP pública y funciona detrás de CGNAT.

Está desplegado con **`replicas: 0`**: existe en el clúster, pero apagado. Es un
interruptor, no una puerta.

```bash
# 1. Encender
kubectl -n sentinel scale deploy/cloudflared --replicas=1

# 2. Leer la URL (tarda ~10 s en aparecer)
kubectl -n sentinel logs deploy/cloudflared | grep trycloudflare

# 3. Al terminar — IMPORTANTE
kubectl -n sentinel scale deploy/cloudflared --replicas=0
```

Quien abra esa URL entra **sin login y en modo lectura**, y aterriza
directamente en el dashboard *CEF Sentinel*. Añadiendo `?kiosk` a la URL se
oculta toda la navegación de Grafana y queda solo el dashboard.

> **Por qué hay que apagarlo.** Un *quick tunnel* no admite Cloudflare Access,
> así que delante de Grafana solo está (a) que la URL es inadivinable y (b) que
> el visitante no puede escribir. Suficiente para una ventana corta y vigilada;
> no para dejarlo encendido sin mirar — una Grafana pública la encuentran los
> escáneres automáticos en horas, y las CVEs graves se saltan el login.
>
> La URL **cambia en cada encendido**: es efímera por diseño.
>
> El día que haya dominio propio, se sustituye el flag `--url` por un túnel con
> nombre y se pone **Cloudflare Access** delante (gratis hasta 50 usuarios). Ahí
> sí puede quedarse encendido, y entonces el acceso anónimo sobra porque el
> portero ya filtra quién pasa. Cambia la configuración del contenedor, no la
> arquitectura.

**Lo que NO ve un visitante:** el pod de Grafana solo tiene la credencial
`grafana_ro` (solo lectura sobre Neon) — no lleva `DATABASE_URL` de owner, ni la
clave de FRED, ni los tokens de Telegram. Son dos capas de solo-lectura
independientes, y es la decisión #28 pagando dividendos.

---

## Si algo falla

| Síntoma | Causa y arreglo |
|---|---|
| `sops: no encuentro...` o no descifra | La clave age no está en la ruta estándar, o es otra. Compara con `age1gqgan8...` de `.sops.yaml`. |
| Arranqué Docker y no hay pods | Docker ≠ Kubernetes: el clúster k3d no vuelve solo. `k3d cluster start sentinel`. |
| `cluster already exists` al crear | Ya existe: usa `k3d cluster start sentinel` en vez de `create`. |
| **Dashboards con datos viejos / todo "rancio"** | La ingesta corre SOLO en los CronJobs del clúster (23:05-23:45 L-V) — si estuvo apagado, nadie ingirió, y los CronJobs **no recuperan** ejecuciones perdidas. El compose no ingiere nada. Arreglo: dispararlos a mano (ver abajo). |
| Cambié un secreto y el pod sigue igual | Un Secret consumido como **variables de entorno no se recarga en caliente**, aunque ArgoCD ya lo haya sincronizado: `kubectl -n sentinel rollout restart deploy/<x>`. |
| `password authentication failed` en Grafana | Suele ser lo anterior (pod con el Secret viejo → `rollout restart`). Si persiste, la password de `grafana_ro` del secreto ya no es la de Neon: recupérala o rótala y re-cifra. |
| k3d no arranca tras reiniciar Docker | Puerto de API en rango reservado por WinNAT. Recrear con `--api-port 6550`. |
| Pods con `Temporary failure in name resolution` | CoreDNS aún no está listo tras crear el clúster. Espera unos segundos o reinicia el pod. |
| Prometheus en CrashLoop | Falta `fsGroup: 65534` (el PVC nace de root y prom corre como `nobody`). Ya está en el manifest. |
| Cambié `prometheus.yml` y no se entera | El ConfigMap tiene nombre estable, así que el pod no se reinicia: `POST /-/reload` o borra el pod. |
| Panel de Grafana vacío | Comprueba primero por SQL si hay dato; puede ser real (p. ej. fin de semana). |
| Toqué algo con `kubectl` y se deshizo solo | ArgoCD hizo su trabajo: revierte toda deriva respecto a git. **Desplegar es hacer commit** — no hay atajo. La única excepción declarada es el escalado de `cloudflared` (`ignoreDifferences`). |
| El túnel pide login | El clúster no tiene aún el commit con las env vars de visitante, o el pod de Grafana arrancó antes que ellas: `kubectl -n sentinel rollout restart deploy/grafana`. |

---

## Volver a casa

El repo no guarda estado del clúster: `k3d cluster delete sentinel` y listo.
El `.env` es local y regenerable — no lo commitees (está en `.gitignore`).
