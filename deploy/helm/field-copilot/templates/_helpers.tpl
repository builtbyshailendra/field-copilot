{{- define "fc.name" -}}{{ .Release.Name }}{{- end -}}

{{- define "fc.labels" -}}
app.kubernetes.io/name: field-copilot
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end -}}

{{- define "fc.secretName" -}}
{{- if .Values.secrets.existingSecret -}}{{ .Values.secrets.existingSecret }}{{- else -}}{{ include "fc.name" . }}-secrets{{- end -}}
{{- end -}}

{{- define "fc.podSecurity" -}}
securityContext:
  runAsNonRoot: true
  seccompProfile: {type: RuntimeDefault}
{{- end -}}

{{- define "fc.containerSecurity" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities: {drop: ["ALL"]}
{{- end -}}

{{/* Image reference: repository@digest if a digest is set, else repository:tag (default appVersion). */}}
{{- define "fc.image" -}}
{{- $img := index .root.Values.image .name -}}
{{- if $img.digest -}}{{ $img.repository }}@{{ $img.digest }}{{- else -}}{{ $img.repository }}:{{ $img.tag | default .root.Chart.AppVersion }}{{- end -}}
{{- end -}}

{{/* The API pod spec, shared by the Deployment and the Argo Rollout. */}}
{{- define "fc.apiPodSpec" -}}
{{ include "fc.podSecurity" . }}
containers:
  - name: api
    image: {{ include "fc.image" (dict "root" . "name" "api") | quote }}
    imagePullPolicy: {{ .Values.image.api.pullPolicy }}
    ports: [{name: http, containerPort: 8020}]
    envFrom: [{secretRef: {name: {{ include "fc.secretName" . }}}}]
    env:
      {{- range $k, $v := .Values.api.env }}
      - {name: {{ $k }}, value: {{ $v | quote }}}
      {{- end }}
      {{- if .Values.api.fault }}
      - {name: FIELD_FAULT, value: {{ .Values.api.fault | quote }}}
      {{- end }}
    readinessProbe: {httpGet: {path: /readyz, port: http}, periodSeconds: 5, failureThreshold: 3}
    livenessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 10, failureThreshold: 3}
    startupProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 3, failureThreshold: 40}
    resources: {{- toYaml .Values.api.resources | nindent 6 }}
    {{ include "fc.containerSecurity" . | nindent 4 }}
    volumeMounts: [{name: tmp, mountPath: /tmp}]
volumes: [{name: tmp, emptyDir: {}}]
{{- end -}}
