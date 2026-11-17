---
id: rb-tls-cert
title: "Runbook: TLS certificate expiry and rotation"
type: runbook
owner: platform-team
updated: 2026-05-11
---
# Runbook: TLS certificate expiry and rotation

## Symptoms
Clients report `certificate has expired` or `x509` errors. Internal services calling an ingress host fail together.

## Checks
1. Check the certificate's expiry: cert-manager's `Certificate` resource shows `Ready` and `notAfter`.
2. If cert-manager failed to renew, its events usually say why: a DNS challenge failing or a rate limit.

## Rotation
1. Fix the renewal cause, then trigger a renewal with `cmctl renew <certificate> -n <namespace>`.
2. Confirm the new expiry date and that the ingress serves the new certificate.

Certificates renew 30 days before expiry. An alert fires at 14 days left; if you are reading this during an outage, that alert was missed or silenced, so add it to the postmortem.
