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
# 全局代理配置开关：输入 'Y' 走代理，输入 'N' 不走代理
PROXY_SWITCH = 'N'  


# 自定义订阅转换解析接口
CONVERT_API = "https://edge-api-v1.ffqla.com/sub?target=mixed&url="


# 抓取 Telegram 节点及聊天链接的时间限制（单位：天）。默认值为 7，仅抓取 7 天以内的内容，超过的不抓。
DAYS_LIMIT = 7  

# ========================================================

# 根据开关自动配置 USE_PROXY 和 PROXIES
USE_PROXY = True if PROXY_SWITCH.upper() == 'Y' else False
PROXIES = {
    "http": "http://127.0.0.1:12334",
    "https": "http://127.0.0.1:12334"
} if USE_PROXY else None

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
    t_me_links = []
    github_links = []
    chat_links = []
    
    if not os.path.exists(links_path):
        print(f"[错误] 未在同路径下找到 {links_path} 文件！")
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
                if current_group == 't.me':
                    t_me_links.append(line)
                elif current_group == 'github':
                    github_links.append(line)
                elif current_group == 'chat':
                    chat_links.append(line)
                    
    return t_me_links, github_links, chat_links

def filter_tme_messages_by_days(html_content, days_limit):
    """
    针对 t.me/s/ 网页，切分出每条消息，并根据 datetime 标签判断是否在 days_limit 天以内。
    返回过滤后保留的 HTML 文本内容。
    """
    if days_limit <= 0:
        return html_content

    now = datetime.now(timezone.utc)
    cutoff_time = now - timedelta(days=days_limit)

    message_blocks = re.split(r'(?=<div class="tgme_widget_message\s)', html_content)
    filtered_html = ""
    
    kept_count = 0
    dropped_count = 0

    for block in message_blocks:
        time_match = re.search(r'datetime="([^"]+)"', block)
        if time_match:
            time_str = time_match.group(1)
            try:
                msg_time = datetime.fromisoformat(time_str.replace('Z', '+00:00'))
                if msg_time >= cutoff_time:
                    filtered_html += block + "\n"
                    kept_count += 1
                else:
                    dropped_count += 1
                    continue
            except Exception:
                filtered_html += block + "\n"
        else:
            filtered_html += block + "\n"

    print(f"    -> [时间过滤] 保留 {kept_count} 条近期消息，过滤掉 {dropped_count} 条超过 {days_limit} 天的旧消息")
    return filtered_html

def is_download_link(url_str):
    """
    判断链接是否为带常见文件后缀的下载文件（如图片、安装包、压缩包、音视频等）
    """
    ignored_extensions = (
        '.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico',
        '.apk', '.exe', '.dmg', '.pkg', '.deb', '.rpm', '.msi',
        '.zip', '.rar', '.7z', '.tar', '.gz', '.bz2',
        '.mp3', '.mp4', '.avi', '.mkv', '.mov', '.flv', '.wav',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx',
        '.txt', '.json', '.xml', '.csv'
    )
    
    parsed_path = urlparse(url_str).path.lower()
    for ext in ignored_extensions:
        if parsed_path.endswith(ext):
            return True
    return False

