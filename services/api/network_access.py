"""局域网访问：监听地址解析、本机网卡地址枚举、Windows 防火墙辅助。

这个模块只做系统层面的探测，不直接改服务端监听；绑定/重新绑定的生命周期在
services.api.main 里。拆出来是为了让设置 API、启动日志和测试都能复用同一套
"当前到底该监听哪个地址" 的判断。
"""

from __future__ import annotations

import ctypes
import ipaddress
import os
import socket
import struct
import subprocess
import sys
import threading
import time
from typing import Any

from services.api import settings
from services.api.subprocess_utils import no_console_window_kwargs


# API_HOST 的默认值是回环地址；显式写成 0.0.0.0 / 具体网卡 IP 时视为启动参数
# 已经决定了监听地址，设置页不再允许用开关覆盖它。
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})
FIREWALL_RULE_PREFIX = "AI Listing System API (LAN"
_FIREWALL_CACHE_TTL_SECONDS = 30.0
_FIREWALL_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}
_FIREWALL_CACHE_LOCK = threading.Lock()

# GetAdaptersAddresses 常量
_AF_UNSPEC = 0
_AF_INET = 2
_ERROR_BUFFER_OVERFLOW = 111
_ERROR_NO_DATA = 232
_GAA_FLAG_SKIP_ANYCAST = 0x0002
_GAA_FLAG_SKIP_MULTICAST = 0x0004
_GAA_FLAG_SKIP_DNS_SERVER = 0x0008
_IF_TYPE_SOFTWARE_LOOPBACK = 24
_IF_TYPE_TUNNEL = 131
_IF_OPER_STATUS_UP = 1


def normalize_bind_host(value: Any) -> str:
    """把监听地址归一化，便于比较（去空白、小写、去掉 IPv6 方括号）。"""

    host = str(value or "").strip().lower()
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    return host


def is_loopback_host(value: Any) -> bool:
    return normalize_bind_host(value) in LOOPBACK_HOSTS


def is_wildcard_host(value: Any) -> bool:
    return normalize_bind_host(value) in WILDCARD_HOSTS


def is_lan_bind_host(value: Any) -> bool:
    """这个监听地址是否能让局域网里的其它电脑连进来。"""

    host = normalize_bind_host(value)
    if not host:
        return False
    if is_wildcard_host(host):
        return True
    return not is_loopback_host(host)


def env_bind_override() -> str:
    """启动脚本用 API_HOST 指定的非回环地址；空字符串表示没有覆盖。"""

    host = normalize_bind_host(settings.API_HOST)
    if not host or is_loopback_host(host):
        return ""
    # 应用只枚举 IPv4；API_HOST=:: 按 IPv4 通配处理，避免 HTTPServer(AF_INET)
    # 绑定 IPv6 通配地址时直接失败。
    if host == "::":
        return "0.0.0.0"
    return host


def resolve_bind_host(network_config: Any) -> str:
    """根据运行时设置和启动参数决定监听地址。

    - API_HOST 显式写成 0.0.0.0 / 具体网卡 IP：启动参数优先（设置页只展示）；
    - 否则看设置里的 lan_enabled：开 = 0.0.0.0，关 = 127.0.0.1。
    """

    override = env_bind_override()
    if override:
        return override
    enabled = bool((network_config or {}).get("lan_enabled")) if isinstance(network_config, dict) else False
    return "0.0.0.0" if enabled else "127.0.0.1"


def same_bind_host(left: Any, right: Any) -> bool:
    return normalize_bind_host(left) == normalize_bind_host(right)


# ---------------------------------------------------------------------------
# 本机 IPv4 地址枚举
# ---------------------------------------------------------------------------


class _Sockaddr(ctypes.Structure):
    _fields_ = [("sa_family", ctypes.c_uint16), ("sa_data", ctypes.c_char * 14)]


class _SocketAddress(ctypes.Structure):
    _fields_ = [
        ("lpSockaddr", ctypes.POINTER(_Sockaddr)),
        ("iSockaddrLength", ctypes.c_int),
    ]


class _IpAdapterUnicastAddress(ctypes.Structure):
    pass


_IpAdapterUnicastAddress._fields_ = [
    ("Length", ctypes.c_uint32),
    ("Flags", ctypes.c_uint32),
    ("Next", ctypes.POINTER(_IpAdapterUnicastAddress)),
    ("Address", _SocketAddress),
    ("PrefixOrigin", ctypes.c_int),
    ("SuffixOrigin", ctypes.c_int),
    ("DadState", ctypes.c_int),
    ("ValidLifetime", ctypes.c_uint32),
    ("PreferredLifetime", ctypes.c_uint32),
    ("LeaseLifetime", ctypes.c_uint32),
    ("OnLinkPrefixLength", ctypes.c_uint8),
]


