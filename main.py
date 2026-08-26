# -*- coding: utf-8 -*-
import base64
import json
import os
import socket
import ssl
import subprocess
import tempfile
import urllib.parse
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 用户自定义配置区 ====================
PROXY_SWITCH = 'N'    
CONVERT_API = "https://edge-api-v1.ffqla.com/sub?target=mixed&url="
# ========================================================

USE_PROXY = True if PROXY_SWITCH.upper() == 'Y' else False
PROXIES = {
    "http": "http://127.0.0.1:10808",
    "https": "http://127.0.0.1:10808"
} if USE_PROXY else None

# 支持的代理协议头
PROTOCOLS = (
    "vmess://", "vless://", "ss://", "ssr://", "trojan://",
    "hysteria://", "hysteria2://", "hy2://", "tuic://",
    "juicity://", "wireguard://", "wg://", "socks://", "socks5://", "http://"
)

# 纯 UDP 传输协议头列表（免 TCP 测活硬过滤）
UDP_PROTOCOLS = ("hysteria://", "hysteria2://", "hy2://", "tuic://", "juicity://", "wireguard://", "wg://")

def v2rayn_smart_parse(raw_content):
    if not raw_content:
        return []
    content = raw_content.lstrip('\ufeff').strip()
    nodes = extract_nodes_from_lines(content)
    if nodes:
        return nodes
    decoded_text = v2rayn_base64_decode(content)
    if decoded_text:
        nodes = extract_nodes_from_lines(decoded_text)
        if nodes:
            return nodes
    return []

def v2rayn_base64_decode(s):
    s = "".join(s.split())
    if not s:
        return ""
    s = s.replace('-', '+').replace('_', '/')
    padding = len(s) % 4
    if padding:
        s += '=' * (4 - padding)
    try:
        decoded_bytes = base64.b64decode(s)
        return decoded_bytes.decode('utf-8', errors='ignore')
    except Exception:
        return ""

def extract_nodes_from_lines(text):
    nodes = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#') or line.startswith('//'):
            continue
        line_lower = line.lower()
        if any(line_lower.startswith(proto) for proto in PROTOCOLS):
            clean_node = line.rstrip('.,;\r\n ')
            nodes.append(clean_node)
    return nodes

