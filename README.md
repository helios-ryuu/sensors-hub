# Giám sát phần cứng, hệ thống & an ninh mạng iMac bằng Grafana & Docker Compose

Stack giám sát toàn diện, nhẹ và đóng gói **100% bằng Docker Compose** cho máy Ubuntu chạy trên iMac. Hiển thị lịch sử nhiệt độ (CPU, GPU, PCH, NVMe, quạt Apple SMC, nhiệt độ phòng ~TA0V), toàn bộ tài nguyên hệ thống (CPU, RAM, Disk I/O, Network, TCP), an ninh mạng & tường lửa và truy cập an toàn qua Tailscale.

Stack đi kèm **4 Dashboards Grafana** được nạp sẵn:
1. **Cảm biến nhiệt độ iMac (`imac-thermal`)**: Chi tiết nhiệt độ các linh kiện, nhiệt độ phòng động học (`TA0V` bù trừ RPM và nhiệt CPU/GPU), tốc độ quạt, công suất GPU và cấu hình `mbpfan`.
1. **Cảm biến nhiệt độ iMac (`imac-thermal`)**: Chi tiết nhiệt độ các linh kiện, nhiệt độ phòng động học (`TA0V` bù trừ quán tính vi phân, phân tầng nhiệt và nhiệt tải CPU/GPU), tốc độ quạt, công suất GPU và cấu hình `mbpfan`.
2. **Tài nguyên hệ thống (`system-resources`)**: Tải CPU (chi tiết theo mode), Load Average, phân bổ RAM, Disk I/O & IOPS, băng thông mạng & lỗi/drop gói.
3. **Sức khỏe hệ thống (`system-health`)**: Uptime, số nhân CPU, tổng dung lượng RAM, phiên bản Kernel, trạng thái kết nối TCP (ESTABLISHED, TIME_WAIT...), TCP Retransmissions, Inode usage và bảng dung lượng Filesystem.
4. **Mạng & Bảo mật (`network-security`)**: Giám sát CGNAT, Symmetric NAT, Double NAT, Topology các card mạng, kho thiết bị hợp nhất (LAN + Tailscale), các cổng mở lắng nghe, quy tắc tường lửa `iptables` và thiết lập bảo mật kernel.

---

## 🏗️ Kiến trúc hệ thống

```text
Host Network & Hardware (/proc, /sys, /var/run/tailscale/tailscaled.sock, iptables)
       │ (network_mode: host, read-only bind-mounts)
       ▼
┌────────────────────────────────────────────────────────┐
│ Docker Compose Network (sensors-hub)            │
│                                                        │
│  ┌──────────────────────┐  ┌──────────────────────┐   │
│  │  sensors-collector   │  │    net-collector     │   │
│  │(Sensors & mbpfan 10s)│  │(Network & Sec 15s/2m)│   │
│  └──────────┬───────────┘  └──────────┬───────────┘   │
│             │                         │               │
│             ▼                         ▼               │
│      thermal_sensors.prom      network_metrics.prom   │
│             │                         │               │
│             └───────────┬─────────────┘               │
│                         ▼ (Thư mục ~/.sens/textfile/) │
│             ┌───────────────────────┐                 │
│             │     node-exporter     │                 │
│             └───────────┬───────────┘                 │
│                         ▼                             │
│             ┌───────────────────────┐                 │
│             │  sensors-prometheus   │                 │
│             └───────────┬────────────┘                │
│                         ▼                             │
│             ┌───────────────────────┐                 │
│             │    thermal-grafana    │                 │
│             │     (4 Dashboards)    │                 │
│             └───────────┬───────────┘                 │
└─────────────────────────┼─────────────────────────────┘
                          ▼
                  Tailscale Network
                  (<TAILSCALE_IP>:3000)
```

* **Dữ liệu tập trung (`~/.sens/`)**:
  * `~/.sens/textfile/`: Chứa file metric trung gian `thermal_sensors.prom` và `network_metrics.prom`.
  * `~/.sens/prometheus/`: Cơ sở dữ liệu time-series TSDB của Prometheus.
  * `~/.sens/grafana/`: Dữ liệu SQLite và cấu hình của Grafana.

---

## ⚡ Bắt đầu nhanh (1-Click)

### 1. Dọn dẹp bản cũ trên host (nếu đã từng cài phiên bản cũ)
```bash
./scripts/cleanup-legacy.sh
```

### 2. Cấu hình & Khởi tạo stack (1-Click)
1. Tạo file `.env` từ `.env.example` và điền `GRAFANA_ADMIN_PASSWORD`:
   ```bash
   cp .env.example .env
   nano .env
   ```
2. Chạy setup:
   ```bash
   ./scripts/setup.sh
   ```
* Dashboard Grafana (qua Tailscale): `http://<TAILSCALE_IP>:3000` (hoặc IP cấu hình trong `.env`).
* Prometheus (nội bộ host): `http://127.0.0.1:9090`.

---

## ⚙️ Cấu hình tùy chỉnh (.env)