class _IpAdapterAddresses(ctypes.Structure):
    pass


_IpAdapterAddresses._fields_ = [
    ("Length", ctypes.c_uint32),
    ("IfIndex", ctypes.c_uint32),
    ("Next", ctypes.POINTER(_IpAdapterAddresses)),
    ("AdapterName", ctypes.c_char_p),
    ("FirstUnicastAddress", ctypes.POINTER(_IpAdapterUnicastAddress)),
    ("FirstAnycastAddress", ctypes.c_void_p),
    ("FirstMulticastAddress", ctypes.c_void_p),
    ("FirstDnsServerAddress", ctypes.c_void_p),
    ("DnsSuffix", ctypes.c_wchar_p),
    ("Description", ctypes.c_wchar_p),
    ("FriendlyName", ctypes.c_wchar_p),
    ("PhysicalAddress", ctypes.c_uint8 * 8),
    ("PhysicalAddressLength", ctypes.c_uint32),
    ("Flags", ctypes.c_uint32),
    ("Mtu", ctypes.c_uint32),
    ("IfType", ctypes.c_uint32),
    ("OperStatus", ctypes.c_int),
    ("Ipv6IfIndex", ctypes.c_uint32),
    ("ZoneIndices", ctypes.c_uint32 * 16),
    ("FirstPrefix", ctypes.c_void_p),
    ("TransmitLinkSpeed", ctypes.c_uint64),
    ("ReceiveLinkSpeed", ctypes.c_uint64),
]


def _windows_adapter_addresses() -> list[dict[str, Any]]:
    """用 GetAdaptersAddresses 一次拿到所有网卡的名字、状态和 IPv4 地址。"""

    if os.name != "nt":
        return []
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi.dll")
        get_adapters = iphlpapi.GetAdaptersAddresses
        get_adapters.argtypes = [
            ctypes.c_uint32,
            ctypes.c_uint32,
            ctypes.c_void_p,
            ctypes.POINTER(_IpAdapterAddresses),
            ctypes.POINTER(ctypes.c_uint32),
        ]
        get_adapters.restype = ctypes.c_uint32
    except (AttributeError, OSError):
        return []

    flags = _GAA_FLAG_SKIP_ANYCAST | _GAA_FLAG_SKIP_MULTICAST | _GAA_FLAG_SKIP_DNS_SERVER
    size = ctypes.c_uint32(15 * 1024)
    buffer = None
    for _ in range(3):
        buffer = ctypes.create_string_buffer(size.value)
        result = get_adapters(
            _AF_INET,
            flags,
            None,
            ctypes.cast(buffer, ctypes.POINTER(_IpAdapterAddresses)),
            ctypes.byref(size),
        )
        if result == 0:
            break
        if result == _ERROR_NO_DATA:
            return []
        if result != _ERROR_BUFFER_OVERFLOW:
            return []
        size.value = max(size.value * 2, 32 * 1024)
    else:
        return []

    rows: list[dict[str, Any]] = []
    adapter = ctypes.cast(buffer, ctypes.POINTER(_IpAdapterAddresses))
    while adapter:
        item = adapter.contents
        unicast = item.FirstUnicastAddress
        while unicast:
            entry = unicast.contents
            sockaddr = entry.Address.lpSockaddr
            if sockaddr:
                raw = ctypes.string_at(sockaddr, 16)
                family = struct.unpack_from("=H", raw, 0)[0]
                if family == _AF_INET:
                    ip = socket.inet_ntoa(raw[4:8])
                    rows.append(
                        {
                            "ip": ip,
                            "interface": str(item.FriendlyName or item.Description or f"网卡 {item.IfIndex}"),
                            "description": str(item.Description or ""),
                            "if_index": int(item.IfIndex),
                            "if_type": int(item.IfType),
                            "oper_status": int(item.OperStatus),
                        }
                    )
            unicast = entry.Next
        adapter = item.Next
    return rows


def _fallback_addresses() -> list[dict[str, Any]]:
    """非 Windows / ctypes 失败时的兜底：主机名解析 + UDP 出口探测。"""

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(ip: str, name: str) -> None:
        ip = str(ip or "").strip()
        if not ip or ip in seen:
            return
        seen.add(ip)
        rows.append(
            {
                "ip": ip,
                "interface": name,
                "description": "",
                "if_index": 0,
                "if_type": 0,
                "oper_status": _IF_OPER_STATUS_UP,
            }
        )

    try:
        hostname = socket.gethostname()
        for ip in socket.gethostbyname_ex(hostname)[2]:
            add(ip, hostname)
    except OSError:
        pass

    for target in ("8.8.8.8", "1.1.1.1", "192.168.1.1"):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.settimeout(0.15)
        try:
            probe.connect((target, 80))
            add(str(probe.getsockname()[0]), "本机网络")
        except OSError:
            pass
        finally:
            probe.close()
    return rows