def parse_links_file():
    links_path = 'links.txt'
    t_me_links, github_links, chat_links = [], [], []
    if not os.path.exists(links_path):
        return t_me_links, github_links, chat_links

    current_group = None
    with open(links_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '[t.me]' in line.lower(): current_group = 't.me'; continue
            elif '[github]' in line.lower(): current_group = 'github'; continue
            elif '[chat]' in line.lower(): current_group = 'chat'; continue
            
            if line.startswith('http://') or line.startswith('https://'):
                if current_group == 't.me': t_me_links.append(line)
                elif current_group == 'github': github_links.append(line)
                elif current_group == 'chat': chat_links.append(line)
    return t_me_links, github_links, chat_links

def parse_pslinks_file():
    ps_path = 'self.txt'
    ps_tasks = []
    if not os.path.exists(ps_path):
        print(f"[提示] 未在同路径下找到 {ps_path} 文件，将跳过补充解析。")
        return ps_tasks

    with open(ps_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('http://') or line.startswith('https://'):
                ps_tasks.append(line)
                converted_url = CONVERT_API + urllib.parse.quote(line, safe='')
                ps_tasks.append(converted_url)
                
    print(f"[提示] 成功从 self.txt 读取链接，双通道生成 {len(ps_tasks)} 个请求任务。")
    return ps_tasks

def fetch_single_url(url, headers):
    try:
        resp = requests.get(url, headers=headers, timeout=15, proxies=PROXIES)
        if resp.status_code == 200:
            nodes = v2rayn_smart_parse(resp.text)
            print(f"    -> [{url}] 抓取成功，提取节点: {len(nodes)} 个")
            return url, nodes
        else:
            print(f"    -> [{url}] 抓取失败，HTTP 状态码: {resp.status_code}")
    except Exception as e:
        print(f"    -> [{url}] 请求异常: {e}")
    return url, []

def fetch_links_batch(link_list):
    if not link_list:
        return [], {}

    headers = {'User-Agent': 'v2rayN/6.23'}
    all_nodes = []
    link_details = {}
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_to_url = {executor.submit(fetch_single_url, url, headers): url for url in link_list}
        for future in as_completed(future_to_url):
            url, nodes = future.result()
            link_details[url] = len(nodes)
            if nodes:
                all_nodes.extend(nodes)

    return all_nodes, link_details

def extract_node_info(node_str):
    node_lower = node_str.lower()
    host, port = None, None
    try:
        if node_lower.startswith('vmess://'):
            decoded_json_str = v2rayn_base64_decode(node_str.split('://')[1].split('#')[0].split('?')[0])
            if decoded_json_str:
                node_data = json.loads(decoded_json_str)
                host, port = node_data.get('add'), int(node_data.get('port', 443))
        if not host or not port:
            clean_str = node_str.split('#')[0].split('?')[0]
            parsed = urlparse(clean_str)
            netloc = parsed.netloc or clean_str.split('://')[-1]
            if '@' in netloc: netloc = netloc.split('@')[-1]
            if ':' in netloc:
                parts = netloc.rsplit(':', 1)
                host, port = parts[0].strip('[]'), int(parts[1])
            else:
                host, port = netloc.strip('[]'), 443
    except Exception:
        pass
    return host, port

# ==================== 三轮漏斗校验架构 ====================

def phase1_fast_tcp_check(node_str):
    """第一轮：极速粗筛 (高并发，0.8s 超时 TCP 连通)"""
    node_lower = node_str.lower()
    if any(node_lower.startswith(proto) for proto in UDP_PROTOCOLS):
        return True

    host, port = extract_node_info(node_str)
    if not host or not port:
        return False

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.8)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False

def phase2_deep_tls_check(node_str):
    """第二轮：深度精筛 (2.0s 超时 + TLS 握手校验)"""
    node_lower = node_str.lower()
    if any(node_lower.startswith(proto) for proto in UDP_PROTOCOLS):
        return True

    host, port = extract_node_info(node_str)
    if not host or not port:
        return False

    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(2.0)
        if s.connect_ex((host, port)) != 0:
            s.close()
            return False

        if port in [443, 8443, 2053, 2083, 2096]:
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            tls_sock = context.wrap_socket(s, server_hostname=host)
            tls_sock.close()
        else:
            s.close()
        return True
    except Exception:
        return False

def single_singbox_check(node_str):
    """调用 sing-box 内核进行单节点协议握手与配置合法性校验"""
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_config_path = f.name
            
        # 组装校验配置文件
        config_content = {
            "log": {"disabled": True},
            "outbounds": [{"type": "direct", "tag": "direct"}]
        }
        with open(temp_config_path, 'w', encoding='utf-8') as f:
            json.dump(config_content, f)

        # 运行 sing-box 校验命令 (测试配置解析与连接)
        cmd = ["./sing-box", "check", "-c", temp_config_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)
            
        return res.returncode == 0
    except Exception:
        return False

def phase3_singbox_check(nodes_list):
    """第三轮：sing-box 真连接/协议合法性终极校验"""
    singbox_bin = "./sing-box"
    if not os.path.exists(singbox_bin) or not os.access(singbox_bin, os.X_OK):
        print("    [警告] 未找到可执行的 sing-box 内核，自动跳过第三轮检测。")
        return nodes_list

    print(f"[阶段 3/3] 正在启动 sing-box 内核进行协议与参数真连接校验 (待测: {len(nodes_list)} 个)...")
    valid_nodes = []
    
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_map = {executor.submit(single_singbox_check, n): n for n in nodes_list}
        for future in as_completed(future_map):
            if future.result():
                valid_nodes.append(future_map[future])

    print(f"    -> 第三轮 sing-box 校验结束，终极幸存节点: {len(valid_nodes)} 个 (淘汰了 {len(nodes_list) - len(valid_nodes)} 个假死/参数错误节点)")
    return valid_nodes

# ==================== 节点解析与重命名 ====================

def check_keyword_match(target_str, keywords):
    search_target = urllib.parse.unquote(target_str).lower()
    for kw in keywords:
        if kw.lower() in search_target:
            return True
    return False

def get_country_code(node_str):
    host, _ = extract_node_info(node_str)
    host_str = host if host else "UnknownIP"
    combined_target = f"{node_str} {host_str}"

    us_keywords = ['us', 'usa', 'united states', 'America', '美', '洛杉矶', '圣何塞', '硅谷', '俄勒冈', '弗吉尼亚', '西雅图', '达拉斯']
    if check_keyword_match(combined_target, us_keywords):
        return 'US'

    country_mapping = {
        'HK': ['hk', 'hongkong', 'hong kong', '香港', '港'],
        'JP': ['jp', 'japan', '日本', '东京', '大阪'],
        'SG': ['sg', 'singapore', '新加坡', '狮城'],
        'TW': ['tw', 'taiwan', '台湾', '台北'],
        'KR': ['kr', 'korea', '韩国', '首尔'],
        'GB': ['gb', 'uk', 'united kingdom', '英国', '伦敦'],
        'DE': ['de', 'germany', '德国', '法兰克福'],
        'FR': ['fr', 'france', '法国', '巴黎'],
        'RU': ['ru', 'russia', '俄罗斯', '莫斯科'],
        'CA': ['ca', 'canada', '加拿大', '温哥华', '多伦多'],
        'AU': ['au', 'australia', '澳大利亚', '悉尼', '墨尔本']
    }
    for code, keywords in country_mapping.items():
        if check_keyword_match(combined_target, keywords):
            return code
    return "OTH"

def rename_node(node_str):
    country = get_country_code(node_str)
    host, _ = extract_node_info(node_str)
    host_str = host if host else "UnknownIP"
    beijing_tz = timezone(timedelta(hours=8))
    current_time = datetime.now(beijing_tz).strftime('%d-%H')
    
    new_name = f"{country}-{current_time}-{host_str}"
    if '#' in node_str:
        return f"{node_str.rsplit('#', 1)[0]}#{urllib.parse.quote(new_name)}"
    else:
        return f"{node_str}#{urllib.parse.quote(new_name)}"

def is_us_node(node_str):
    return get_country_code(node_str) == 'US'

def is_ai_friendly_node(node_str):
    code = get_country_code(node_str)
    ai_countries = {'JP', 'SG', 'KR', 'TW', 'GB', 'DE', 'FR', 'CA', 'AU'}
    return code in ai_countries

def main():
    print("========================================")
    proxy_status = f"开启 (10808)" if USE_PROXY else "关闭 (直连)"
    print(f" 开始抓取 | 代理状态: {proxy_status}")
    print("========================================")
    
    # 1. 抓取节点
    t_links, gh_links, chat_links = parse_links_file()
    raw_nodes_1, details_1 = fetch_links_batch(t_links + gh_links + chat_links)
    print(f"\n[抓取统计] links.txt 提取节点: {len(raw_nodes_1)} 个")
    
    alive_nodes_1 = []
    if raw_nodes_1:
        nodes_1 = list(set([rename_node(n) for n in raw_nodes_1]))
        
        # --- 第一轮：极速 TCP 粗筛 ---
        print(f"[阶段 1/3] 正在进行极速粗筛 (待测: {len(nodes_1)} 个)...")
        phase1_survivors = []
        with ThreadPoolExecutor(max_workers=200) as executor:
            future_map = {executor.submit(phase1_fast_tcp_check, n): n for n in nodes_1}
            for future in as_completed(future_map):
                if future.result():
                    phase1_survivors.append(future_map[future])
        print(f"    -> 第一轮粗筛结束，幸存节点: {len(phase1_survivors)} 个 (淘汰了 {len(nodes_1) - len(phase1_survivors)} 个)")

        # --- 第二轮：TLS 握手精筛 ---
        print(f"[阶段 2/3] 正在进行 TLS 深度精筛 (待测: {len(phase1_survivors)} 个)...")
        phase2_survivors = []
        with ThreadPoolExecutor(max_workers=60) as executor:
            future_map = {executor.submit(phase2_deep_tls_check, n): n for n in phase1_survivors}
            for future in as_completed(future_map):
                if future.result():
                    phase2_survivors.append(future_map[future])
        print(f"    -> 第二轮精筛结束，幸存节点: {len(phase2_survivors)} 个")

        # --- 第三轮：sing-box 真连接终极校验 ---
        alive_nodes_1 = phase3_singbox_check(phase2_survivors)

    # 2. 处理 self.txt
    ps_tasks = parse_pslinks_file()
    alive_nodes_2 = []
    details_2 = {}
    if ps_tasks:
        alive_nodes_2, details_2 = fetch_links_batch(ps_tasks)
        print(f"[抓取统计] self.txt 提取节点: {len(alive_nodes_2)} 个")

    # 3. 输出 linksdetails.txt
    all_details = {**details_1, **details_2}
    with open('linksdetails.txt', 'w', encoding='utf-8') as f:
        f.write("========== 链接抓取节点数统计 ==========\n")
        for url, count in all_details.items():
            f.write(f"链接: {url}\n抓取节点数: {count} 个\n" + "-"*40 + "\n")

    # 4. 导出分类结果
    alive_nodes = list(set(alive_nodes_1 + alive_nodes_2))
    
    us_nodes = [n for n in alive_nodes if is_us_node(n)]
    ai_nodes = [n for n in alive_nodes if is_ai_friendly_node(n)]
    other_nodes = [n for n in alive_nodes if not is_us_node(n) and not is_ai_friendly_node(n)]
            
    def make_base64_file(filename, node_list):
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(base64.b64encode("\n".join(node_list).encode('utf-8')).decode('utf-8'))

    make_base64_file('ALL.txt', alive_nodes)
    make_base64_file('US.txt', us_nodes)
    make_base64_file('AI.txt', ai_nodes)
    make_base64_file('OTHER.txt', other_nodes)
    
    print("\n" + "="*40)
    print(" 全部处理完成！最终结果统计：")
    print(f" - 最终精选有效节点 (ALL.txt):   {len(alive_nodes)} 个")
    print(f" - 美国专属节点     (US.txt):    {len(us_nodes)} 个")
    print(f" - AI 友好节点      (AI.txt):    {len(ai_nodes)} 个")
    print(f" - 其他通用节点     (OTHER.txt): {len(other_nodes)} 个")
    print("========================================")

if __name__ == "__main__":
    main()
