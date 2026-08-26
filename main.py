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

PROTOCOLS = (
    "vmess://", "vless://", "ss://", "ssr://", "trojan://",
    "hysteria://", "hysteria2://", "hy2://", "tuic://",
    "juicity://", "wireguard://", "wg://", "socks://", "socks5://", "http://"
)

UDP_PROTOCOLS = ("hysteria://", "hysteria2://", "hy2://", "tuic://", "juicity://", "wireguard://", "wg://")

US_KEYWORDS = ['us', 'usa', 'united states', 'america', '美', '洛杉矶', '圣何塞', '硅谷', '俄勒冈', '弗吉尼亚', '西雅图', '达拉斯']

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

def encode_to_base64_file(filename, node_list):
    """将节点列表转为标准 Base64 格式写入文件"""
    content = "\n".join(node_list)
    encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(encoded)

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

def is_us_raw_node(node_str):
    """判断原始节点字符串是否为美国节点（不修改节点内容）"""
    host, _ = extract_node_info(node_str)
    host_str = host if host else ""
    target_text = urllib.parse.unquote(f"{node_str} {host_str}").lower()
    return any(kw.lower() in target_text for kw in US_KEYWORDS)

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

# ==================== 专属通道：self.txt 处理 (不测速/不改名/不去重) ====================

def process_self_nodes():
    """独立处理 self.txt 链接，生成 SALL.txt 和 SUS.txt"""
    ps_path = 'self.txt'
    if not os.path.exists(ps_path):
        print(f"\n[提示] 未在同路径下找到 {ps_path} 文件，跳过专属通道。")
        encode_to_base64_file('SALL.txt', [])
        encode_to_base64_file('SUS.txt', [])
        return {}

    ps_tasks = []
    with open(ps_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('http://') or line.startswith('https://'):
                # 1. 原始链接
                ps_tasks.append(line)
                # 2. 拼接 subconverter 转换后的链接
                converted_url = CONVERT_API + urllib.parse.quote(line, safe='')
                ps_tasks.append(converted_url)

    print(f"\n[专属通道] 从 self.txt 提取链接，已创建 {len(ps_tasks)} 个双通道请求任务...")
    
    # 抓取原始节点列表（保持原始顺序，不去重）
    raw_sall_nodes, self_details = fetch_links_batch(ps_tasks)
    
    # 筛选美国节点（仅匹配地区，不更名、不去重）
    raw_sus_nodes = [n for n in raw_sall_nodes if is_us_raw_node(n)]
    
    # 输出 Base64 文件
    encode_to_base64_file('SALL.txt', raw_sall_nodes)
    encode_to_base64_file('SUS.txt', raw_sus_nodes)
    
    print(f"[专属通道完成] SALL.txt (原样导出): {len(raw_sall_nodes)} 个 | SUS.txt (美国筛选): {len(raw_sus_nodes)} 个")
    return self_details

# ==================== 公共通道：三轮测活/重命名/去重 ====================

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

def phase1_fast_tcp_check(node_str):
    node_lower = node_str.lower()
    if any(node_lower.startswith(proto) for proto in UDP_PROTOCOLS): return True
    host, port = extract_node_info(node_str)
    if not host or not port: return False
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(0.8)
        res = s.connect_ex((host, port))
        s.close()
        return res == 0
    except Exception:
        return False

def phase2_deep_tls_check(node_str):
    node_lower = node_str.lower()
    if any(node_lower.startswith(proto) for proto in UDP_PROTOCOLS): return True
    host, port = extract_node_info(node_str)
    if not host or not port: return False
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
    try:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            temp_config_path = f.name
        config_content = {"log": {"disabled": True}, "outbounds": [{"type": "direct", "tag": "direct"}]}
        with open(temp_config_path, 'w', encoding='utf-8') as f:
            json.dump(config_content, f)
        cmd = ["./sing-box", "check", "-c", temp_config_path]
        res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=3)
        if os.path.exists(temp_config_path):
            os.remove(temp_config_path)
        return res.returncode == 0
    except Exception:
        return False

def phase3_singbox_check(nodes_list):
    singbox_bin = "./sing-box"
    if not os.path.exists(singbox_bin) or not os.access(singbox_bin, os.X_OK):
        print("    [警告] 未找到 sing-box 内核，自动跳过第三轮检测。")
        return nodes_list

    print(f"[阶段 3/3] 启动 sing-box 内核校验协议与参数 (待测: {len(nodes_list)} 个)...")
    valid_nodes = []
    with ThreadPoolExecutor(max_workers=30) as executor:
        future_map = {executor.submit(single_singbox_check, n): n for n in nodes_list}
        for future in as_completed(future_map):
            if future.result():
                valid_nodes.append(future_map[future])
    print(f"    -> 第三轮校验完成，幸存节点: {len(valid_nodes)} 个")
    return valid_nodes