def fetch_single_url(url, headers):
    print(f"[-] 正在抓取: {url}")
    try:
        resp = requests.get(
            url, 
            headers=headers, 
            timeout=15, 
            proxies=PROXIES
        )
        if resp.status_code == 200:
            page_text = resp.text
            extracted_subs = []
            
            if "t.me" in url:
                page_text = filter_tme_messages_by_days(page_text, DAYS_LIMIT)
                found_sub_links = re.findall(r"(https?://[^\s<>\"']+(?:sub|token|api|v2ray|clash|custom|[a-zA-Z0-9\-_./?=]+[a-zA-Z0-9\-_./?=]))", page_text, re.IGNORECASE)
                for sub_link in found_sub_links:
                    if "t.me" in sub_link or "telegram.org" in sub_link or "w3.org" in sub_link:
                        continue
                    sub_link = sub_link.rstrip('.,;\'">)')
                    if is_download_link(sub_link):
                        continue
                    if CONVERT_API in sub_link:
                        converted_sub = sub_link
                    else:
                        converted_sub = CONVERT_API + urllib.parse.quote(sub_link, safe='')
                    extracted_subs.append(converted_sub)

            found_in_page = re.findall(r"((?:vmess|vless|trojan|ss|ssr|hysteria|hy2|tuic)://[^\s<>\"']+)", page_text, re.IGNORECASE)
            decoded_page = safe_base64_decode(page_text)
            found_in_decoded = re.findall(r"((?:vmess|vless|trojan|ss|ssr|hysteria|hy2|tuic)://[^\s<>\"']+)", decoded_page, re.IGNORECASE)
            
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

def fetch_and_extract():
    t_links, gh_links, chat_links = parse_links_file()
    all_links = t_links + gh_links + chat_links
    
    if not all_links:
        print("[错误] links.txt 中未检测到任何有效链接！")
        return []

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    
    all_raw_text = ""
    processed_urls = set()
    urls_to_fetch = []

    for original_url in all_links:
        if original_url.startswith(CONVERT_API):
            urls_to_fetch.append(original_url)
        elif original_url in chat_links or "clash" in original_url.lower() or "sub" in original_url.lower() or not original_url.endswith(('.txt', '.yaml', '.yml', '/')):
            if not original_url.startswith("https://t.me/"):
                encoded_target = urllib.parse.quote(original_url, safe='')
                urls_to_fetch.append(CONVERT_API + encoded_target)
            else:
                urls_to_fetch.append(original_url)
        else:
            urls_to_fetch.append(original_url)

    # 动态并发抓取队列
    with ThreadPoolExecutor(max_workers=50) as executor:
        future_to_url = {}
        queue = list(urls_to_fetch)
        
        while queue:
            current_batch = []
            for u in queue:
                if u not in processed_urls:
                    processed_urls.add(u)
                    current_batch.append(u)
            queue.clear()
            
            if not current_batch:
                break
                
            for url in current_batch:
                future_to_url[executor.submit(fetch_single_url, url, headers)] = url
                
            for future in as_completed(future_to_url):
                page_text, new_subs = future.result()
                if page_text:
                    all_raw_text += page_text + "\n"
                for sub in new_subs:
                    if sub not in processed_urls and sub not in queue:
                        print(f"    -> [发现聊天订阅] 提取到订阅链接并加入队列: {sub}")
                        queue.append(sub)
                future_to_url.pop(future, None)

    nodes = set()
    pattern = re.compile(r"((?:vmess|vless|trojan|ss|ssr|hysteria|hy2|tuic)://[^\s<>\"']+)", re.IGNORECASE)
    for node in pattern.findall(all_raw_text):
        nodes.add(node.strip().rstrip('.,;'))
        
    for line in all_raw_text.splitlines():
        line_clean = line.strip()
        if "://" in line_clean and not line_clean.startswith("http"):
            nodes.add(line_clean.rstrip('.,;'))
            
    return list(nodes)

def is_us_node(node_str):
    us_keywords = ['us', 'usa', 'united states', 'America', '美', '洛杉矶', '圣何塞', '硅谷', '俄勒冈', '弗吉尼亚', '西雅图', '达拉斯']
    try:
        if '#' in node_str:
            name_part = node_str.split('#')[-1]
            decoded_name = urllib.parse.unquote(name_part)
            for kw in us_keywords:
                if kw.lower() in decoded_name.lower():
                    return True
    except Exception:
        pass

    for kw in us_keywords:
        if kw in node_str.lower():
            return True
    return False

