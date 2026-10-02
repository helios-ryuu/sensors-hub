#!/usr/bin/env bash
# ==============================================================================
# deploy.sh — Deploy toàn bộ thermal-monitoring stack lên K3s
#
# Cách dùng:
#   ./k3s/scripts/deploy.sh [NODE_NAME] [--purge]
#
# Tham số:
#   NODE_NAME   Tên node K3s sẽ deploy collectors (mặc định: hỏi tự động)
#   --purge     Xóa toàn bộ namespace và tài nguyên (dùng để clean up)
#
# Yêu cầu trước khi chạy:
#   1. kubectl đã kết nối cluster K3s
#   2. Đã sửa k3s/base/secret.yaml (grafana-admin-password)
#   3. Đã build images: ./k3s/scripts/build-images.sh (trên node đích)
#   4. Dashboard JSONs: được tạo tự động từ grafana/dashboards/*.json
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
K3S_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
PROJECT_ROOT="$(cd "${K3S_DIR}/.." && pwd)"
NAMESPACE="thermal-monitoring"

# ──────────────────────────────────────────────────────────────────────────────
# Tham số
# ──────────────────────────────────────────────────────────────────────────────
NODE_NAME="${1:-}"
PURGE=false
for arg in "$@"; do
    [[ "${arg}" == "--purge" ]] && PURGE=true
done

# ──────────────────────────────────────────────────────────────────────────────
# Purge mode
# ──────────────────────────────────────────────────────────────────────────────
if [[ "${PURGE}" == "true" ]]; then
    echo "⚠️  PURGE: Xóa toàn bộ namespace ${NAMESPACE}..."
    kubectl delete namespace "${NAMESPACE}" --ignore-not-found
    kubectl delete clusterrole thermal-prometheus --ignore-not-found
    kubectl delete clusterrolebinding thermal-prometheus --ignore-not-found
    echo "✅ Đã xóa sạch."
    exit 0
fi

# ──────────────────────────────────────────────────────────────────────────────
# Xác nhận node name
# ──────────────────────────────────────────────────────────────────────────────
if [[ -z "${NODE_NAME}" ]]; then
    echo "📋 Danh sách node trong cluster:"
    kubectl get nodes -o wide
    echo ""
    read -r -p "Nhập tên node sẽ deploy thermal collectors (ví dụ: imac-ubuntu): " NODE_NAME
fi

if ! kubectl get node "${NODE_NAME}" &>/dev/null; then
    echo "❌ Không tìm thấy node '${NODE_NAME}' trong cluster."
    exit 1
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 Node deploy collectors : ${NODE_NAME}"
echo "📦 Namespace              : ${NAMESPACE}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ──────────────────────────────────────────────────────────────────────────────
# Gắn labels cho node
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "🏷️  Gắn labels cho node ${NODE_NAME}..."
kubectl label node "${NODE_NAME}" thermal=enabled --overwrite
kubectl label node "${NODE_NAME}" role=monitoring --overwrite
echo "✅ Labels: thermal=enabled, role=monitoring"

# ──────────────────────────────────────────────────────────────────────────────
# Namespace — apply trước để namespace tồn tại khi tạo secret bên dưới
# (00-base sẽ được apply lại trong loop bên dưới, kubectl apply idempotent)
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "📁 Tạo Namespace..."
kubectl apply -f "${K3S_DIR}/00-base/00-namespace.yaml"

# ──────────────────────────────────────────────────────────────────────────────
# Secrets — hỏi nếu chưa edit
# ──────────────────────────────────────────────────────────────────────────────
SECRET_FILE="${K3S_DIR}/00-base/01-secret.yaml"
if grep -q "CHANGE_THIS_ADMIN_PASSWORD" "${SECRET_FILE}"; then
    echo ""
    echo "⚠️  Phát hiện mật khẩu mặc định trong ${SECRET_FILE}"
    read -r -s -p "Nhập GRAFANA_ADMIN_PASSWORD (sẽ không hiện): " GRAFANA_PASS
    echo ""
    read -r -s -p "Nhập GRAFANA_RENDERER_TOKEN (Enter = mặc định): " RENDERER_TOKEN
    echo ""
    RENDERER_TOKEN="${RENDERER_TOKEN:-thermal-renderer-secret-token-1511}"

    kubectl create secret generic thermal-secrets \
        --namespace="${NAMESPACE}" \
        --from-literal=grafana-admin-password="${GRAFANA_PASS}" \
        --from-literal=grafana-renderer-token="${RENDERER_TOKEN}" \
        --dry-run=client -o yaml | kubectl apply -f -
    echo "✅ Secret đã tạo"
else
    kubectl apply -f "${SECRET_FILE}"
fi

# ──────────────────────────────────────────────────────────────────────────────
# Dashboard ConfigMap — tạo từ grafana/dashboards/*.json
# ──────────────────────────────────────────────────────────────────────────────
DASHBOARD_DIR="${PROJECT_ROOT}/grafana/dashboards"
echo ""
echo "📊 Tạo ConfigMap cho dashboards từ ${DASHBOARD_DIR}..."
kubectl create configmap grafana-dashboards \
    --namespace="${NAMESPACE}" \
    --from-file="${DASHBOARD_DIR}/" \
    --dry-run=client -o yaml | kubectl apply -f -
echo "✅ ConfigMap grafana-dashboards đã cập nhật"

# ──────────────────────────────────────────────────────────────────────────────
# Apply toàn bộ manifests theo thứ tự số
# Prefix số trên dir + file đảm bảo kubectl apply đúng thứ tự:
#   00-base → 10-prometheus → 20-renderer → 30-grafana → 40-collectors
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "🚀 Apply manifests..."
for dir in \
    "${K3S_DIR}/00-base" \
    "${K3S_DIR}/10-prometheus" \
    "${K3S_DIR}/20-renderer" \
    "${K3S_DIR}/30-grafana" \
    "${K3S_DIR}/40-collectors"
do
    echo "  📂 $(basename "${dir}")"
    kubectl apply -f "${dir}/"
done

# ──────────────────────────────────────────────────────────────────────────────
# Chờ và kiểm tra
# ──────────────────────────────────────────────────────────────────────────────
echo ""
echo "⏳ Chờ các pod khởi động (tối đa 120s)..."
kubectl rollout status deployment/prometheus    -n "${NAMESPACE}" --timeout=120s || true
kubectl rollout status deployment/grafana       -n "${NAMESPACE}" --timeout=120s || true

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Deploy hoàn tất!"
echo ""
echo "📋 Trạng thái pods:"
kubectl get pods -n "${NAMESPACE}" -o wide

echo ""
echo "🌐 Truy cập Grafana:"
GRAFANA_PORT=$(kubectl get svc grafana -n "${NAMESPACE}" -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "?")
NODE_IP=$(kubectl get node "${NODE_NAME}" -o jsonpath='{.status.addresses[?(@.type=="InternalIP")].address}' 2>/dev/null || echo "<NODE_IP>")
echo "   http://${NODE_IP}:${GRAFANA_PORT}"
echo "   (hoặc qua Tailscale IP của node)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