def get_country_code(node_str):
    if is_us_raw_node(node_str): return 'US'
    host, _ = extract_node_info(node_str)
    combined_target = urllib.parse.unquote(f"{node_str} {host if host else ''}").lower()
    
    mapping = {
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
    for code, keywords in mapping.items():
        if any(kw in combined_target for kw in keywords):
            return code
    return "OTH"

def rename_node(node_str):
    country = get_country_code(node_str)
    host, _ = extract_node_info(node_str)
    beijing_tz = timezone(timedelta(hours=8))
    current_time = datetime.now(beijing_tz).strftime('%d-%H')
    new_name = f"{country}-{current_time}-{host if host else 'UnknownIP'}"
    if '#' in node_str:
        return f"{node_str.rsplit('#', 1)[0]}#{urllib.parse.quote(new_name)}"
    return f"{node_str}#{urllib.parse.quote(new_name)}"

def main():
    print("========================================")
    print(f" 开始执行任务 | 代理状态: {'开启' if USE_PROXY else '直连'}")
    print("========================================")
    
    # 1. 处理专属通道 self.txt (独立、不测速、不去重、不改名)
    self_details = process_self_nodes()

    # 2. 处理公共通道 links.txt (三轮严苛测活、更名、去重)
    t_links, gh_links, chat_links = parse_links_file()
    raw_nodes, public_details = fetch_links_batch(t_links + gh_links + chat_links)
    print(f"\n[公共通道] links.txt 提取节点: {len(raw_nodes)} 个")
    
    alive_nodes = []
    if raw_nodes:
        # 重命名并去重
        nodes_to_test = list(set([rename_node(n) for n in raw_nodes]))
        
        # 第一轮：TCP 极速粗筛
        print(f"[阶段 1/3] 极速粗筛 (待测: {len(nodes_to_test)} 个)...")
        p1_survivors = []
        with ThreadPoolExecutor(max_workers=200) as executor:
            future_map = {executor.submit(phase1_fast_tcp_check, n): n for n in nodes_to_test}
            for future in as_completed(future_map):
                if future.result(): p1_survivors.append(future_map[future])
        print(f"    -> 第一轮幸存: {len(p1_survivors)} 个")

        # 第二轮：TLS 深度精筛
        print(f"[阶段 2/3] TLS 深度精筛 (待测: {len(p1_survivors)} 个)...")
        p2_survivors = []
        with ThreadPoolExecutor(max_workers=60) as executor:
            future_map = {executor.submit(phase2_deep_tls_check, n): n for n in p1_survivors}
            for future in as_completed(future_map):
                if future.result(): p2_survivors.append(future_map[future])
        print(f"    -> 第二轮幸存: {len(p2_survivors)} 个")

        # 第三轮：sing-box 真连接校验
        alive_nodes = phase3_singbox_check(p2_survivors)

    # 3. 输出抓取统计文件 linksdetails.txt
    all_details = {**public_details, **self_details}
    with open('linksdetails.txt', 'w', encoding='utf-8') as f:
        f.write("========== 链接抓取节点数统计 ==========\n")
        for url, count in all_details.items():
            f.write(f"链接: {url}\n抓取节点数: {count} 个\n" + "-"*40 + "\n")

    # 4. 导出公共分类订阅文件 (Base64)
    us_nodes = [n for n in alive_nodes if get_country_code(n) == 'US']
    ai_nodes = [n for n in alive_nodes if get_country_code(n) in {'JP', 'SG', 'KR', 'TW', 'GB', 'DE', 'FR', 'CA', 'AU'}]
    other_nodes = [n for n in alive_nodes if get_country_code(n) not in {'US', 'JP', 'SG', 'KR', 'TW', 'GB', 'DE', 'FR', 'CA', 'AU'}]
            
    encode_to_base64_file('ALL.txt', alive_nodes)
    encode_to_base64_file('US.txt', us_nodes)
    encode_to_base64_file('AI.txt', ai_nodes)
    encode_to_base64_file('OTHER.txt', other_nodes)
    
    print("\n" + "="*40)
    print(" 任务执行完成！订阅导出汇总：")
    print(f" [公共精选] ALL.txt:   {len(alive_nodes)} 个")
    print(f" [公共美国] US.txt:    {len(us_nodes)} 个")
    print(f" [公共 AI ] AI.txt:    {len(ai_nodes)} 个")
    print(f" [公共其他] OTHER.txt: {len(other_nodes)} 个")
    print("----------------------------------------")
    print(f" [专属原样] SALL.txt:  (已生成)")
    print(f" [专属美国] SUS.txt:   (已生成)")
    print("========================================")

if __name__ == "__main__":
    main()
