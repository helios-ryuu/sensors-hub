import io
import os
import tempfile
import unittest
from unittest import mock

import collect_network


class NetworkCollectorTests(unittest.TestCase):
    def setUp(self):
        collect_network._metrics_cache = {
            "interfaces": {"data": None, "last": 0.0},
            "ports": {"data": None, "last": 0.0},
            "connections": {"counts": None, "active": None, "last": 0.0},
            "firewall": {"policies": None, "rules": None, "last": 0.0},
            "device_fps": {"data": None, "last": 0.0},
        }

    def test_rfc1918_private_ips(self):
        self.assertTrue(collect_network.is_rfc1918_private("192.168.1.1"))
        self.assertTrue(collect_network.is_rfc1918_private("10.0.0.1"))
        self.assertTrue(collect_network.is_rfc1918_private("172.16.5.10"))
        self.assertFalse(collect_network.is_rfc1918_private("8.8.8.8"))
        self.assertFalse(collect_network.is_rfc1918_private("1.1.1.1"))
        self.assertFalse(collect_network.is_rfc1918_private("100.64.0.1"))
        self.assertFalse(collect_network.is_rfc1918_private("127.0.0.1"))

    def test_rfc6598_cgnat_ips(self):
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.64.0.1"))
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.100.50.1"))
        self.assertTrue(collect_network.is_rfc6598_cgnat("100.127.255.254"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("192.168.1.1"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("8.8.8.8"))
        self.assertFalse(collect_network.is_rfc6598_cgnat("invalid-ip"))

    def test_prometheus_escape(self):
        self.assertEqual(collect_network.prometheus_escape('hello "world"'), 'hello \\"world\\"')
        self.assertEqual(collect_network.prometheus_escape("line1\nline2"), "line1 line2")
        self.assertEqual(collect_network.prometheus_escape("back\\slash"), "back\\\\slash")

    def test_add_sample_with_and_without_labels(self):
        lines = []
        collect_network.add_sample(lines, "test_metric", 42)
        self.assertIn("test_metric 42", lines)

        collect_network.add_sample(lines, "test_metric_labels", 1, {"foo": "bar", "num": "10"})
        self.assertIn('test_metric_labels{foo="bar",num="10"} 1', lines)

    def test_tailscale_peer_parsing(self):
        mock_status = {
            "Self": {
                "HostName": "node-host",
                "TailscaleIPs": ["100.64.1.1"],
                "Online": True,
                "OS": "linux",
            },
            "Peer": {
                "node1": {
                    "HostName": "laptop",
                    "TailscaleIPs": ["100.64.1.2"],
                    "Online": True,
                    "CurAddr": "192.168.1.15:41641",
                    "OS": "macos",
                },
                "node2": {
                    "HostName": "phone",
                    "TailscaleIPs": ["100.64.1.3"],
                    "Online": False,
                    "CurAddr": "",
                    "Relay": "sgp",
                    "OS": "ios",
                },
            },
        }

        lines = []
        with mock.patch.object(collect_network, "query_tailscale_localapi", return_value=mock_status):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any("net_tailscale_status" in l and 'hostname="node-host"' in l for l in lines))
        self.assertTrue(any('network="Tailscale"' in l and 'name="laptop"' in l and 'link_type="direct"' in l for l in lines))
        self.assertTrue(any('network="Tailscale"' in l and 'name="phone"' in l and 'status="offline"' in l for l in lines))

    def test_arp_neighbors_parsing(self):
        mock_arp = [
            {"ip": "192.168.1.1", "mac": "00:11:22:33:44:55", "dev": "enp3s0", "reachable": True},
            {"ip": "192.168.1.100", "mac": "aa:bb:cc:dd:ee:ff", "dev": "enp3s0", "reachable": False},
        ]
        lines = []
        with (
            mock.patch.object(collect_network, "get_arp_neighbors", return_value=mock_arp),
            mock.patch.object(collect_network, "get_default_gateway_and_routes", return_value=("192.168.1.1", "enp3s0")),
        ):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any('network="LAN"' in l and 'ip="192.168.1.1"' in l and 'identifier="00:11:22:33:44:55"' in l and 'status="reachable"' in l for l in lines))
        self.assertTrue(any('network="LAN"' in l and 'ip="192.168.1.100"' in l and 'status="stale"' in l for l in lines))
        self.assertTrue(any("net_gateway_info" in l and 'gateway_ip="192.168.1.1"' in l for l in lines))

    def test_open_ports_and_connections(self):
        mock_ports = [
            {"proto": "tcp", "port": "22", "bind_ip": "0.0.0.0", "exposure": "Public / LAN", "service": "sshd"},
            {"proto": "tcp", "port": "3000", "bind_ip": "100.64.1.1", "exposure": "Tailscale Only", "service": "grafana"},
        ]
        mock_states = {"ESTAB": 5, "TIME_WAIT": 2}
        mock_conns = [
            {"proto": "tcp", "local_port": "3000", "remote_ip": "100.64.1.2", "remote_port": "54321", "state": "ESTABLISHED"}
        ]
        lines = []
        with (
            mock.patch.object(collect_network, "get_open_listening_ports", return_value=mock_ports),
            mock.patch.object(collect_network, "get_active_connections", return_value=(mock_states, mock_conns)),
        ):
            collect_network.collect_network_metrics(lines)

        self.assertTrue(any("net_listening_port_info" in l and 'port="22"' in l and 'exposure="Public / LAN"' in l for l in lines))
        self.assertTrue(any("net_listening_port_info" in l and 'port="3000"' in l and 'exposure="Tailscale Only"' in l for l in lines))
        self.assertTrue(any('net_active_connections_total{state="ESTAB"} 5' in l for l in lines))
        self.assertTrue(any("net_active_connection_info" in l and 'local_port="3000"' in l for l in lines))

    def test_collect_once_writes_atomic_file(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.exists(temp_dir) and os.system(f"rm -rf {temp_dir}"))
        target_path = os.path.join(temp_dir, "test_network.prom")

        ret = collect_network.collect_once(output_path=target_path)
        self.assertEqual(ret, 0)
        self.assertTrue(os.path.exists(target_path))

        with open(target_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("net_collector_success 1", content)
        self.assertIn("net_nat_is_cgnat", content)
    def test_identify_mac_vendor(self):
        self.assertEqual(collect_network.identify_mac_vendor("10:51:07:94:68:7b"), "Apple Inc.")
        self.assertEqual(collect_network.identify_mac_vendor("e4:0d:36:cd:1e:3c"), "Apple Inc.")
        self.assertEqual(collect_network.identify_mac_vendor("04:5f:a6:8f:1c:6d"), "ZTE / ISP Gateway")
        self.assertEqual(collect_network.identify_mac_vendor("06:d1:50:f2:9b:0a"), "Private Wi-Fi (Apple/Android)")
        self.assertEqual(collect_network.identify_mac_vendor("ee:4d:50:9c:2d:79"), "Private Wi-Fi (Apple/Android)")

    def test_resolve_device_name(self):
        self.assertEqual(collect_network.resolve_device_name("192.168.1.1", gateway_ip="192.168.1.1"), "Default Gateway (Router)")
        name = collect_network.resolve_device_name("192.168.1.100", vendor="Apple Inc.")
        self.assertEqual(name, "Apple-Device-100")

    def test_resolve_port_service(self):
        # Khi có tên tiến trình thực tế
        self.assertEqual(collect_network.resolve_port_service("41641", "udp", "tailscaled"), "tailscaled")
        self.assertEqual(collect_network.resolve_port_service("10250", "tcp", "kubelet"), "kubelet")
        
        # Khi tiến trình bị rỗng (-) hoặc rỗng, dùng bảng tra cứu WELL_KNOWN_SERVICES
        self.assertEqual(collect_network.resolve_port_service("53", "tcp", "-"), "systemd-resolved (DNS)")
        self.assertEqual(collect_network.resolve_port_service("53", "udp", ""), "systemd-resolved (DNS)")
        self.assertEqual(collect_network.resolve_port_service("10250", "tcp", "-"), "kubelet (K8s API)")
        self.assertEqual(collect_network.resolve_port_service("41641", "udp", "-"), "tailscale (DERP/WireGuard)")
        self.assertEqual(collect_network.resolve_port_service("3000", "tcp", "-"), "grafana")

    def test_multi_target_latency_emission(self):
        collect_network.slow_cache["latencies"] = {
            "gateway": {"val": 0.8, "service": "Gateway", "category": "local_network"},
            "vn_viettel": {"val": 3.2, "service": "Viettel DNS", "category": "domestic_vn"},
            "cloudflare": {"val": 25.1, "service": "Cloudflare (1.1.1.1)", "category": "global_dns"},
            "youtube": {"val": 12.5, "service": "YouTube", "category": "media_service"},
        }
        lines = []
        collect_network.collect_network_metrics(lines)
        self.assertTrue(any('net_ping_latency_ms{category="local_network",protocol="icmp",service="Gateway",target="gateway"} 0.8' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="domestic_vn",protocol="icmp",service="Viettel DNS",target="vn_viettel"} 3.2' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="global_dns",protocol="icmp",service="Cloudflare (1.1.1.1)",target="cloudflare"} 25.1' in l for l in lines))
        self.assertTrue(any('net_ping_latency_ms{category="media_service",protocol="icmp",service="YouTube",target="youtube"} 12.5' in l for l in lines))

    def test_get_open_listening_ports_ss_parsing(self):
        mock_ss_output = """tcp LISTEN 0 128 0.0.0.0:10250 0.0.0.0:* users:(("kubelet",pid=800,fd=10))
udp UNCONN 0 0 127.0.0.53%lo:53 0.0.0.0:* users:(("systemd-resolve",pid=320,fd=12))
udp UNCONN 0 0 0.0.0.0:41641 0.0.0.0:* -
tcp LISTEN 0 128 [::]:3000 [::]:* users:(("grafana",pid=1500,fd=7))
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=mock_ss_output)
            ports = collect_network.get_open_listening_ports()

        self.assertEqual(len(ports), 4)
        
        # 10250 kubelet
        p_10250 = next(p for p in ports if p["port"] == "10250")
        self.assertEqual(p_10250["service"], "kubelet")
        self.assertEqual(p_10250["exposure"], "Public / LAN")

        # 53 systemd-resolved on loopback %lo
        p_53 = next(p for p in ports if p["port"] == "53")
        self.assertEqual(p_53["service"], "systemd-resolve")
        self.assertEqual(p_53["exposure"], "Localhost Only")

        # 41641 tailscale (khi ss trả về `-`, fallback sang WELL_KNOWN_SERVICES)
        p_41641 = next(p for p in ports if p["port"] == "41641")
        self.assertEqual(p_41641["service"], "tailscale (DERP/WireGuard)")
        self.assertEqual(p_41641["exposure"], "Public / LAN")

        # 3000 grafana on ::
        p_3000 = next(p for p in ports if p["port"] == "3000")
        self.assertEqual(p_3000["service"], "grafana")
        self.assertEqual(p_3000["exposure"], "Public / LAN")

    def test_detect_double_nat_and_cgnat_hop_index_and_drop(self):
        # Case 1: Hop 1 và Hop 2 đều là private RFC1918 -> Double NAT
        with mock.patch.object(collect_network, "run_traceroute_hops", return_value={1: "192.168.2.1", 2: "192.168.1.1"}):
            d_nat, cgnat = collect_network.detect_double_nat_and_cgnat("192.168.2.1")
            self.assertEqual(d_nat, 1)
            self.assertEqual(cgnat, 0)

        # Case 2: Hop 2 bị router ZTE/Huawei drop (*), nhưng Hop 1 và Hop 3 đều là private -> Double NAT
        with mock.patch.object(collect_network, "run_traceroute_hops", return_value={1: "192.168.2.1", 2: "*", 3: "192.168.1.1"}):
            d_nat, cgnat = collect_network.detect_double_nat_and_cgnat("192.168.2.1")
            self.assertEqual(d_nat, 1)
            self.assertEqual(cgnat, 0)

        # Case 3: Hop 2 bị drop (*), nhưng Hop 3 là CGNAT 100.64.x.x -> CGNAT
        with mock.patch.object(collect_network, "run_traceroute_hops", return_value={1: "192.168.2.1", 2: "*", 3: "100.64.10.1"}):
            d_nat, cgnat = collect_network.detect_double_nat_and_cgnat("192.168.2.1")
            self.assertEqual(d_nat, 0)
            self.assertEqual(cgnat, 1)

        # Case 4: Public IP nằm trong dải CGNAT (100.64.0.0/10)
        with mock.patch.object(collect_network, "run_traceroute_hops", return_value={1: "192.168.2.1", 2: "*", 3: "*"}):
            d_nat, cgnat = collect_network.detect_double_nat_and_cgnat("192.168.2.1", public_ip="100.100.50.1")
            self.assertEqual(cgnat, 1)

    def test_fingerprint_all_devices_async(self):
        mock_devices = [
            {"ip": "192.168.1.1", "mac": "00:11:22:33:44:55", "reachable": True},
            {"ip": "192.168.1.20", "mac": "aa:bb:cc:dd:ee:ff", "reachable": True},
        ]
        # Xóa cache trước khi test
        collect_network._device_fingerprint_cache.clear()
        
        with mock.patch.object(collect_network, "_probe_ip_ports", return_value=[80]):
            res = collect_network.fingerprint_all_devices(mock_devices, gateway_ip="192.168.1.1")
            self.assertIn("192.168.1.1", res)
            self.assertIn("192.168.1.20", res)
            self.assertEqual(res["192.168.1.1"]["device_type"], "Router / Gateway")

    def test_ping_target_multi_host(self):
        # Case 1: Host đầu tiên thất bại, host thứ hai thành công
        def mock_subprocess_run(cmd, **kwargs):
            target = cmd[-1]
            if target == "facebook.com":
                return mock.Mock(returncode=1, stdout="")
            elif target == "m.facebook.com":
                return mock.Mock(returncode=0, stdout="64 bytes from ...: time=45.2 ms\n64 bytes from ...: time=42.1 ms")
            return mock.Mock(returncode=1, stdout="")

        with mock.patch("subprocess.run", side_effect=mock_subprocess_run):
            lat = collect_network.ping_target(["facebook.com", "m.facebook.com"])
            self.assertEqual(lat, 42.1)

    def test_latency_cache_retention_on_drop(self):
        # Giả lập chu kỳ trước đo thành công Facebook
        collect_network.slow_cache["last_run"] = 0
        collect_network.slow_cache["latencies"] = {
            "facebook": {"val": 53.7, "service": "Facebook", "category": "media_service"}
        }
        collect_network.slow_cache["latency_fail_counts"] = {"facebook": 0}

        # Chu kỳ hiện tại: ping_target trả về None (rớt gói)
        with mock.patch.object(collect_network, "ping_target", return_value=None):
            lines = []
            collect_network.collect_network_metrics(lines)
            
            # Kiểm tra giá trị Facebook vẫn được bảo lưu (không bị biến mất khỏi metric)
            self.assertIn("facebook", collect_network.slow_cache["latencies"])
            self.assertEqual(collect_network.slow_cache["latencies"]["facebook"]["val"], 53.7)
            self.assertEqual(collect_network.slow_cache["latency_fail_counts"]["facebook"], 1)
            self.assertTrue(any('net_ping_latency_ms{category="media_service",protocol="icmp",service="Facebook",target="facebook"} 53.7' in l for l in lines))


    def test_tcp_handshake_probe(self):
        with mock.patch("socket.socket") as mock_sock_cls:
            mock_sock = mock.MagicMock()
            mock_sock_cls.return_value = mock_sock
            mock_sock.connect_ex.return_value = 0
            lat = collect_network.probe_tcp_handshake_ms("youtube.com", 443, timeout=1.0)
            self.assertIsNotNone(lat)
            self.assertGreaterEqual(lat, 0.0)

    def test_ping_target_advanced_parsing(self):
        sample_output = """PING youtube.com (142.250.66.78) 56(84) bytes of data.
