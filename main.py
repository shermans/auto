# -*- coding: utf-8 -*-
import base64
import json
import os
import re
import socket
import urllib.parse
from urllib.parse import urlparse
from datetime import datetime, timezone, timedelta
import requests
from requests.adapters import HTTPAdapter
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==================== 用户自定义配置区 ====================
PROXY_SWITCH = 'N'  
CONVERT_API = "https://url.v1.mk/sub?target=mixed&url="
DAYS_LIMIT = 3
EXTERNAL_US_URL = "https://raw.githubusercontent.com/shermans/auto/refs/heads/main/US.txt"

# GitHub Actions 运行环境下的安全并发上限参数
FETCH_MAX_WORKERS = 200    # HTTP 抓取并发数
TCPING_MAX_WORKERS = 1200   # TCP 测活并发数
# ========================================================

USE_PROXY = True if PROXY_SWITCH.upper() == 'Y' else False
PROXIES = {
    "http": "http://127.0.0.1:10808",
    "https": "http://127.0.0.1:10808"
} if USE_PROXY else None

# 创建全局高性能 Session，扩大连接池以支持高并发
GLOBAL_SESSION = requests.Session()
adapter = HTTPAdapter(pool_connections=FETCH_MAX_WORKERS, pool_maxsize=FETCH_MAX_WORKERS * 2)
GLOBAL_SESSION.mount("http://", adapter)
GLOBAL_SESSION.mount("https://", adapter)

SUPPORTED_SCHEMES = (
    "vmess", "vless", "trojan", "ss", "ssr", 
    "hysteria","hysteria2", "hy2", "tuic", "anytls","Anytls", 
    "juicity", "wireguard", "wg", "ssh", "socks5"
)
PROTOCOL_REGEX_STR = r"((?:" + "|".join(SUPPORTED_SCHEMES) + r")://[^\s<>\"']+)"

def safe_base64_decode(s):
    s = s.strip()
    padding = len(s) % 4
    if padding:
        s += '=' * (4 - padding)
    try:
        decoded_bytes = base64.b64decode(s)
        for encoding in ['utf-8', 'gbk', 'latin1']:
            try:
                return decoded_bytes.decode(encoding)
            except UnicodeDecodeError:
                continue
    except Exception:
        pass
    return ""

def generate_time_node():
    """生成带有当前月日时（格式：02-13-14 或 10-03-08）的占位空节点"""
    beijing_time = datetime.now(timezone.utc) + timedelta(hours=8)
    time_str = beijing_time.strftime('%m-%d-%H')
    
    dummy_vmess_data = {
        "v": "2",
        "ps": time_str,
        "add": "0.0.0.0",
        "port": 0,
        "id": "00000000-0000-0000-0000-000000000000",
        "aid": 0,
        "scy": "auto",
        "net": "tcp",
        "type": "none",
        "host": "",
        "path": "",
        "tls": ""
    }
    encoded_json = base64.b64encode(json.dumps(dummy_vmess_data).encode('utf-8')).decode('utf-8')
    return f"vmess://{encoded_json}"

def extract_node_address(node_str):
    """提取节点的真实服务器 IP 或域名地址"""
    try:
        if node_str.lower().startswith('vmess://'):
            decoded_json_str = safe_base64_decode(node_str.split('://')[1].split('#')[0].split('?')[0])
            if decoded_json_str:
                node_data = json.loads(decoded_json_str)
                addr = node_data.get('add')
                if addr:
                    return str(addr).strip()
        
        clean_str = node_str.split('#')[0].split('?')[0]
        parsed = urlparse(clean_str)
        netloc = parsed.netloc or clean_str.split('://')[-1]
        if '@' in netloc: 
            netloc = netloc.split('@')[-1]
        if ':' in netloc:
            host = netloc.rsplit(':', 1)[0]
        else:
            host = netloc
        return host.strip('[]').strip()
    except Exception:
        return ""