def _address_kind(ip: str) -> str:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return "other"
    if address.is_loopback or address.is_unspecified or address.is_multicast:
        return "ignored"
    if address.is_link_local:
        return "link-local"
    if address.is_private:
        return "private"
    # 100.64.0.0/10（Tailscale / 运营商 NAT）等共享地址：可用但不是 RFC1918。
    try:
        if address in ipaddress.ip_network("100.64.0.0/10"):
            return "shared"
    except (TypeError, ValueError):
        pass
    return "public"


def _is_usable_lan_ip(ip: str) -> bool:
    kind = _address_kind(ip)
    return kind not in {"ignored", "link-local"}


def _interface_rank(name: str, description: str, if_type: int) -> int:
    """排序用：物理有线/无线优先，虚拟网卡靠后。"""

    blob = f"{name} {description}".lower()
    virtual_hints = (
        "vethernet", "hyper-v", "vmware", "virtualbox", "wsl", "docker",
        "tailscale", "zerotier", "radmin", "hamachi", "npcap", "loopback",
        "bluetooth", "蓝牙",
    )
    if any(hint in blob for hint in virtual_hints):
        return 2
    if "vpn" in blob:
        return 3
    if if_type in {6, 71}:  # ethernet / ieee80211
        return 0
    if if_type == _IF_TYPE_TUNNEL:
        return 3
    return 1


def list_lan_addresses(port: int | None = None) -> list[dict[str, Any]]:
    """列出本机所有可用的局域网 IPv4 地址（不含回环/链路本地）。

    返回顺序：物理网卡优先，其次虚拟网卡；每个地址带 url，方便设置页直接展示。
    """

    try:
        port = int(port or settings.API_PORT)
    except (TypeError, ValueError):
        port = settings.API_PORT

    raw = _windows_adapter_addresses()
    if not raw:
        raw = _fallback_addresses()

    merged: dict[str, dict[str, Any]] = {}
    for row in raw:
        ip = str(row.get("ip") or "").strip()
        if not _is_usable_lan_ip(ip):
            continue
        interface = str(row.get("interface") or "").strip() or "本机网络"
        description = str(row.get("description") or "").strip()
        if int(row.get("oper_status") or _IF_OPER_STATUS_UP) != _IF_OPER_STATUS_UP:
            # 掉线的网卡不展示；地址还在但连不上，展示出来只会误导操作员。
            continue
        if int(row.get("if_type") or 0) == _IF_TYPE_SOFTWARE_LOOPBACK:
            continue
        candidate = {
            "ip": ip,
            "url": f"http://{ip}:{port}",
            "interface": interface,
            "description": description,
            "kind": _address_kind(ip),
            "rank": _interface_rank(interface, description, int(row.get("if_type") or 0)),
            "if_index": int(row.get("if_index") or 0),
        }
        existing = merged.get(ip)
        if existing is None or candidate["rank"] < existing["rank"]:
            merged[ip] = candidate

    rows = list(merged.values())
    rows.sort(key=lambda item: (int(item["rank"]), 0 if item["kind"] == "private" else 1, item["ip"]))
    return rows


def hostname_url(port: int | None = None) -> str:
    """主机名地址（可选展示；解析不到就不返回）。"""

    try:
        port = int(port or settings.API_PORT)
    except (TypeError, ValueError):
        port = settings.API_PORT

    try:
        hostname = socket.gethostname()
        resolved = socket.gethostbyname_ex(hostname)[2]
    except OSError:
        return ""
    if not any(_is_usable_lan_ip(ip) for ip in resolved):
        return ""
    return f"http://{hostname}:{port}"


# ---------------------------------------------------------------------------
# Windows 防火墙
# ---------------------------------------------------------------------------


def firewall_rule_name(port: int) -> str:
    return f"{FIREWALL_RULE_PREFIX} {int(port)})"


def is_windows_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001 - 探测失败按非管理员处理
        return False


def _run_netsh(arguments: list[str], *, timeout: float = 15.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["netsh", *arguments],
        capture_output=True,
        text=True,
        errors="replace",
        timeout=timeout,
        check=False,
        **no_console_window_kwargs(),
    )