| Biến môi trường | Mặc định | Ý nghĩa |
| --- | --- | --- |
| `SENS_DATA_DIR` | `/home/user/.sens` | Thư mục lưu trữ tập trung dữ liệu |
| `GRAFANA_ADMIN_USER` | `admin` | Tên đăng nhập Admin Grafana |
| `GRAFANA_ADMIN_PASSWORD` | `...` | Mật khẩu Admin Grafana (cấu hình trong `.env`) |
| `GRAFANA_BIND_IP` | `0.0.0.0` | IP publish của Grafana (Mặc định `0.0.0.0` để lắng nghe Tailscale & LAN, hoặc điền IP Tailscale để giới hạn) |
| `GRAFANA_PORT` | `3000` | Port truy cập Grafana |
| `PROMETHEUS_BIND_IP`| `127.0.0.1` | IP publish của Prometheus |
| `PROMETHEUS_PORT` | `9090` | Port truy cập Prometheus |
| `COLLECTOR_INTERVAL`| `10` | Chu kỳ thu thập cảm biến của thermal collector (giây) |
| `NET_COLLECTOR_FAST_INTERVAL`| `15` | Chu kỳ thu thập nhanh của net-collector (giây) |
| `NET_COLLECTOR_SLOW_INTERVAL`| `120` | Chu kỳ kiểm tra NAT/STUN/Traceroute của net-collector (giây) |
| `PROMETHEUS_RETENTION_TIME` | `15d` | Thời gian lưu trữ dữ liệu metric |
| `PROMETHEUS_RETENTION_SIZE` | `2GB` | Dung lượng tối đa của Prometheus TSDB |

---

## 🌡️ Thuật toán & Công thức Ước tính Nhiệt độ Phòng

Stack giám sát sử dụng trực tiếp cảm biến phần cứng **Apple SMC `TA0V`** (nằm ở khe hút khí nạp dưới đáy màn hình iMac) kết hợp với mô hình nhiệt động học vi phân (**Dynamic Thermal Gradient Model**) trong [collect_sensors.py] để ước tính nhiệt độ phòng làm việc theo thời gian thực mà không cần gắn thêm cảm biến IoT rời bên ngoài.

### 1. Nguyên lý vật lý
* **Vị trí hút khí nạp ($T_{\text{intake}}$)**: Khe hút gió đáy iMac liên tục hút không khí từ môi trường phòng vào buồng máy qua quạt làm mát. Cảm biến `TA0V` ghi nhận giá trị này trước khi luồng khí đi qua các linh kiện phát nhiệt chính (CPU, GPU, Heatsink).
* **Các yếu tố gây sai lệch vật lý cần bù trừ**:
  1. **Quán tính nhiệt (Thermal inertia)**: Cảm biến nhiệt (thermistor) gắn trên linh kiện có hằng số trễ nhiệt ($\tau \approx 0.5\text{ phút}$) khi nhiệt độ môi trường thay đổi đột ngột (ví dụ khi bật/tắt điều hòa).
  2. **Phân tầng khí lạnh theo độ cao (Cold air stratification / Buoyancy)**: Luồng khí lạnh từ điều hòa có khối lượng riêng lớn hơn nên chìm sát mặt bàn (vị trí khe hút gió đáy iMac). Không khí ở độ cao ngang tầm người ngồi làm việc thường cao hơn từ $0.0^\circ\text{C}$ đến $0.4^\circ\text{C}$.
  3. **Dẫn nhiệt qua vỏ nhôm khi tải nặng (Conductive heat leakage)**: Khi CPU hoặc GPU tải nặng ($> 55^\circ\text{C}$), nhiệt lượng từ khối tản nhiệt nhôm có thể truyền dẫn một phần nhỏ làm ấm vùng vỏ đáy gần cảm biến nạp.

---

### 2. Mô hình toán học & Công thức tổng quát

Nhiệt độ phòng ước tính ($T_{\text{ambient}}$) được tính toán theo công thức:

$$T_{\text{ambient}} = T_{\text{intake}} + \Delta_{\text{dynamic}}$$

Trong đó:
* $T_{\text{intake}} = \text{round}(\text{TA0V}, 1)$ (đo trực tiếp từ cảm biến `TA0V`).
* $\Delta_{\text{dynamic}}$ là độ lệch bù trừ nhiệt động học tổng hợp, được kẹp an toàn tuyệt đối trong khoảng $[-0.6^\circ\text{C}, +0.6^\circ\text{C}]$:

$$\Delta_{\text{dynamic}} = \operatorname{clamp}\left(\text{gradient\_adj} + \text{strat\_adj} - \text{load\_penalty}, -0.6^\circ\text{C}, +0.6^\circ\text{C}\right)$$

---

### 3. Chi tiết các thành phần bù trừ

#### a. Bù quán tính trễ vi phân ($\text{gradient\_adj}$)
Tính tốc độ biến thiên nhiệt độ nạp $dT/dt$ trên cửa sổ trượt tối đa 20 mẫu trong vòng $300\text{s}$ ($\Delta t \ge 10\text{s}$):