def rename_node_to_address(node_str):
    """将节点的备注名称(#后的部分)修改为与节点服务器地址一模一样"""
    addr = extract_node_address(node_str)
    if not addr:
        return node_str
        
    if node_str.lower().startswith('vmess://'):
        try:
            raw_body = node_str.split('://')[1].split('#')[0].split('?')[0]
            decoded_json_str = safe_base64_decode(raw_body)
            if decoded_json_str:
                node_data = json.loads(decoded_json_str)
                node_data['ps'] = addr
                encoded_json = base64.b64encode(json.dumps(node_data, ensure_ascii=False).encode('utf-8')).decode('utf-8')
                return f"vmess://{encoded_json}"
        except Exception:
            pass

    base_url = node_str.rsplit('#', 1)[0]
    return f"{base_url}#{urllib.parse.quote(addr)}"

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
            if '[t.me]' in line.lower():
                current_group = 't.me'
                continue
            elif '[github]' in line.lower():
                current_group = 'github'
                continue
            elif '[chat]' in line.lower():
                current_group = 'chat'
                continue
            
            if line.startswith('http://') or line.startswith('https://'):
                if current_group == 't.me': t_me_links.append(line)
                elif current_group == 'github': github_links.append(line)
                elif current_group == 'chat': chat_links.append(line)
    return t_me_links, github_links, chat_links

def filter_tme_messages_by_days(html_content, days_limit):
    if days_limit <= 0:
        return html_content
    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(days=days_limit)
    message_blocks = re.split(r'(?=<div class="tgme_widget_message\s)', html_content)
    filtered_html = ""
    for block in message_blocks:
        time_match = re.search(r'datetime="([^"]+)"', block)
        if time_match:
            try:
                msg_time = datetime.fromisoformat(time_match.group(1).replace('Z', '+00:00'))
                if msg_time >= cutoff_time:
                    filtered_html += block + "\n"
            except Exception:
                filtered_html += block + "\n"
        else:
            filtered_html += block + "\n"
    return filtered_html

def is_download_link(url_str):
    ignored_extensions = (
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico', '.tif', '.tiff',
        '.apk', '.exe', '.dmg', '.pkg', '.deb', '.rpm', '.msi', '.bin', '.iso', '.img',
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.zst', '.cab',
        '.mp3', '.mp4', '.avi', '.mkv', '.mov', '.flv', '.wav', '.aac', '.flac', '.wmv', '.webm',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.odt', '.ods', '.odp',
        '.txt', '.json', '.xml', '.csv', '.log', '.md', '.yaml', '.yml',
        '.ttf', '.otf', '.woff', '.woff2', '.eot',
        '.torrent', '.sql', '.db', '.sqlite', '.bak', '.jar', '.war', '.class'
    )
    parsed_path = urlparse(url_str).path.lower()
    return any(parsed_path.endswith(ext) for ext in ignored_extensions)

def fetch_single_url(url, headers):
    print(f"[-] 正在抓取: {url}")
    
    def perform_request(target_url):
        try:
            resp = GLOBAL_SESSION.get(target_url, headers=headers, timeout=12, proxies=PROXIES)
            if resp.status_code == 200:
                page_text = resp.text
                extracted_subs = []
                
                if "t.me" in target_url:
                    page_text = filter_tme_messages_by_days(page_text, DAYS_LIMIT)
                    all_links = re.findall(r'https?://[^\s<>"\']+', page_text, re.IGNORECASE)
                    for link in all_links:
                        link = link.rstrip('.,;\'">)')
                        if any(x in link for x in ["t.me", "telegram.org", "w3.org"]):
                            continue
                        if is_download_link(link):
                            continue
                        if re.match(PROTOCOL_REGEX_STR, link, re.IGNORECASE):
                            continue
                        keywords = ['sub', 'token', 'api', 'v2ray', 'clash', 'custom', 'subscribe', 'list', 'config', 'profile']
                        if any(kw in link.lower() for kw in keywords):
                            converted_sub = link if CONVERT_API in link else CONVERT_API + urllib.parse.quote(link, safe='')
                            extracted_subs.append(converted_sub)
                        elif re.search(r'/(sub|subscribe|get|list|config|profile)(/|$|\?)', link, re.IGNORECASE):
                            converted_sub = link if CONVERT_API in link else CONVERT_API + urllib.parse.quote(link, safe='')
                            extracted_subs.append(converted_sub)
                        elif any(domain in link.lower() for domain in ['v2ray', 'clash', 'ssr', 'sub', 'proxy', 'node']):
                            converted_sub = link if CONVERT_API in link else CONVERT_API + urllib.parse.quote(link, safe='')
                            extracted_subs.append(converted_sub)

                found_in_page = re.findall(PROTOCOL_REGEX_STR, page_text, re.IGNORECASE)
                decoded_page = safe_base64_decode(page_text)
                found_in_decoded = re.findall(PROTOCOL_REGEX_STR, decoded_page, re.IGNORECASE)
                
                total_found = len(set(found_in_page + found_in_decoded))
                combined_text = page_text + "\n" + decoded_page + "\n"
                return combined_text, extracted_subs, total_found
            else:
                print(f"    -> [{target_url}] 抓取失败，HTTP 状态码: {resp.status_code}")
        except Exception as e:
            print(f"    -> [{target_url}] 请求异常: {e}")
        return "", [], 0

    if "t.me" in url:
        combined_text, extracted_subs, total_found = perform_request(url)
        if total_found > 0:
            print(f"    -> [{url}] 成功获取，提取到节点: {total_found} 个")
        return combined_text, extracted_subs

    combined_text, extracted_subs, total_found = perform_request(url)
    if total_found > 0:
        print(f"    -> [{url}] 直连成功获取，提取到节点: {total_found} 个")
        return combined_text, extracted_subs

    converted_url = url if CONVERT_API in url else CONVERT_API + urllib.parse.quote(url, safe='')
    print(f"    -> [{url}] 直连未抓取到节点，使用 API 转换继续抓取: {converted_url}")
    combined_text_conv, extracted_subs_conv, total_found_conv = perform_request(converted_url)
    if total_found_conv > 0:
        print(f"    -> [{converted_url}] 转换后成功获取，提取到节点: {total_found_conv} 个")
        
    return combined_text_conv, extracted_subs_conv

