# Migration lên K3s

Stack đã được migrate từ Docker Compose sang K3s Kubernetes.

## Kiến trúc

```
Cluster K3s (heterogeneous: VPS + iMac Ubuntu + HP Debian)
│
├─ Node: imac-ubuntu  [label: sensors-hub=enabled, role=monitoring]
│  ├─ DaemonSet: sensors-collector       → /var/lib/sensors-hub/textfile/thermal_sensors.prom
│  ├─ DaemonSet: sensors-net-collector   → /var/lib/sensors-hub/textfile/network_metrics.prom
│  ├─ DaemonSet: sensors-node-exporter  (đọc textfile ↑, expose :9100)
│  ├─ Deployment: prometheus            (scrape node-exporter, PVC local-path)
│  ├─ Deployment: grafana               (PVC local-path, NodePort)
│  └─ Deployment: grafana-renderer      (ClusterIP)
│
├─ Node: vps-ubuntu   [không có label thermal] → không có DaemonSet collector
└─ Node: hp-debian    [không có label thermal] → không có DaemonSet collector
```

## Label hệ thống

| Label | Node | Ý nghĩa |
|---|---|---|
| `sensors-hub=enabled` | Node có phần cứng cần monitor | DaemonSet collectors chạy ở đây |
| `role=monitoring` | Node chạy Prometheus + Grafana | Deployment central chạy ở đây |

Có thể gắn cả 2 label cho cùng 1 node (như iMac).

## Cấu trúc thư mục

```
k3s/
├─ 00-base/
│  ├─ 00-namespace.yaml         # Namespace: sensors-hub
│  └─ 01-secret.yaml            # Template secret (sửa trước khi apply)
├─ 10-prometheus/
│  ├─ 00-rbac.yaml              # ClusterRole + ServiceAccount
│  ├─ 01-configmap.yaml         # prometheus.yml với K8s SD
│  └─ 02-deployment.yaml        # Deployment + PVC + Service
├─ 20-renderer/
│  └─ 00-deployment.yaml        # Image Renderer Deployment
├─ 30-grafana/
│  └─ 00-deployment.yaml        # Deployment + PVC + ConfigMaps + NodePort
├─ 40-collectors/
│  ├─ 00-collector.yaml         # DaemonSet: sensors-collector
│  ├─ 01-net-collector.yaml     # DaemonSet: net-collector (hostNetwork)
│  └─ 02-node-exporter.yaml     # DaemonSet: node-exporter
└─ scripts/
   ├─ build-images.sh           # Build + import images vào k3s containerd
   └─ deploy.sh                 # Deploy toàn bộ stack
```

> **Thứ tự apply**: Prefix số đảm bảo `kubectl apply -f <dir>/` luôn chạy đúng thứ tự alphabetical:  
> `00-base` → `10-prometheus` → `20-renderer` → `30-grafana` → `40-collectors`


## Hướng dẫn deploy

### Bước 1: Build images trên node đích (iMac)

SSH vào node iMac, clone repo, rồi:

```bash
# Trên node iMac
cd /path/to/thermal-sensors
./k3s/scripts/build-images.sh
```

> Script tự phát hiện `nerdctl` (k3s native) hoặc `docker` rồi build + import.

### Bước 2: Deploy từ máy có kubectl

```bash
# Từ bất kỳ máy nào có kubectl kết nối cluster
./k3s/scripts/deploy.sh imac-ubuntu
```

Script sẽ:
1. Gắn labels `sensors-hub=enabled` + `role=monitoring` cho node
2. Hỏi Grafana admin password (không lưu vào file)
3. Tạo ConfigMap từ dashboard JSON tự động
4. Apply tất cả manifests theo đúng thứ tự

### Bước 3: Truy cập

```bash
# Xem NodePort của Grafana
kubectl get svc grafana -n sensors-hub

# Lấy IP của node iMac
kubectl get node imac-ubuntu -o wide
```

Truy cập: `http://<TAILSCALE_IP_iMac>:<NodePort>`

---

## Collector Universal

`collector/collect_sensors.py` đã được nâng cấp để **tự động phát hiện chip**:

| Role | Chip ưu tiên (theo thứ tự) |
|---|---|
| CPU temp | `coretemp-` → `k10temp-` (AMD) → `cpu_thermal-` |
| GPU temp | `amdgpu-` → `nouveau-` → `nvidia-` → `i915-` |
| PCH/Chipset | `pch_cannonlake-` → `pch_cometlake-` → ... → `pch-` |
| NVMe | `nvme-` (multi-drive: nvme0, nvme1...) |
| Apple SMC | `applesmc-` (chỉ iMac/MacBook) |
| Fallback | `acpitz-` (khi không có chip chuyên dụng) |

> Máy HP Debian sẽ vẫn collect được CPU/GPU/NVMe qua k10temp hoặc coretemp — chỉ Apple SMC metrics sẽ trống (graceful).

---

## Thêm node mới (horizontal scaling)

Khi muốn monitor thêm 1 iMac khác:

```bash
# 1. Build images trên node mới
ssh user@imac2 "cd /path/to/thermal-sensors && ./k3s/scripts/build-images.sh"

# 2. Label node mới (chỉ collector, không cần Prometheus/Grafana riêng)
kubectl label node imac2 sensors-hub=enabled

# DaemonSet tự động schedule pod collector lên node mới
# Prometheus tự discovery qua kubernetes_sd → label node=imac2 trong Grafana
```

---

## Dọn dẹp

```bash
./k3s/scripts/deploy.sh --purge
```
