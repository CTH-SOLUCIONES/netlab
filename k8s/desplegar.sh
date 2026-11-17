#!/usr/bin/env bash
# Despliega o actualiza NetLab en un cluster de Kubernetes.
#
# Uso:
#   NETLAB_DOMINIO=netlab.mi-academia.edu ./k8s/desplegar.sh
#
# Variables opcionales:
#   NETLAB_DOMINIO        dominio publico del laboratorio (obligatoria)
#   NETLAB_INGRESS_CLASS  clase de ingress del cluster       (defecto: nginx)
#   NETLAB_ISSUER         ClusterIssuer de cert-manager      (defecto: letsencrypt)
#
# Es idempotente: se puede ejecutar tantas veces como se quiera. Cada vez
# regenera el ConfigMap con el codigo de app/ y reinicia el pod para que tome
# los cambios.
#
# La primera vez crea el secreto netlab-claves con claves aleatorias y las
# imprime una sola vez: anotelas. Las ejecuciones siguientes las conservan.
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NS=netlab

DOMINIO="${NETLAB_DOMINIO:-}"
INGRESS_CLASS="${NETLAB_INGRESS_CLASS:-nginx}"
ISSUER="${NETLAB_ISSUER:-letsencrypt}"

if [[ -z "$DOMINIO" ]]; then
  echo "Defina el dominio del laboratorio, por ejemplo:"
  echo "  NETLAB_DOMINIO=netlab.mi-academia.edu $0"
  exit 1
fi

# El laboratorio queda en internet: nunca arranca con las claves de fabrica,
# que estan publicadas en la guia. El secreto va antes que los manifiestos
# para que el pod no arranque sin el.
echo "==> Preparando el namespace $NS y sus claves"
# --save-config deja la anotacion que espera el kubectl apply de mas abajo.
kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS" --save-config
if kubectl get secret netlab-claves --namespace "$NS" >/dev/null 2>&1; then
  echo "    El secreto netlab-claves ya existe; se conservan sus claves."
else
  command -v openssl >/dev/null || { echo "Falta openssl para generar las claves"; exit 1; }
  CLAVE_ADMIN="$(openssl rand -hex 16)"
  CLAVE_LECTURA="$(openssl rand -hex 16)"
  kubectl create secret generic netlab-claves --namespace "$NS" \
    --from-literal=admin="$CLAVE_ADMIN" \
    --from-literal=lectura="$CLAVE_LECTURA" >/dev/null
  echo
  echo "    Claves del laboratorio. Se muestran SOLO esta vez: anotelas."
  echo "      administrador (X-API-Key de escritura): $CLAVE_ADMIN"
  echo "      lectura:                                $CLAVE_LECTURA"
  echo
fi

echo "==> Aplicando manifiestos base (dominio: $DOMINIO)"
sed -e "s/netlab\.example\.com/$DOMINIO/g" \
    -e "s/ingressClassName: nginx$/ingressClassName: $INGRESS_CLASS/" \
    -e "s/cluster-issuer: letsencrypt$/cluster-issuer: $ISSUER/" \
    "$RAIZ/k8s/netlab.yaml" | kubectl apply -f -

echo "==> Generando ConfigMap con el codigo de la aplicacion"
kubectl create configmap netlab-codigo \
  --namespace "$NS" \
  --from-file="$RAIZ/app/netlab.py" \
  --from-file="$RAIZ/app/ui.html" \
  --dry-run=client -o yaml | kubectl apply -f -

# El hash del codigo va en una anotacion del pod: cambia el hash, se reinicia.
HASH=$(cat "$RAIZ/app/netlab.py" "$RAIZ/app/ui.html" | shasum -a 256 | cut -c1-16)
echo "==> Hash del codigo: $HASH"
kubectl patch deployment netlab-api -n "$NS" --type=merge \
  -p "{\"spec\":{\"template\":{\"metadata\":{\"annotations\":{\"checksum/codigo\":\"$HASH\"}}}}}"

echo "==> Esperando que el pod este listo"
kubectl rollout status deployment/netlab-api -n "$NS" --timeout=180s

echo
echo "==> Estado"
kubectl get pods,svc,ingress -n "$NS"
echo
echo "Laboratorio disponible en: https://$DOMINIO/"