def fetch_links_batch(link_list):
    if not link_list:
        return "", {}

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    
    all_raw_text = ""
    processed_urls = set()
    queue = list(link_list)
    url_details = {}

    with ThreadPoolExecutor(max_workers=FETCH_MAX_WORKERS) as executor:
        future_to_url = {}
        
        while queue:
            current_batch = [u for u in queue if u not in processed_urls]
            for u in current_batch:
                processed_urls.add(u)
            queue.clear()
            
            if not current_batch:
                break
                
            for url in current_batch:
                future_to_url[executor.submit(fetch_single_url, url, headers)] = url
                
            for future in list(as_completed(future_to_url)):
                url = future_to_url[future]
                page_text, new_subs = future.result()
                if page_text:
                    all_raw_text += page_text + "\n"
                    node_count = len(extract_nodes_from_text(page_text))
                    url_details[url] = node_count
                else:
                    url_details[url] = 0

                for sub in new_subs:
                    if sub not in processed_urls and sub not in queue:
                        queue.append(sub)
                future_to_url.pop(future, None)

    return all_raw_text, url_details

def fetch_external_us_nodes(url):
    """抓取外部 US 节点链接，支持 Base64 解码与纯文本两种方式"""
    print(f"[-] 正在获取外部 US 节点: {url}")
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    try:
        resp = GLOBAL_SESSION.get(url, headers=headers, timeout=15, proxies=PROXIES)
        if resp.status_code == 200:
            content = resp.text.strip()
            decoded_content = safe_base64_decode(content)
            
            nodes_raw = extract_nodes_from_text(content)
            nodes_decoded = extract_nodes_from_text(decoded_content) if decoded_content else []
            
            combined_external_nodes = list(set(nodes_raw + nodes_decoded))
            print(f"    -> 成功获取外部 US 节点: {len(combined_external_nodes)} 个")
            return combined_external_nodes
        else:
            print(f"    -> 外部 US 节点获取失败，HTTP 状态码: {resp.status_code}")
    except Exception as e:
        print(f"    -> 外部 US 节点请求异常: {e}")
    return []

def extract_nodes_from_text(raw_text):
    nodes = []
    pattern = re.compile(PROTOCOL_REGEX_STR, re.IGNORECASE)
    for node in pattern.findall(raw_text):
        nodes.append(node.strip().rstrip('.,;'))
    for line in raw_text.splitlines():
        line_clean = line.strip()
        if "://" in line_clean and not line_clean.startswith("http"):
            nodes.append(line_clean.rstrip('.,;'))
    return nodes

