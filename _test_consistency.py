#!/usr/bin/env python3
"""Test profile generation consistency after fixes."""
from core.profile_generator import ProfileGenerator

pg = ProfileGenerator()

for i in range(10):
    p = pg.generate_profile(f"Test-{i}")
    ua = p["user_agent"]
    plat = p["platform"]
    webgl_renderer = p["webgl_fingerprint"]["unmaskedRenderer"]
    webgpu_vendor = p["webgpu_fingerprint"]["highPerformance"]["info"]["vendor"]
    screen_w = p["screen"]["width"]
    screen_h = p["screen"]["height"]
    vp_w = p["viewport"]["width"]
    vp_h = p["viewport"]["height"]
    voices = p["speech_voices"][0]["name"] if p["speech_voices"] else "NONE"
    os_fam = p.get("os_family", "?")
    sys_color_caption = p["system_colors"].get("ActiveCaption", "?")

    errors = []
    if "Windows" in ua and plat != "Win32":
        errors.append(f"UA=Windows but platform={plat}")
    if "Macintosh" in ua and plat != "MacIntel":
        errors.append(f"UA=Mac but platform={plat}")
    if "Linux" in ua and "Linux" not in plat:
        errors.append(f"UA=Linux but platform={plat}")
    if vp_w > screen_w:
        errors.append(f"viewport_w({vp_w}) > screen_w({screen_w})")
    if vp_h > screen_h:
        errors.append(f"viewport_h({vp_h}) > screen_h({screen_h})")
    if plat == "MacIntel" and "Apple" not in webgl_renderer:
        errors.append(f"Mac but GPU={webgl_renderer}")
    if plat == "Win32" and "Apple" in webgl_renderer:
        errors.append(f"Win but Apple GPU")
    if plat == "MacIntel" and "Microsoft" in voices:
        errors.append(f"Mac but Windows voice")
    if plat == "Win32" and voices in ("Milena", "Yuri", "Samantha"):
        errors.append(f"Win but Mac voice")
    # WebGPU consistency
    if "Apple" in webgl_renderer and webgpu_vendor != "apple":
        errors.append(f"Apple GPU but WebGPU={webgpu_vendor}")
    if "NVIDIA" in webgl_renderer and webgpu_vendor != "nvidia":
        errors.append(f"NVIDIA GPU but WebGPU={webgpu_vendor}")
    if "AMD" in webgl_renderer and webgpu_vendor != "amd":
        errors.append(f"AMD GPU but WebGPU={webgpu_vendor}")
    if "Intel" in webgl_renderer and webgpu_vendor not in ("google", "intel"):
        errors.append(f"Intel GPU but WebGPU={webgpu_vendor}")
    # System colors per OS
    if plat == "MacIntel" and sys_color_caption == "rgb(153, 180, 209)":
        errors.append("Mac but Windows system colors")

    status = "OK" if not errors else "FAIL: " + "; ".join(errors)
    print(f"{os_fam:7s} | {plat:14s} | {screen_w}x{screen_h} vp={vp_w}x{vp_h} | {webgl_renderer[:45]:45s} | gpu={webgpu_vendor:8s} | {voices[:25]:25s} | {status}")