64 bytes from hkg07s50-in-f14.1e100.net (142.250.66.78): icmp_seq=1 ttl=117 time=35.2 ms
64 bytes from hkg07s50-in-f14.1e100.net (142.250.66.78): icmp_seq=2 ttl=117 time=38.4 ms

--- youtube.com ping statistics ---
2 packets transmitted, 2 received, 0% packet loss, time 1001ms
rtt min/avg/max/mdev = 35.210/36.805/38.400/1.595 ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=sample_output)
            min_rtt, loss, jitter = collect_network.ping_target_advanced("youtube.com")
            self.assertEqual(min_rtt, 35.2)
            self.assertEqual(loss, 0.0)
            self.assertEqual(jitter, 1.595)

    def test_auto_diagnose_spike_and_metrics(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.exists(temp_dir) and os.system(f"rm -rf {temp_dir}"))
        sample_trace = """traceroute to youtube.com (142.250.66.78), 15 hops max
 1  192.168.1.1  0.512 ms
 2  10.0.0.1  2.145 ms
 3  203.162.0.1  185.420 ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=sample_trace)
            hop, ip, rtt = collect_network.auto_diagnose_spike("youtube.com", 245.0, "YouTube", spike_dir=temp_dir)
            self.assertEqual(hop, 3)
            self.assertEqual(ip, "203.162.0.1")
            self.assertEqual(rtt, 185.42)

            log_files = os.listdir(temp_dir)
            self.assertTrue(any(f.startswith("spike_youtube_") for f in log_files))

    def test_auto_diagnose_spike_edge_rate_limit(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.exists(temp_dir) and os.system(f"rm -rf {temp_dir}"))
        # Toàn bộ hop đều <100ms nhưng ping đo được 268ms -> Phải phát hiện là Edge/Target
        sample_trace = """traceroute to youtube.com (74.125.68.93), 15 hops max
 1  10.10.10.1  0.437 ms
 2  14.169.128.1  2.149 ms
 3  74.125.68.93  64.089 ms
