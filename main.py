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

SUPPORTED_SCHEMES = (
    "vmess", "vless", "trojan", "ss", "ssr", 
    "hysteria", "hy2", "tuic", "anytls", 
    "juicity", "wireguard", "wg", "ssh", "socks5", "http"
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

def parse_pslinks_file():
    ps_path = 'self.txt'
    ps_tasks = []
    if not os.path.exists(ps_path):
        print(f"[提示] 未在同路径下找到 {ps_path} 文件，将跳过 self.txt 解析。")
        return ps_tasks

    with open(ps_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('http://') or line.startswith('https://'):
                ps_tasks.append(line)
                
    print(f"[提示] 成功从 self.txt 读取链接，直连抓取，共生成 {len(ps_tasks)} 个请求任务。")
    return ps_tasks

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
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico',
        '.apk', '.exe', '.dmg', '.pkg', '.deb', '.rpm', '.msi',
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
        '.mp3', '.mp4', '.avi', '.mkv', '.mov', '.flv', '.wav',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.txt', '.json', '.xml', '.csv'
    )
    parsed_path = urlparse(url_str).path.lower()
    return any(parsed_path.endswith(ext) for ext in ignored_extensions)

def fetch_single_url(url, headers):
    print(f"[-] 正在抓取: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=15, proxies=PROXIES)
        if resp.status_code == 200:
            page_text = resp.text
            extracted_subs = []
            
            if "t.me" in url:
                page_text = filter_tme_messages_by_days(page_text, DAYS_LIMIT)
                found_sub_links = re.findall(r"(https?://[^\s<>\"']+(?:sub|token|api|v2ray|clash|custom|[a-zA-Z0-9\-_./?=]+[a-zA-Z0-9\-_./?=]))", page_text, re.IGNORECASE)
                for sub_link in found_sub_links:
                    if any(x in sub_link for x in ["t.me", "telegram.org", "w3.org"]) or is_download_link(sub_link):
                        continue
                    sub_link = sub_link.rstrip('.,;\'">)')
                    converted_sub = sub_link if CONVERT_API in sub_link else CONVERT_API + urllib.parse.quote(sub_link, safe='')
                    extracted_subs.append(converted_sub)

            found_in_page = re.findall(PROTOCOL_REGEX_STR, page_text, re.IGNORECASE)
            decoded_page = safe_base64_decode(page_text)
            found_in_decoded = re.findall(PROTOCOL_REGEX_STR, decoded_page, re.IGNORECASE)
            
            total_found = len(set(found_in_page + found_in_decoded))
            if total_found > 0:
                print(f"    -> [{url}] 成功获取，提取到节点: {total_found} 个")
            
            combined_text = page_text + "\n" + decoded_page + "\n"
            return combined_text, extracted_subs
        else:
            print(f"    -> [{url}] 抓取失败，HTTP 状态码: {resp.status_code}")
    except Exception as e:
        print(f"    -> [{url}] 请求异常: {e}")
    return "", []

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

    with ThreadPoolExecutor(max_workers=50) as executor:
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

def extract_node_host(node_str):
    try:
        if node_str.lower().startswith('vmess://'):
            decoded_json_str = safe_base64_decode(node_str.split('://')[1].split('#')[0].split('?')[0])
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

def get_country_code(node_str):
    country_mapping = {
        'US': ['us', 'usa', 'united states', 'America', '美', '洛杉矶', '圣何塞', '硅谷', '俄勒冈', '弗吉尼亚', '西雅图', '达拉斯'],
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
    name_part = urllib.parse.unquote(node_str.split('#')[-1]) if '#' in node_str else ""
    search_target = (name_part + " " + node_str).lower()
    for code, keywords in country_mapping.items():
        for kw in keywords:
            pattern = r'(?i)\b' + re.escape(kw) + r'\b' if len(kw) <= 3 else r'(?i)' + re.escape(kw)
            if re.search(pattern, search_target):
                return code
    return "OTH"

def rename_node(node_str):
    country = get_country_code(node_str)
    host = extract_node_host(node_str)
    current_day = datetime.now().strftime('%d')
    new_name = f"{country}-{current_day}-{host}"
    if '#' in node_str:
        return f"{node_str.rsplit('#', 1)[0]}#{urllib.parse.quote(new_name)}"
    else:
        return f"{node_str}#{urllib.parse.quote(new_name)}"

def is_ai_friendly_node(node_str):
    return get_country_code(node_str) in {'US', 'JP', 'SG', 'KR', 'TW', 'GB', 'DE', 'FR', 'CA', 'AU'}

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
        s.settimeout(1.5)
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
    print(f" 开始并发抓取与解析 | 代理状态: {proxy_status} | 频道时间限制: 最近 {DAYS_LIMIT} 天")
    print("========================================")
    
    # ---------------- 1. 处理 links.txt (抓取 -> 重命名 -> 去重 -> TCP测活) ----------------
    t_links, gh_links, chat_links = parse_links_file()
    links_batch_text, links_details = fetch_links_batch(t_links + gh_links + chat_links)
    raw_nodes_links = extract_nodes_from_text(links_batch_text)
    print(f"\n[抓取统计] links.txt 来源原始节点总数: {len(raw_nodes_links)} 个")
    
    # 输出每个链接的节点抓取情况到 linksdetails.txt
    with open('linksdetails.txt', 'w', encoding='utf-8') as f:
        f.write("========== links.txt 节点抓取明细 ==========\n")
        for url, count in links_details.items():
            f.write(f"链接: {url}\n提取节点数: {count} 个\n----------------------------------------\n")
    print(f"[提示] 已生成链接抓取明细文件: linksdetails.txt")

    alive_nodes_links = []
    if raw_nodes_links:
        renamed_nodes = [rename_node(n) for n in raw_nodes_links]
        unique_renamed_nodes = list(set(renamed_nodes))
        print(f"[提示] 重命名后去重完成：由 {len(renamed_nodes)} 个节点去重为 {len(unique_renamed_nodes)} 个节点")
        
        print(f"[提示] 开始对 links.txt 去重后的节点进行 TCP 测活...")
        with ThreadPoolExecutor(max_workers=200) as executor:
            for future in as_completed({executor.submit(test_node_comprehensive, n): n for n in unique_renamed_nodes}):
                res_node, tcp_ok, _ = future.result()
                if tcp_ok: 
                    alive_nodes_links.append(res_node)
        print(f"[统计] links.txt 测活完毕，存活节点数: {len(alive_nodes_links)} 个")

    # 分类筛选出 US 节点、AI 节点、其他节点
    us_nodes_links = [n for n in alive_nodes_links if get_country_code(n) == 'US']
    ai_nodes_links = [n for n in alive_nodes_links if is_ai_friendly_node(n)]
    other_nodes_links = [n for n in alive_nodes_links if not is_ai_friendly_node(n)]
    
    # 导出 links.txt 生成的文件
    make_base64_file('ALL.txt', alive_nodes_links)
    make_base64_file('US.txt', us_nodes_links)
    make_base64_file('AI.txt', ai_nodes_links)
    make_base64_file('OTHER.txt', other_nodes_links)

    # ---------------- 2. 单独摘出 self.txt 处理 (抓取 -> 不测活、不重命名、不删除) ----------------
    ps_tasks = parse_pslinks_file()
    self_nodes = []
    if ps_tasks:
        raw_self_text, _ = fetch_links_batch(ps_tasks)
        raw_self_nodes = extract_nodes_from_text(raw_self_text)
        print(f"[抓取统计] self.txt 来源原始节点总数: {len(raw_self_nodes)} 个")
        if raw_self_nodes:
            self_nodes = list(set(raw_self_nodes))
            print(f"[提示] self.txt 提取后直接采用节点数: {len(self_nodes)} 个")

    # 导出单独的 self 节点文件 SUS.txt
    make_base64_file('SUS.txt', self_nodes)

    # ---------------- 3. 合并 links.txt 有效节点 + self.txt 全量节点 ----------------
    sall_nodes = list(set(alive_nodes_links + self_nodes))
    make_base64_file('SALL.txt', sall_nodes)

    # ---------------- 统计输出 ----------------
    print("\n" + "="*40)
    print(" 全部处理完成！最终结果统计：")
    print(" --- links.txt 部分 ---")
    print(f" - 有效可用节点总数 (ALL.txt):    {len(alive_nodes_links)} 个")
    print(f" - 美国节点         (US.txt):     {len(us_nodes_links)} 个")
    print(f" - AI 友好节点       (AI.txt):     {len(ai_nodes_links)} 个")
    print(f" - 其他节点         (OTHER.txt):  {len(other_nodes_links)} 个")
    print(" --- self.txt 及合并部分 ---")
    print(f" - self.txt 专属节点 (SUS.txt):    {len(self_nodes)} 个")
    print(f" - 最终合并节点总数 (SALL.txt):   {len(sall_nodes)} 个")
    print("========================================")

if __name__ == "__main__":
    main()
