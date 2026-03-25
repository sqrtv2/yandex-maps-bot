#!/usr/bin/env python3
"""Analyze DOM dump to understand data-cid blocks"""
import re

html = open('screenshots/notfound_debug.html', 'r', errors='replace').read()

cids = list(re.finditer(r'data-cid="(\d+)"', html))
for m in cids:
    cid = m.group(1)
    start = max(0, m.start() - 50)
    end = min(len(html), m.start() + 3000)
    snippet = html[start:end]
    
    has_organic_title = 'OrganicTitle' in snippet[:2000]
    has_organic_class = 'class="Organic organic' in snippet[:500]
    has_snippet_text = 'OrganicText' in snippet[:2000] or 'Organic-ContentWrapper' in snippet[:2000]
    
    # Extract green URL
    path_match = re.search(r'class="[^"]*Path[^"]*"', snippet[:2000])
    green_url_area = ''
    if path_match:
        area = snippet[path_match.start():path_match.start()+300]
        urls = re.findall(r'>([a-z0-9][\w.-]+\.\w{2,})', area)
        green_url_area = urls[0] if urls else ''
    
    # Title text
    title_match = re.search(r'OrganicTitle[^>]*>.*?<a[^>]*>(.*?)</a>', snippet[:2000], re.DOTALL)
    title_text = ''
    if title_match:
        title_text = re.sub(r'<[^>]+>', '', title_match.group(1)).strip()[:60]
    
    # Check for Subtitle (description snippet)
    has_subtitle = 'Organic-Subtitle' in snippet or 'OrganicText' in snippet
    
    # Block type markers
    block_types = []
    for marker in ['Organic-OfferThumb', 'RequestMeta', 'VanillaReact', 'Pager', 'Sitelinks', 'AdvLabel', 'Direct']:
        if marker in snippet[:2000]:
            block_types.append(marker)
    
    print(f'CID={cid}: organic={has_organic_class}, title_link={has_organic_title}, has_subtitle={has_subtitle}')
    print(f'  title: {title_text}')
    print(f'  green_url: {green_url_area}')
    print(f'  block_types: {block_types}')
    print()