"""
        with mock.patch("subprocess.run") as mock_run:
            mock_run.return_value = mock.Mock(returncode=0, stdout=sample_trace)
            hop, ip, rtt = collect_network.auto_diagnose_spike("youtube.com", 268.5, "YouTube", spike_dir=temp_dir)
            self.assertEqual(hop, 3)
            self.assertIn("74.125.68.93", ip)
            self.assertEqual(rtt, 268.5)

    def test_load_existing_spike_history(self):
        temp_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: os.path.exists(temp_dir) and os.system(f"rm -rf {temp_dir}"))
        sample_log = """=== SPIKE DIAGNOSTIC LOG ===
Service: YouTube
Target: youtube.com
Latency: 268.5 ms
Culprit Hop: #3 (74.125.68.93 (Edge/Target)) at 268.5 ms
Timestamp: 1790557308
"""
        with open(os.path.join(temp_dir, "spike_youtube_1790557308.log"), "w", encoding="utf-8") as f:
            f.write(sample_log)

        totals, culprits, ts = collect_network.load_existing_spike_history(spike_dir=temp_dir)
        self.assertEqual(totals.get("youtube"), 1)
        self.assertEqual(culprits.get("youtube")["culprit_hop"], "3")
        self.assertIn("74.125.68.93", culprits.get("youtube")["culprit_ip"])
        self.assertEqual(culprits.get("youtube")["culprit_rtt_ms"], "268.5")

if __name__ == "__main__":
    unittest.main()

