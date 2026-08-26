# -*- coding: utf-8 -*-
import base64
import json
import os
import socket
import urllib.parse
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 用户自定义配置区 ====================
PROXY_SWITCH = 'N'    
CONVERT_API = "https://edge-api-v1.ffqla.com/sub?target=mixed&url="
DAYS_LIMIT = 7    
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
            print(f"    -> [{url}] 抓取成功 | 提取节点: {len(nodes)} 个")
            return url, nodes
        else:
            print(f"    -> [{url}] 抓取失败 | HTTP状态码: {resp.status_code}")
    except Exception as e:
        print(f"    -> [{url}] 请求异常 | {e}")
    return url, []

def fetch_links_batch(link_list):
    if not link_list:
        return [], {}

    headers = {'User-Agent': 'v2rayN/6.23'}
    all_nodes = []
    url_details = {} # 用于保存 URL -> 节点数量的对应关系
    
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_url = {executor.submit(fetch_single_url, url, headers): url for url in link_list}
        for future in as_completed(future_to_url):
            url, nodes = future.result()
            url_details[url] = len(nodes)
            if nodes:
                all_nodes.extend(nodes)

    return all_nodes, url_details

def extract_node_host(node_str):
    try:
        if node_str.lower().startswith('vmess://'):
            decoded_json_str = v2rayn_base64_decode(node_str.split('://')[1].split('#')[0].split('?')[0])
            if decoded_json_str:
                host = json.loads(decoded_json_str).get('add')
                if host: return str(host).strip()
        clean_str = node_str.split('#')[0].split('?')[0]
        parsed = urlparse(clean_str)
        netloc = parsed.netloc or clean_str.split('://')[-1]
        if '@' in netloc: netloc = netloc.split('@')[-1]
        host = netloc.split(':')[0].strip('[]')
        if host: return host
    except Exception:
        pass
    return "UnknownIP"

def check_keyword_match(target_str, keywords):
    search_target = urllib.parse.unquote(target_str).lower()
    for kw in keywords:
        if kw.lower() in search_target:
            return True
    return False

def get_country_code(node_str):
    host = extract_node_host(node_str)
    combined_target = f"{node_str} {host}"

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
    host = extract_node_host(node_str)
    beijing_tz = timezone(timedelta(hours=8))
    current_time = datetime.now(beijing_tz).strftime('%d-%H')
    
    new_name = f"{country}-{current_time}-{host}"
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

def test_tcping(node_str):
    try:
        host, port = None, None
        if node_str.lower().startswith('vmess://'):
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
        if not host or not port: return False
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False

def main():
    print("========================================")
    proxy_status = f"开启 (10808)" if USE_PROXY else "关闭 (直连)"
    print(f" 开始抓取 | 代理状态: {proxy_status}")
    print("========================================")
    
    # 1. 处理 links.txt
    t_links, gh_links, chat_links = parse_links_file()
    all_links_1 = t_links + gh_links + chat_links
    raw_nodes_1, details_1 = fetch_links_batch(all_links_1)
    print(f"\n[抓取统计] links.txt 提取原始节点总数: {len(raw_nodes_1)} 个")
    
    alive_nodes_1 = []
    if raw_nodes_1:
        nodes_1 = list(set([rename_node(n) for n in raw_nodes_1]))
        with ThreadPoolExecutor(max_workers=200) as executor:
            future_map = {executor.submit(test_tcping, n): n for n in nodes_1}
            for future in as_completed(future_map):
                if future.result():
                    alive_nodes_1.append(future_map[future])

    # 2. 处理 self.txt
    ps_tasks = parse_pslinks_file()
    alive_nodes_2, details_2 = fetch_links_batch(ps_tasks)
    if ps_tasks:
        print(f"[抓取统计] self.txt 提取节点总数: {len(alive_nodes_2)} 个")

    # 3. 合并抓取明细并写入 fetch_details.txt
    all_details = {**details_1, **details_2}
    
    with open('fetch_details.txt', 'w', encoding='utf-8') as f:
        f.write("========== 订阅链接节点抓取明细列表 ==========\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        for url, count in all_details.items():
            line = f"链接: {url}\n抓取节点数量: {count} 个\n" + "-"*50 + "\n"
            f.write(line)

    # 4. 合并与去重导出
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
    print(f" - 抓取明细已导出至文件:            fetch_details.txt")
    print(f" - 最终有效可用节点总数 (ALL.txt):    {len(alive_nodes)} 个")
    print(f" - 其中美国专属节点        (US.txt):    {len(us_nodes)} 个")
    print(f" - 其中 AI 友好节点        (AI.txt):    {len(ai_nodes)} 个")
    print(f" - 其中其他节点            (OTHER.txt): {len(other_nodes)} 个")
    print("========================================")

if __name__ == "__main__":
    main()
