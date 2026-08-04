{{/*
Helpers del chart. Deliberadamente POCOS y cortos.

No hay `sentinel.fullname` (el helper estrella de `helm create`) porque este
chart NO prefija los nombres con el release: ver la cabecera de values.yaml
para el porqué. Los recursos se llaman como se llaman, y ese es el contrato.
*/}}

{{/*
La imagen, en un solo sitio. image.tag vacío cae en appVersion de Chart.yaml,
que es donde vive la versión de la aplicación.
*/}}
{{- define "sentinel.image" -}}
{{ .Values.image.repository }}:{{ .Values.image.tag | default .Chart.AppVersion }}
{{- end -}}

{{/*
Labels comunes. `part-of` es la que ya llevaban los manifiestos a mano y la que
permite sacarlos todos de golpe:  kubectl get all -l app.kubernetes.io/part-of=k8s-market-sentinel
*/}}
{{- define "sentinel.labels" -}}
app.kubernetes.io/part-of: {{ .Values.partOf }}
{{- end -}}

{{/*
Hash del contenido de unos ficheros del repo, para anotar el pod template.

PARA QUÉ: cierra dos gotchas operativos documentados y pagados a golpes.
Los ConfigMaps de este chart tienen nombre ESTABLE (sin sufijo-hash), así que
cambiar un dashboard o prometheus.yml actualizaba el ConfigMap pero NO tocaba
el Deployment -> el pod seguía sirviendo la versión vieja y había que acordarse
de un `kubectl rollout restart` a mano. Le pasó a los dashboards de Grafana
(que no releen el ConfigMap ni con updateIntervalSeconds) y a Prometheus (que
no recarga su config solo).

Con el hash en una anotación del POD TEMPLATE, cambiar el fichero cambia el
Deployment, y quien reinicia es Kubernetes. Deja de haber un paso manual que
recordar — que es justo lo que un despliegue GitOps promete.

Se hashean los ficheros CONCRETOS que le importan a cada componente, y no el
render entero de los ConfigMaps, para que tocar un dashboard no reinicie
también al poller. Precisión barata: el ruido de reinicios se paga en huecos
de datos.

Uso:  {{ include "sentinel.checksum" (list . (list "ruta/a.yaml" "otra/*.json")) }}
*/}}
{{- define "sentinel.checksum" -}}
{{- $root := index . 0 -}}
{{- $patrones := index . 1 -}}
{{- $acumulado := "" -}}
{{- range $patrones -}}
{{- range $ruta, $_ := $root.Files.Glob . -}}
{{- $acumulado = printf "%s%s" $acumulado ($root.Files.Get $ruta) -}}
{{- end -}}
{{- end -}}
{{- $acumulado | sha256sum -}}
{{- end -}}
