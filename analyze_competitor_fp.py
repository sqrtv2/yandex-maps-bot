#!/usr/bin/env python3
"""Analyze competitor fingerprint.json"""
import json, sys

with open("/Users/sqrtv2/Downloads/Profile №41-4-5135443/fingerprint.json", "r") as f:
    fp = json.load(f)

fdata = json.loads(fp["fingerprint"])

print("=== UA ===")
print(fdata.get("ua", "")[:150])

print("\n=== WEBGL PROPERTIES ===")
wp = fdata.get("webgl_properties", {})
if isinstance(wp, list):
    print("webgl_properties keys:", wp)
elif isinstance(wp, dict):
    for k2, v2 in wp.items():
        print(f"  {k2}: {str(v2)[:120]}")

print("\n=== WEBGPU ===")
wgpu = fdata.get("webgpu", {})
if isinstance(wgpu, list):
    print("webgpu keys:", wgpu)
elif isinstance(wgpu, dict):
    for k2, v2 in wgpu.items():
        print(f"  {k2}: {str(v2)[:120]}")

print("\n=== SPEECH ===")
for s in fdata.get("speech", []):
    print(f"  {s}")

print("\n=== FEATURES ===")
print(fdata.get("features"))

print("\n=== AUDIO PROPERTIES ===")
print(fdata.get("audio_properties"))

print("\n=== CSS ===")
print(fdata.get("css"))

print("\n=== FONTS ===")
fonts = fdata.get("fonts", [])
print(f"Font count: {len(fonts)}")
print(f"First 10: {fonts[:10]}")

print("\n=== CODECS ===")
for c in fdata.get("codecs", []):
    print(f"  {c}")

print("\n=== KEYBOARD ===")
kb = fdata.get("keyboard", [])
print(f"Keyboard count: {len(kb)}")

print("\n=== CONNECTION ===")
print(fdata.get("connection"))

print("\n=== TAGS ===")
print(fdata.get("tags"))

print("\n=== USERAGENTDATA ===")
uad = fdata.get("useragentdata", "")
if uad:
    import base64
    try:
        decoded = base64.b64decode(uad).decode("utf-8")
        uad_json = json.loads(decoded)
        print(json.dumps(uad_json, indent=2)[:500])
    except Exception:
        print(uad[:100])

print("\n=== HEADERS ===")
headers = fdata.get("headers", [])
print(f"Header count: {len(headers)}")
print(f"Headers: {headers}")

print("\n=== PLUGINS ===")
plugins = fdata.get("plugins", [])
for p in plugins:
    print(f"  {p.get('filename', '?')}: {p.get('description', '?')}")

print("\n=== MIMES ===")
mimes = fdata.get("mimes", [])
for m in mimes:
    print(f"  {m}")

print("\n=== STORAGE ===")
print(fdata.get("storage"))

print("\n=== HEAP ===")
print(fdata.get("heap"))

print("\n=== SYSTEMCOLORS ===")
sc = fdata.get("systemcolors", [])
print(f"Count: {len(sc)}")
print(sc[:5])

print("\n=== SYSTEMFONTS ===")
print(fdata.get("systemfonts"))

print("\n=== WEBRTC CODECS ===")
print(fdata.get("webrtc_codecs"))
