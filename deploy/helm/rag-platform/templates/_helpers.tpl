{{- define "rag-platform.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-platform.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "rag-platform.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "rag-platform.labels" -}}
app.kubernetes.io/name: {{ include "rag-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "rag-platform.selectorLabels" -}}
app.kubernetes.io/name: {{ include "rag-platform.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}
