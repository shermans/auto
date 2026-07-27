# -*- coding: utf-8 -*-
import base64
import os
import re
import socket
import urllib.parse
from urllib.parse import urlparse
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
 
USE_PROXY = False
 

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
    
    if not os.path.exists(links_path):
        print(f"[错误] 未在同路径下找到 {links_path} 文件！")
        return t_me_links, github_links

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
            
            if line.startswith('http://') or line.startswith('https://'):
                if current_group == 't.me':
                    t_me_links.append(line)
                elif current_group == 'github':
                    github_links.append(line)
                    
    return t_me_links, github_links

def fetch_and_extract():
    t_links, gh_links = parse_links_file()
    all_links = t_links + gh_links
    
    if not all_links:
        print("[错误] links.txt 中没有检测到任何有效链接！")
        return []

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
    }
    
    all_raw_text = ""
    
    for url in all_links:
        print(f"[-] 正在抓取: {url}")
        try:
            resp = requests.get(
                url, 
                headers=headers, 
                timeout=15, 
                proxies=PROXIES if USE_PROXY else None
            )
            if resp.status_code == 200:
                page_text = resp.text
                found_in_page = re.findall(r"((?:vmess|vless|trojan|ss|ssr|hysteria|hy2|tuic)://[^\s<>\"']+)", page_text, re.IGNORECASE)
                
                decoded_page = safe_base64_decode(page_text)
                found_in_decoded = re.findall(r"((?:vmess|vless|trojan|ss|ssr|hysteria|hy2|tuic)://[^\s<>\"']+)", decoded_page, re.IGNORECASE)
                
                total_found = len(set(found_in_page + found_in_decoded))
                print(f"    -> 成功获取，提取到节点: {total_found} 个")
                
                all_raw_text += page_text + "\n" + decoded_page + "\n"
            else:
                print(f"    -> 抓取失败，HTTP 状态码: {resp.status_code}")
        except Exception as e:
            print(f"    -> 请求异常: {e}")

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

def test_node_connectivity(node_str):
    try:
        parsed = urlparse(node_str)
        netloc = parsed.netloc
        if '@' in netloc:
            netloc = netloc.split('@')[-1]
        host = netloc.split(':')[0].strip('[]')
        port = int(netloc.split(':')[1]) if ':' in netloc else 443

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(1.5)
        result = s.connect_ex((host, port))
        s.close()
        return node_str if result == 0 else None
    except Exception:
        return None

def main():
    print("========================================")
    print(" 开始通过 Hidify (12334) 抓取网页...")
    print("========================================")
    raw_nodes = fetch_and_extract()
    
    # 测试之前先去重
    nodes = list(set(raw_nodes))
    print(f"\n[去重统计] 网页原始节点总数: {len(raw_nodes)} 个 | 去重后独立节点总数: {len(nodes)} 个")
    
    if len(nodes) == 0:
        print("[提示] 没有找到任何节点。")
        return

    print("\n========================================")
    print(" 正在使用 500 线程并发测试连通性...")
    print("========================================")
    
    alive_nodes = []
    with ThreadPoolExecutor(max_workers=500) as executor:
        futures = {executor.submit(test_node_connectivity, node): node for node in nodes}
        for future in as_completed(futures):
            res = future.result()
            if res:
                alive_nodes.append(res)
            
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
    print("========================================")

if __name__ == "__main__":
    main()
