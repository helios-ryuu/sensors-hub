#!/usr/bin/env bash
# ==============================================================================
# build-images.sh — Build Docker images cho thermal-collector & net-collector
# Chạy trực tiếp trên từng node sẽ deploy DaemonSet
#
# Cách dùng:
#   ./k3s/scripts/build-images.sh
#
# Script sẽ build images bằng docker hoặc nerdctl (containerd của k3s)
# rồi import vào k3s containerd nếu cần.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

COLLECTOR_IMAGE="thermal-collector:latest"
NET_COLLECTOR_IMAGE="thermal-net-collector:latest"

# Phát hiện runtime: ưu tiên nerdctl (k3s), fallback docker
if command -v nerdctl &>/dev/null; then
    RUNTIME="nerdctl"
    NERDCTL_NS="--namespace k8s.io"
elif command -v docker &>/dev/null; then
    RUNTIME="docker"
    NERDCTL_NS=""
else
    echo "❌ Không tìm thấy docker hoặc nerdctl. Hãy cài một trong hai."
    exit 1
fi

echo "🔧 Sử dụng runtime: ${RUNTIME}"
echo "📁 Project root: ${PROJECT_ROOT}"

build_and_import() {
    local context_dir="$1"
    local image_name="$2"
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "🏗️  Build: ${image_name}"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [[ "${RUNTIME}" == "nerdctl" ]]; then
        # nerdctl build thẳng vào k8s.io namespace
        nerdctl ${NERDCTL_NS} build \
            --platform linux/amd64 \
            -t "${image_name}" \
            "${context_dir}"
    else
        # docker build rồi import sang k3s containerd
        docker build \
            --platform linux/amd64 \
            -t "${image_name}" \
            "${context_dir}"

        if command -v k3s &>/dev/null; then
            echo "📦 Import ${image_name} vào k3s containerd..."
            docker save "${image_name}" | k3s ctr images import -
            echo "✅ Import hoàn tất"
        else
            echo "⚠️  k3s không tìm thấy — bỏ qua bước import containerd."
            echo "   Nếu cluster dùng containerd, chạy thủ công:"
            echo "   docker save ${image_name} | k3s ctr images import -"
        fi
    fi
    echo "✅ ${image_name} đã sẵn sàng"
}

build_and_import "${PROJECT_ROOT}/collector"     "${COLLECTOR_IMAGE}"
build_and_import "${PROJECT_ROOT}/net-collector" "${NET_COLLECTOR_IMAGE}"

echo ""
echo "🎉 Build hoàn tất! Tiếp theo chạy: ./k3s/scripts/deploy.sh"
