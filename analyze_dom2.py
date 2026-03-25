#!/usr/bin/env python3
"""Deep analysis of search results DOM to find organic vs recommendation blocks"""
import re

html = open('screenshots/notfound_debug2.html', 'r', errors='replace').read()

print(f"=== Total HTML size: {len(html)} chars ===")
print(f"Title: {re.search(r'<title>(.*?)</title>', html).group(1)}")
print()

# Find ALL serp-item elements
serp_items = list(re.finditer(r'<li[^>]*class="serp-item([^"]*)"[^>]*data-cid="(\d+)"', html))
print(f"=== Found {len(serp_items)} serp-item elements ===")

for m in serp_items:
    extra_class = m.group(1)
    cid = m.group(2)
    start = m.start()
    
    # Get 5000 chars after the start to find all relevant info
    snippet = html[start:start+5000]
    
    # Find OrganicTitle link
    title_match = re.search(r'class="OrganicTitle[^"]*"[^>]*>.*?<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', snippet[:3000], re.DOTALL)
    title = re.sub(r'<[^>]+>', '', title_match.group(2)).strip()[:60] if title_match else 'NO TITLE'
    href = title_match.group(1)[:80] if title_match else 'NO HREF'
    
    # Find green URL / Path
    path_match = re.search(r'class="[^"]*Path[^"]*"[^>]*>.*?</div>', snippet[:3000], re.DOTALL)
    green_text = ''
    if path_match:
        green_text = re.sub(r'<[^>]+>', '', path_match.group(0)).strip()[:50]
    
    # Check for special classes
    has_rsya = 'RsyaGuarantee' in extra_class
    has_extra_width = 'extra-width' in extra_class
    has_card = 'card' in extra_class
    
    # Check data-log-node
    log_node_match = re.search(r'data-log-node="([^"]*)"', snippet[:200])
    log_node = log_node_match.group(1) if log_node_match else 'none'
    
    # Check for VanillaReact wrapper inside (recommendation blocks often use this)
    has_vanilla = 'VanillaReact' in snippet[:500]
    
    # Check if the Organic content has snippet text (real results usually do)
    has_organic_text = 'Organic-Subtitle' in snippet[:3000] or 'OrganicText' in snippet[:3000]
    
    # Check if the title contains relevant keywords
    has_ph_keyword = any(kw in title.lower() for kw in ['ph', 'тестер', 'метр', 'кислот', 'вод', 'анализ', 'измер'])
    
    print(f'CID={cid}: {"💰RSYA " if has_rsya else ""}{"📦EXTRA " if has_extra_width else ""}')
    print(f'  log-node: {log_node}')
    print(f'  title: {title}')
    print(f'  green: {green_text}')
    print(f'  href: {href}')
    print(f'  has_snippet: {has_organic_text}, relevant: {has_ph_keyword}')
    print()

# Also check if there's a serp-list element and how many direct children it has
serp_list_match = re.search(r'<(ol|ul)[^>]*class="[^"]*serp-list[^"]*"', html)
if serp_list_match:
    tag = serp_list_match.group(1)
    start = serp_list_match.start()
    # Count direct li children
    snippet = html[start:start+50000]
    li_count = len(re.findall(r'<li[^>]*class="serp-item', snippet))
    print(f"\n=== serp-list ({tag}): contains {li_count} serp-items ===")