$$\frac{dT}{dt} = \frac{T_{\text{intake}}(t) - T_{\text{intake}}(t - \Delta t)}{\Delta t / 60} \quad (^\circ\text{C}/\text{phút})$$

Từ đó tính thành phần bù vi phân (giới hạn chặt trong khoảng $[-0.3^\circ\text{C}, +0.3^\circ\text{C}]$):

$$\text{gradient\_adj} = \operatorname{clamp}\left(0.5 \times \frac{dT}{dt}, -0.3^\circ\text{C}, +0.3^\circ\text{C}\right)$$

* **Ý nghĩa**: Khi phòng tăng nhiệt nhanh ($dT/dt > 0$), cảm biến phản ứng trễ hơn không khí bên ngoài $\rightarrow$ cộng bù dương; khi phòng lạnh nhanh ($dT/dt < 0$) $\rightarrow$ trừ bù âm.

#### b. Bù phân tầng khí lạnh theo độ cao ($\text{strat\_adj}$)
Bù chênh lệch nhiệt độ giữa mặt bàn và tầm người ngồi dựa theo nhiệt độ khí nạp qua hàm chuyển tiếp tuyến tính:

$$\text{strat\_adj} = \begin{cases} 
0.4^\circ\text{C} & \text{khi } T_{\text{intake}} \le 21.5^\circ\text{C} \\ 
0.0^\circ\text{C} & \text{khi } T_{\text{intake}} \ge 23.5^\circ\text{C} \\ 
0.4 \times \left(\dfrac{23.5 - T_{\text{intake}}}{2.0}\right) & \text{khi } 21.5^\circ\text{C} < T_{\text{intake}} < 23.5^\circ\text{C} 
\end{cases}$$

* **Ý nghĩa**: Khi phòng bật điều hòa mát ($\le 21.5^\circ\text{C}$), lớp không khí lạnh sát đáy chênh lệch khoảng $+0.4^\circ\text{C}$ so với phòng. Khi nhiệt độ phòng ấm ($\ge 23.5^\circ\text{C}$), hiện tượng phân tầng triệt tiêu dần về $0.0^\circ\text{C}$.

#### c. Giảm trừ nhiệt dẫn truyền khung vỏ khi tải nặng ($\text{load\_penalty}$)
Khi CPU Package hoặc GPU Edge vượt ngưỡng $55^\circ\text{C}$, áp dụng mức phạt nhiệt tối đa $0.2^\circ\text{C}$:

$$\text{penalty} = \max(0, (T_{\text{CPU}} - 55) \times 0.01) + \max(0, (T_{\text{GPU}} - 55) \times 0.01)$$

$$\text{load\_penalty} = \min(0.2^\circ\text{C}, \text{penalty})$$

* **Ý nghĩa**: Ngăn chặn hiện tượng CPU/GPU render hoặc stress nặng làm "ảo" nhiệt độ phòng ước tính do nhiệt vỏ lan tới cảm biến.

---

### 4. Thuật toán Fallback an toàn (Khi mất cảm biến `TA0V`)

Trường hợp cảm biến `TA0V` bị lỗi, mất kết nối hoặc trả về giá trị ngoại lai ($T \le 0^\circ\text{C}$ hoặc $T \ge 125^\circ\text{C}$):
1. Thu thập tất cả các cảm biến nhiệt độ Apple SMC khả dụng có giá trị nằm trong dải $[15^\circ\text{C}, 60^\circ\text{C}]$ (các cảm biến nhiệt tiệm cận như `TC0p`, `TG0p`, `TL0p`...).
2. Lấy giá trị thấp nhất và trừ đi offset chênh lệch cấu trúc linh kiện $4.5^\circ\text{C}$:

$$T_{\text{ambient\_fallback}} = \min(T_{\text{candidates}}) - 4.5^\circ\text{C}$$

---

### 5. Định dạng Metrics Prometheus xuất ra

| Tên Metric | Type | Đơn vị | Mô tả |
| --- | --- | --- | --- |
| `thermal_inlet_air_temperature_celsius` | Gauge | °C | Nhiệt độ khí nạp đo trực tiếp tại đáy iMac từ cảm biến `TA0V` |
| `thermal_room_temperature_celsius` | Gauge | °C | Nhiệt độ phòng ước tính theo mô hình nhiệt động học (`Dynamic Thermal Gradient`) |
| `thermal_estimated_ambient_temperature_celsius` | Gauge | °C | Alias tương thích ngược của `thermal_room_temperature_celsius` |

---

## 🛠️ Quản lý & Dọn dẹp

* **Xem thông tin truy cập & metadata dữ liệu (Cảm biến + Mạng + Dung lượng):**
  ```bash
  ./scripts/info.sh
  ```

* **Xem trạng thái container / log:**
  ```bash
  docker compose ps
  docker compose logs -f net-collector
  ```

* **Dừng stack:**
  ```bash
  ./scripts/clean.sh
  ```

* **Dừng stack và xóa sạch toàn bộ dữ liệu (`~/.sens`):**
  ```bash
  ./scripts/clean.sh --purge
  ```