def _run_powershell(command: str, *, timeout: float = 25.0) -> subprocess.CompletedProcess[str]:
    """运行只读 PowerShell 查询；输出统一按 UTF-8 解码（规则名可能含中文）。"""

    return subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " + command,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        **no_console_window_kwargs(),
    )


def _existing_port_rules(port: int) -> list[str]:
    """找出所有“已启用 + 入站 + 允许 + 专用/域/任意 profile”的端口放行规则名。

    netsh 按规则名查不到本系统规则时，端口可能早已被别的规则（例如同端口的旧应用）
    放行。不识别这种情况会误导操作员重复添加规则。
    """

    if os.name != "nt":
        return []
    script = (
        f"$names = @(Get-NetFirewallPortFilter | Where-Object {{ $_.LocalPort -eq {int(port)} }} | "
        "Get-NetFirewallRule | Where-Object { [string]$_.Enabled -eq 'True' -and "
        "[string]$_.Direction -eq 'Inbound' -and [string]$_.Action -eq 'Allow' -and "
        "([string]$_.Profile -eq 'Any' -or [string]$_.Profile -match 'Private' -or [string]$_.Profile -match 'Domain') } | "
        "Select-Object -ExpandProperty DisplayName); "
        "$names | Sort-Object -Unique"
    )
    try:
        result = _run_powershell(script)
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    names = [line.strip() for line in (result.stdout or "").splitlines() if line.strip()]
    return list(dict.fromkeys(names))


def firewall_status(port: int | None = None) -> dict[str, Any]:
    """检查当前端口是否已有放行规则（结果缓存 10 秒）。"""

    try:
        port = int(port or settings.API_PORT)
    except (TypeError, ValueError):
        port = settings.API_PORT
    if os.name != "nt":
        return {
            "supported": False,
            "rule_exists": False,
            "port_open": False,
            "port_allowed_by": [],
            "rule_name": firewall_rule_name(port),
            "port": port,
            "is_admin": False,
            "message": "仅 Windows 支持自动配置防火墙；其它系统请自行放行端口。",
        }

    now = time.monotonic()
    with _FIREWALL_CACHE_LOCK:
        cached = _FIREWALL_CACHE.get(port)
        if cached and now - cached[0] <= _FIREWALL_CACHE_TTL_SECONDS:
            return dict(cached[1])

    rule_name = firewall_rule_name(port)
    try:
        result = _run_netsh(
            ["advfirewall", "firewall", "show", "rule", f"name={rule_name}"]
        )
        exists = result.returncode == 0
    except (OSError, subprocess.SubprocessError) as exc:
        exists = False
        print(f"[network] 读取防火墙规则失败：{exc}", file=sys.stderr)

    # 本系统规则不存在时，端口仍可能被同端口的旧应用规则放行（例如 8788 上的
    # iLab CONJURE）。只查规则名会误报“尚未放行”，让操作员重复添加规则。
    port_allowed_by = [] if exists else _existing_port_rules(port)
    port_open = bool(exists or port_allowed_by)
    if exists:
        message = ""
    elif port_allowed_by:
        message = "端口已被现有防火墙规则放行：" + "、".join(port_allowed_by[:5])
    else:
        message = "尚未找到本端口的放行规则。"

    status = {
        "supported": True,
        "rule_exists": exists,
        "port_open": port_open,
        "port_allowed_by": port_allowed_by,
        "rule_name": rule_name,
        "port": port,
        "is_admin": is_windows_admin(),
        "message": message,
    }
    with _FIREWALL_CACHE_LOCK:
        _FIREWALL_CACHE[port] = (now, dict(status))
    return status


def invalidate_firewall_cache(port: int | None = None) -> None:
    with _FIREWALL_CACHE_LOCK:
        if port is None:
            _FIREWALL_CACHE.clear()
        else:
            try:
                _FIREWALL_CACHE.pop(int(port), None)
            except (TypeError, ValueError):
                _FIREWALL_CACHE.clear()


