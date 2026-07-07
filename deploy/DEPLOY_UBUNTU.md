# Runbook — Despliegue en el servidor Ubuntu (cierre de Fase 3)

> Guía paso a paso para el día que haya acceso al servidor. Prerrequisito: la
> imagen ya publicada en GHCR (pasos 0-1 se hacen desde cualquier máquina con
> Docker; 2 en adelante, en el servidor).

## 0. Publicar la imagen en GHCR (una vez, desde Windows)

La imagen ya está construida y etiquetada localmente como
`ghcr.io/fmr693/k8s-market-sentinel:0.1.0`. Para subirla:

1. Crear un **PAT (classic)** en GitHub → Settings → Developer settings →
   Personal access tokens, con el scope **`write:packages`**.
2. Login y push (el token se pasa por stdin, no queda en el historial):

   ```powershell
   $env:CR_PAT = "<token>"
   $env:CR_PAT | docker login ghcr.io -u fmr693 --password-stdin
   docker push ghcr.io/fmr693/k8s-market-sentinel:0.1.0
   docker push ghcr.io/fmr693/k8s-market-sentinel:latest
   Remove-Item Env:CR_PAT
   ```

3. **Hacer el paquete público** (recomendado — evita imagePullSecrets en el
   clúster y es coherente con un proyecto portfolio): en github.com →
   perfil → Packages → `k8s-market-sentinel` → Package settings →
   Danger Zone → Change visibility → Public.
   - *Si se prefiere privado*: crear en el clúster un secret
     `kubectl -n sentinel create secret docker-registry ghcr-pull
     --docker-server=ghcr.io --docker-username=fmr693
     --docker-password=<PAT-read:packages>` y añadir
     `imagePullSecrets: [{name: ghcr-pull}]` a los pod specs.

En la fase 7 este paso lo automatizará GitHub Actions en cada push.

## 1. Verificación previa (desde cualquier máquina)

```bash
docker pull ghcr.io/fmr693/k8s-market-sentinel:0.1.0   # ¿se descarga sin login?
docker run --rm ghcr.io/fmr693/k8s-market-sentinel:0.1.0  # muestra el --help del CLI
```

## 2. Instalar k3s en el servidor (está virgen)

```bash
curl -sfL https://get.k3s.io | sh -
# Comprobar:
sudo k3s kubectl get nodes    # el nodo en Ready
```

- k3s se instala como servicio **systemd** (`k3s.service`): arranca solo al
  encender la máquina — exactamente el comportamiento que pide el brief.
- Para usar `kubectl` sin sudo: copiar el kubeconfig
  ```bash
  mkdir -p ~/.kube
  sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/config
  sudo chown "$USER" ~/.kube/config
  ```
- Nota: el Ubuntu moderno ya usa cgroup v2 — la lección de WSL2
  (`cgroup_no_v1`) NO aplica aquí.

## 3. Llevar el repo y los secretos al servidor

```bash
git clone https://github.com/fmr693/k8s-market-sentinel.git
cd k8s-market-sentinel
```

El `.env` **no viaja por git** (está en .gitignore, a propósito). Crearlo a
mano en la raíz del repo clonado, con los mismos valores que en desarrollo
(plantilla: `.env.example`; los valores, del gestor de contraseñas o de la
consola de Neon/FRED). Comprobar permisos: `chmod 600 .env`.

## 4. Desplegar

```bash
kubectl apply -k .                 # namespace + ConfigMap + Secret + 4 CronJobs
kubectl -n sentinel get cronjobs   # deben aparecer los 4, SUSPEND=False
```

## 5. Migrar el esquema (acción puntual)

```bash
kubectl -n sentinel create -f deploy/k8s/job-migrate.yaml
kubectl -n sentinel wait --for=condition=complete job -l app.kubernetes.io/part-of=k8s-market-sentinel --timeout=120s
kubectl -n sentinel logs job/$(kubectl -n sentinel get jobs -o jsonpath='{.items[-1:].metadata.name}')
```

(Contra la MISMA Neon ya migrada dirá "nada que aplicar" — correcto: el
runner es idempotente.)

## 6. Validación end-to-end (disparar un CronJob a mano)

```bash
kubectl -n sentinel create job --from=cronjob/ingest-fx smoke-fx
kubectl -n sentinel wait --for=condition=complete job/smoke-fx --timeout=300s
kubectl -n sentinel logs job/smoke-fx
```

Éxito = logs de ingesta con upserts contra Neon **desde el clúster del
servidor**. Con eso, la Fase 3 queda cerrada de verdad.

## 7. Limpieza y notas

```bash
kubectl -n sentinel delete job smoke-fx
```

- Los CronJobs quedan programados en `Europe/Madrid` (L-V). Si el servidor
  está apagado a esa hora, la ejecución se pierde **por diseño**: el backfill
  idempotente se pone al día en la siguiente (decisión #18 de DECISIONS.md).
- Actualizar imagen en el futuro: build + push con tag nuevo → cambiar
  `newTag` en `kustomization.yaml` (y la etiqueta de `job-migrate.yaml`) →
  `kubectl apply -k .`