def test_tcping(node_str):
    try:
        host, port = None, None
        if node_str.lower().startswith('vmess://'):
            base64_part = node_str.split('://')[1].split('#')[0]
            decoded_json_str = safe_base64_decode(base64_part)
            if decoded_json_str:
                node_data = json.loads(decoded_json_str)
                host = node_data.get('add')
                port = int(node_data.get('port', 443))
        else:
            parsed = urlparse(node_str)
            netloc = parsed.netloc
            if '@' in netloc:
                netloc = netloc.split('@')[-1]
            host = netloc.split(':')[0].strip('[]')
            port = int(netloc.split(':')[1]) if ':' in netloc else 443

        if not host or not port:
            return False

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        result = s.connect_ex((host, port))
        s.close()
        return result == 0
    except Exception:
        return False

def test_clash_connectivity(node_str):
    try:
        resp = requests.get("http://www.gstatic.com/generate_204", proxies={"http": node_str, "https": node_str}, timeout=3)
        return resp.status_code == 204
    except Exception:
        return False

def test_node_comprehensive(node_str):
    tcp_ok = test_tcping(node_str)
    clash_ok = test_clash_connectivity(node_str) if tcp_ok else False
    return node_str, tcp_ok, clash_ok

def main():
    print("========================================")
    proxy_status = f"开启 (12334)" if USE_PROXY else "关闭 (直连)"
    print(f" 开始并发抓取网页与解析接口 | 代理状态: {proxy_status} | 频道时间限制: 最近 {DAYS_LIMIT} 天")
    print("========================================")
    raw_nodes = fetch_and_extract()
    
    nodes = list(set(raw_nodes))
    print(f"\n[去重统计] 网页原始节点总数: {len(raw_nodes)} 个 | 去重后独立节点总数: {len(nodes)} 个")
    
    if len(nodes) == 0:
        print("[提示] 没有找到任何节点。")
        return

    print("\n========================================")
    print(" 正在使用 500 线程并发测试连通性与协议可用性...")
    print("========================================")
    
    tcp_failed_count = 0
    clash_failed_count = 0
    both_alive_count = 0
    alive_nodes = []

    with ThreadPoolExecutor(max_workers=500) as executor:
        futures = {executor.submit(test_node_comprehensive, node): node for node in nodes}
        for future in as_completed(futures):
            res_node, tcp_ok, clash_ok = future.result()
            if not tcp_ok:
                tcp_failed_count += 1
            elif not clash_ok:
                clash_failed_count += 1
            else:
                both_alive_count += 1
                
            if tcp_ok:
                alive_nodes.append(res_node)

    print("\n" + "="*40)
    print(" 连通性测试报告：")
    print(f" - TCPING 不通节点数: {tcp_failed_count} 个")
    print(f" - TCP通但 CLASH 不通节点数: {clash_failed_count} 个")
    print(f" - 均通（TCP与Clash双通）节点数: {both_alive_count} 个")
    print("========================================")
        
    us_nodes = [n for n in alive_nodes if is_us_node(n)]
    other_nodes = [n for n in alive_nodes if not is_us_node(n)]
            
    def make_base64_file(filename, node_list):
        content = "\n".join(node_list)
        b64_content = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        with open(filename, 'w', encoding='utf-8') as f:
            f.write(b64_content)

    make_base64_file('ALL.txt', alive_nodes)
    make_base64_file('US.txt', us_nodes)
    make_base64_file('OTHER.txt', other_nodes)
    
    print("\n" + "="*40)
    print(" 测速完成！结果统计：")
    print(f" - 有效可用节点总数 (ALL.txt): {len(alive_nodes)} 个")
    print(f" - 其中美国节点   (US.txt):    {len(us_nodes)} 个")
    print(f" - 其中其他节点   (OTHER.txt): {len(other_nodes)} 个")
    print(f" - 频道时间天数限制:            {DAYS_LIMIT} 天")
    print("========================================")

if __name__ == "__main__":
    main()