def ensure_firewall_rule(port: int | None = None) -> dict[str, Any]:
    """添加只允许本地子网访问的入站规则（需要管理员权限）。"""

    try:
        port = int(port or settings.API_PORT)
    except (TypeError, ValueError):
        port = settings.API_PORT
    if os.name != "nt":
        return {"ok": False, "message": "仅 Windows 支持自动配置防火墙。", "port": port}

    rule_name = firewall_rule_name(port)
    current = firewall_status(port)
    if current.get("rule_exists"):
        return {"ok": True, "already": True, "message": "防火墙规则已存在。", "port": port, "rule_name": rule_name}
    if current.get("port_open"):
        allowed = current.get("port_allowed_by") or []
        detail = "、".join(str(name) for name in allowed[:5]) or "系统现有规则"
        return {
            "ok": True,
            "already": True,
            "message": f"端口已被现有防火墙规则放行（{detail}），无需重复添加。",
            "port": port,
            "rule_name": rule_name,
            "port_allowed_by": allowed,
        }

    try:
        result = _run_netsh(
            [
                "advfirewall", "firewall", "add", "rule",
                f"name={rule_name}",
                "dir=in",
                "action=allow",
                "protocol=TCP",
                f"localport={port}",
                "profile=private,domain",
                "remoteip=LocalSubnet",
                "enable=yes",
            ]
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "message": f"执行 netsh 失败：{exc}", "port": port, "rule_name": rule_name}

    with _FIREWALL_CACHE_LOCK:
        _FIREWALL_CACHE.pop(port, None)

    output = " ".join(part for part in (result.stdout, result.stderr) if part).strip()
    if result.returncode == 0:
        return {
            "ok": True,
            "already": False,
            "message": "已开放防火墙端口（仅专用/域网络和本地子网）。",
            "port": port,
            "rule_name": rule_name,
            "output": output[:800],
        }
    message = output[:800] or "添加防火墙规则失败。"
    if not is_windows_admin():
        message += " 需要以管理员身份运行 API 才能自动添加规则。"
    return {"ok": False, "message": message, "port": port, "rule_name": rule_name, "output": output[:800]}


# ---------------------------------------------------------------------------
# 设置页状态
# ---------------------------------------------------------------------------


def build_network_status(
    network_config: Any,
    *,
    active_bind_host: str = "",
    active_port: int = 0,
    rebind_scheduled: bool = False,
) -> dict[str, Any]:
    """给设置页的完整网络状态（当前监听、可用地址、防火墙、并发配置）。"""

    config = network_config if isinstance(network_config, dict) else {}
    desired_host = resolve_bind_host(config)
    active_host = normalize_bind_host(active_bind_host) or normalize_bind_host(settings.API_HOST)
    try:
        port = int(active_port or settings.API_PORT)
    except (TypeError, ValueError):
        port = settings.API_PORT

    active_lan = is_lan_bind_host(active_host)
    configured_enabled = bool(config.get("lan_enabled"))
    locked_by_env = bool(env_bind_override())

    addresses = list_lan_addresses(port)
    for address in addresses:
        if active_lan and (is_wildcard_host(active_host) or same_bind_host(active_host, address["ip"])):
            address["reachable"] = True
        else:
            address["reachable"] = False

    restart_required = (
        not locked_by_env
        and not same_bind_host(desired_host, active_host)
        and not rebind_scheduled
    )

    hints: list[str] = []
    if locked_by_env:
        hints.append(
            f"监听地址由启动参数 API_HOST={settings.API_HOST} 指定，设置页的开关不会覆盖它。"
        )
        if not same_bind_host(desired_host, active_host):
            hints.append(
                f"启动参数指定的 {desired_host} 当前无法绑定，实际监听 {active_host}；请检查网卡地址或修改启动参数。"
            )
    elif rebind_scheduled:
        hints.append("正在切换监听地址，当前页面可能短暂断开，稍后会自动恢复。")
    elif restart_required:
        hints.append("网络设置已保存，重启 API 后生效。")

    firewall = firewall_status(port)
    firewall_open = bool(firewall.get("rule_exists") or firewall.get("port_open"))
    if active_lan and firewall.get("supported") and not firewall_open:
        hints.append("Windows 防火墙可能拦截局域网访问；如无法打开，请点「开放防火墙端口」。")

    return {
        "configured_enabled": configured_enabled,
        "enabled": is_lan_bind_host(desired_host),
        "active": active_lan,
        "bind_host": desired_host,
        "active_bind_host": active_host,
        "port": port,
        "restart_required": restart_required,
        "rebind_scheduled": bool(rebind_scheduled),
        "locked_by_env": locked_by_env,
        "env_bind_host": env_bind_override(),
        "loopback_url": f"http://127.0.0.1:{port}",
        "hostname_url": hostname_url(port) if active_lan else "",
        "addresses": addresses,
        "firewall": firewall,
        "hints": hints,
        "server": {
            "protocol": settings.API_HTTP_PROTOCOL,
            "keep_alive": settings.API_HTTP_PROTOCOL.upper() == "HTTP/1.1",
            "keep_alive_timeout_seconds": settings.API_KEEPALIVE_TIMEOUT_SECONDS,
            "request_queue_size": settings.API_REQUEST_QUEUE_SIZE,
            "get_cache_ttl_seconds": settings.API_GET_CACHE_TTL_SECONDS,
        },
    }