def get_country_code(node_str):
    country_mapping = {
        'US': ['us', 'usa', 'united states', 'america', '美', '美国', '洛杉矶', '圣何塞', '硅谷', '俄勒冈', '弗吉尼亚', '西雅图', '达拉斯', '芝加哥', '纽交所', '华盛顿']
    }
    
    name_part = urllib.parse.unquote(node_str.split('#')[-1]) if '#' in node_str else ""
    search_target = (name_part + " " + node_str).lower()
    
    for code, keywords in country_mapping.items():
        for kw in keywords:
            if len(kw) <= 3:
                pattern = r'(?i)(?:^|[\s\.\-_/@\^])' + re.escape(kw) + r'(?:$|[\s\.\-_/@\d])'
            else:
                pattern = r'(?i)' + re.escape(kw)
                
            if re.search(pattern, search_target):
                return code
    return "OTH"

def test_tcping(node_str):
    try:
        host, port = None, None
        if node_str.lower().startswith('vmess://'):
            decoded_json_str = safe_base64_decode(node_str.split('://')[1].split('#')[0].split('?')[0])
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
        s.settimeout(1.2)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False

def test_node_comprehensive(node_str):
    tcp_ok = test_tcping(node_str)
    return node_str, tcp_ok, tcp_ok

def make_base64_file(filename, node_list):
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(base64.b64encode("\n".join(node_list).encode('utf-8')).decode('utf-8'))

def main():
    print("========================================")
    proxy_status = f"开启 (10808)" if USE_PROXY else "关闭 (直连)"
    print(f" 开始并发抓取与解析 | 代理状态: {proxy_status} | 抓取并发: {FETCH_MAX_WORKERS} | 测活并发: {TCPING_MAX_WORKERS}")
    print("========================================")
    
    # ---------------- 1. 处理 links.txt (抓取 -> 筛选US -> 重命名) ----------------
    t_links, gh_links, chat_links = parse_links_file()
    links_batch_text, links_details = fetch_links_batch(t_links + gh_links + chat_links)
    raw_nodes_links = extract_nodes_from_text(links_batch_text)
    print(f"\n[抓取统计] links.txt 来源原始节点总数: {len(raw_nodes_links)} 个")
    
    with open('linksdetails.txt', 'w', encoding='utf-8') as f:
        f.write("========== links.txt 节点抓取明细 ==========\n")
        for url, count in links_details.items():
            f.write(f"链接: {url}\n提取节点数: {count} 个\n----------------------------------------\n")
    print(f"[提示] 已生成链接抓取明细文件: linksdetails.txt")

    us_raw_nodes = [n for n in raw_nodes_links if get_country_code(n) == 'US']
    print(f"[提示] links.txt 提取出美国 (US) 节点: {len(us_raw_nodes)} 个")
    renamed_us_nodes = [rename_node_to_address(n) for n in us_raw_nodes]
    print(f"[提示] links.txt 的 US 节点重命名完成，共 {len(renamed_us_nodes)} 个")

    # ---------------- 2. 抓取外部 US 节点 (保持原样，不重命名) ----------------
    external_us_nodes = fetch_external_us_nodes(EXTERNAL_US_URL)

    # ---------------- 3. 合并两部分节点 -> 去重 ----------------
    all_merged_nodes = renamed_us_nodes + external_us_nodes
    unique_us_nodes = list(set(all_merged_nodes))
    print(f"[提示] 合并去重完成：由总数 {len(all_merged_nodes)} 个精简为 {len(unique_us_nodes)} 个独立节点")

    # ---------------- 4. 对去重后的节点进行 TCP 测活 ----------------
    alive_us_nodes = []
    if unique_us_nodes:
        print(f"[提示] 开始对合并去重后的节点进行极速 TCP 测活 (并发数: {TCPING_MAX_WORKERS})...")
        with ThreadPoolExecutor(max_workers=TCPING_MAX_WORKERS) as executor:
            for future in as_completed({executor.submit(test_node_comprehensive, n): n for n in unique_us_nodes}):
                res_node, tcp_ok, _ = future.result()
                if tcp_ok: 
                    alive_us_nodes.append(res_node)
        print(f"[统计] 测活完毕，存活可用 US 节点数: {len(alive_us_nodes)} 个")

    # ---------------- 5. 插入时间节点并输出 US.txt ----------------
    time_node = generate_time_node()
    final_us_nodes = [time_node] + alive_us_nodes
    make_base64_file('US.txt', final_us_nodes)

    # ---------------- 统计输出 ----------------
    print("\n" + "="*40)
    print(" 全部处理完成！最终结果统计：")
    print(f" - 美国可用节点总数 (US.txt, 含1个时间占位节点): {len(final_us_nodes)} 个")
    print("========================================")

if __name__ == "__main__":
    main()
