{{/* Chart name (overridable via nameOverride) */}}
{{- define "graphrag-ui.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Full resource-name prefix: <release>-graphrag-ui */}}
{{- define "graphrag-ui.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "graphrag-ui.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "graphrag-ui.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{- define "graphrag-ui.selectorLabels" -}}
app.kubernetes.io/name: {{ include "graphrag-ui.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/* App-layer Secret (JWT/bootstrap/DATABASE_URL); an existingSecret value switches to the pre-existing Secret */}}
{{- define "graphrag-ui.secretName" -}}
{{- default (printf "%s-secret" (include "graphrag-ui.fullname" .)) .Values.existingSecret -}}
{{- end -}}

{{/* Service name of the bundled postgresql dependency — replicates bitnami common.names.fullname:
     the release name when it already contains "postgresql", else <release>-postgresql */}}
{{- define "graphrag-ui.postgresql.fullname" -}}
{{- if contains "postgresql" .Release.Name -}}
{{- .Release.Name -}}
{{- else -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}
{{- end -}}

{{/* Build DATABASE_URL from values: external DB uses url directly; bundled DB splices the dependency's auth values */}}
{{- define "graphrag-ui.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password (include "graphrag-ui.postgresql.fullname" .) .Values.postgresql.auth.database -}}
{{- else -}}
{{- required "externalDatabase.url is required when postgresql.enabled=false" .Values.externalDatabase.url -}}
{{- end -}}
{{- end -}}

{{/* Secret holding oauth2-proxy material: operator-provided or chart-created (spec §7.2) */}}
{{- define "graphrag-ui.proxyAuthSecretName" -}}
{{- if .Values.proxyAuth.existingSecret -}}{{ .Values.proxyAuth.existingSecret }}{{- else -}}{{ include "graphrag-ui.fullname" . }}-proxy-auth{{- end -}}
{{- end -}}
