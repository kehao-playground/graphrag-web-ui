{{/* Chart 名稱(可用 nameOverride 覆寫) */}}
{{- define "graphrag-ui.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* 完整資源名稱前綴:<release>-graphrag-ui */}}
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

{{/* 應用層 Secret(JWT/bootstrap/DATABASE_URL);existingSecret 設定時改用既有 Secret */}}
{{- define "graphrag-ui.secretName" -}}
{{- default (printf "%s-secret" (include "graphrag-ui.fullname" .)) .Values.existingSecret -}}
{{- end -}}

{{/* 內建 postgresql dependency 的 service 名稱 — 複製 bitnami common.names.fullname 的邏輯:
     release 名稱含 "postgresql" 時就是 release 名,否則 <release>-postgresql */}}
{{- define "graphrag-ui.postgresql.fullname" -}}
{{- if contains "postgresql" .Release.Name -}}
{{- .Release.Name -}}
{{- else -}}
{{- printf "%s-postgresql" .Release.Name -}}
{{- end -}}
{{- end -}}

{{/* 由 values 組出 DATABASE_URL:外部 DB 直接用 url;內建 DB 用 dependency 的 auth 值拼接 */}}
{{- define "graphrag-ui.databaseUrl" -}}
{{- if .Values.postgresql.enabled -}}
{{- printf "postgresql+asyncpg://%s:%s@%s:5432/%s" .Values.postgresql.auth.username .Values.postgresql.auth.password (include "graphrag-ui.postgresql.fullname" .) .Values.postgresql.auth.database -}}
{{- else -}}
{{- required "externalDatabase.url is required when postgresql.enabled=false" .Values.externalDatabase.url -}}
{{- end -}}
{{- end -}}
