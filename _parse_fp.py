import json, base64

with open('/Users/sqrtv2/Downloads/Profile №41-4-5135443/fingerprint.json') as f:
    data = json.load(f)

fp = json.loads(data['fingerprint'])

print('=== ОСНОВНЫЕ ПАРАМЕТРЫ ===')
print(f"UA: {fp['ua']}")
print(f"Platform: {fp['attr']['navigator.platform']}")
print(f"Tags: {fp['tags']}")
print(f"Screen: {fp['attr']['screen.width']}x{fp['attr']['screen.height']}")
print(f"Viewport: {fp['width']}x{fp['height']}")
print(f"DeviceMemory: {fp['attr']['deviceMemory']}GB")
print(f"HardwareConcurrency: {fp['attr']['hardwareConcurrency']}")
print(f"DevicePixelRatio: {fp['attr']['window.devicePixelRatio']}")
print(f"MaxTouchPoints: {fp['attr']['maxTouchPoints']}")
print(f"DoNotTrack: {fp['doNotTrack']}")
print(f"Lang: {fp['lang']}")
print()

print('=== WebGL ===')
wgl = fp['webgl_properties']
print(f"Vendor: {wgl['unmaskedVendor']}")
print(f"Renderer: {wgl['unmaskedRenderer']}")
print()

print('=== WebGPU ===')
wgpu = fp.get('webgpu', {})
if wgpu and wgpu.get('highPerformance'):
    info = wgpu['highPerformance']['info']
    print(f"GPU Vendor: {info['vendor']}")
    print(f"GPU Arch: {info['architecture']}")
print()

print('=== UserAgentData (decoded) ===')
uad = base64.b64decode(fp['useragentdata']).decode()
uad_obj = json.loads(uad)
print(f"Brands: {[(b['brand'], b['version']) for b in uad_obj['brands']]}")
print(f"Mobile: {uad_obj['mobile']}")
print(f"Platform: {uad_obj['platform']}")
print(f"PlatformVersion: {uad_obj['platformVersion']}")
print(f"Architecture: {uad_obj['architecture']}")
print(f"Bitness: {uad_obj['bitness']}")
print(f"FullVersion: {uad_obj['fullVersion']}")
print()

print('=== Fonts ===')
print(f"{len(fp['fonts'])} fonts")
print(f"Font_data2: {fp.get('font_data2', [])}")
print()

print('=== Spoofing flags ===')
print(f"canvas: {data['canvas']}")
print(f"webgl: {data['webgl']}")
print(f"audio: {data['audio']}")
print(f"battery: {data['battery']}")
print(f"rectangles: {data['rectangles']}")
print(f"perfectcanvas: {data['perfectcanvas']}")
print(f"sensor: {data['sensor']}")
print(f"font_data: {data['font_data']}")
print(f"device_scale: {data['device_scale']}")
print()

print('=== Connection ===')
conn = fp.get('connection', {})
print(f"effectiveType: {conn.get('effectiveType')}")
print(f"rtt: {conn.get('rtt')}")
print(f"downlink: {conn.get('downlink')}")
print()

print('=== Speech voices ===')
for v in fp.get('speech', []):
    print(f"  {v['name']} ({v['lang']}) default={v['default']}")
print()

print('=== Media devices ===')
for d in fp.get('media', {}).get('devices', []):
    print(f"  {d['kind']}: {d.get('deviceId') or '(empty)'}")
print()

print('=== CSS media ===')
css = fp.get('css', {})
print(f"  prefers-color-scheme: {css.get('prefers-color-scheme')}")
print(f"  prefers-reduced-motion: {css.get('prefers-reduced-motion')}")
print(f"  orientation: {css.get('orientation')}")
print(f"  pointer: {css.get('pointer')}")
print(f"  hover: {css.get('hover')}")
print()

print('=== Native code format ===')
print(f"  {fp.get('native_code', 'N/A')}")
print()

print('=== Key inconsistencies check ===')
# Check UA vs platform
ua = fp['ua']
platform = fp['attr']['navigator.platform']
if 'Windows' in ua and platform != 'Win32':
    print(f"  ⚠️ UA says Windows but platform={platform}")
if 'Mac' in ua and 'Mac' not in platform:
    print(f"  ⚠️ UA says Mac but platform={platform}")
if 'Linux' in ua and 'Linux' not in platform:
    print(f"  ⚠️ UA says Linux but platform={platform}")

# Check WebGL vs WebGPU vendor
webgl_vendor = wgl['unmaskedVendor']
webgl_renderer = wgl['unmaskedRenderer']
if wgpu and wgpu.get('highPerformance'):
    gpu_vendor = wgpu['highPerformance']['info']['vendor']
    gpu_arch = wgpu['highPerformance']['info']['architecture']
    print(f"  WebGL: {webgl_vendor} / {webgl_renderer}")
    print(f"  WebGPU: {gpu_vendor} / {gpu_arch}")
    if 'intel' in gpu_vendor.lower() and 'intel' not in webgl_renderer.lower() and 'mali' in webgl_renderer.lower():
        print(f"  ⚠️ WebGPU=Intel but WebGL renderer=Mali — MISMATCH!")

# Screen consistency
screen_w = fp['attr']['screen.width']
screen_h = fp['attr']['screen.height']
avail_w = fp['attr']['screen.availWidth']
avail_h = fp['attr']['screen.availHeight']
outer_w = fp['attr']['outerWidth']
outer_h = fp['attr']['outerHeight']
print(f"  Screen: {screen_w}x{screen_h}, Avail: {avail_w}x{avail_h}, Outer: {outer_w}x{outer_h}")

# UA version vs useragentdata version
if 'YaBrowser' in ua:
    print(f"  Browser: YaBrowser (Chromium-based)")
    print(f"  UA Chrome version extracted: Chrome/110")
    print(f"  UAD fullVersion: {uad_obj['fullVersion']}")
