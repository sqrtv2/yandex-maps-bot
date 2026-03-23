"""
Browser profile generator with advanced fingerprinting capabilities.
"""
import random
import json
import hashlib
import base64
from typing import Dict, List, Optional
from fake_useragent import UserAgent
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class ProfileGenerator:
    """Generate realistic browser profiles with unique fingerprints."""

    def __init__(self):
        self.ua = UserAgent()
        self._load_fingerprint_data()

    def _load_fingerprint_data(self):
        """Load fingerprint data for generation."""
        # Всегда используем российские таймзоны и язык для Яндекс-визитов
        self.timezones = [
            "Europe/Moscow", "Europe/Moscow", "Europe/Moscow",
            "Europe/Samara", "Asia/Yekaterinburg", "Europe/Volgograd",
        ]

        self.languages = [
            "ru-RU", "ru-RU", "ru-RU", "ru-RU",
            "ru,en-US;q=0.9,en;q=0.8",
        ]

        # Desktop screen resolutions
        self.screen_resolutions = [
            (1920, 1080), (1366, 768), (1440, 900), (1600, 900),
            (1280, 1024), (1024, 768), (1280, 800), (1680, 1050),
            (2560, 1440), (3840, 2160), (2880, 1800), (1920, 1200)
        ]

        self.viewport_sizes = [
            (1920, 929), (1366, 657), (1440, 789), (1600, 789),
            (1280, 913), (1024, 657), (1280, 689), (1680, 939),
            (2560, 1329), (3840, 2049), (2880, 1689), (1920, 1089)
        ]

        # Mobile screen resolutions (portrait, width x height)
        self.mobile_screen_resolutions = [
            (360, 800), (375, 812), (390, 844), (393, 873),
            (412, 915), (414, 896), (360, 780), (384, 854),
            (411, 731), (320, 568), (375, 667), (428, 926),
        ]

        # Mobile device models for UA metadata
        self.mobile_devices = [
            {"model": "Pixel 7", "android": "14", "build": "AP2A.240805.005"},
            {"model": "Pixel 8", "android": "14", "build": "AD1A.240530.047"},
            {"model": "SM-S928B", "android": "14", "build": "UP1A.231005.007"},  # Samsung Galaxy S24 Ultra
            {"model": "SM-A546B", "android": "14", "build": "UP1A.231005.007"},  # Samsung Galaxy A54
            {"model": "SM-G998B", "android": "13", "build": "TP1A.220624.014"},  # Samsung Galaxy S21 Ultra
            {"model": "22101316G", "android": "14", "build": "UKQ1.231003.002"},  # Xiaomi 13
            {"model": "2201117TG", "android": "13", "build": "TKQ1.220829.002"},  # Xiaomi 12
            {"model": "CPH2451", "android": "13", "build": "TP1A.220905.001"},  # OPPO Reno 8
            {"model": "RMX3630", "android": "14", "build": "UP1A.231005.007"},  # Realme 11 Pro
        ]

        # Common fonts found on different systems
        self.fonts = {
            "windows": [
                "Arial", "Times New Roman", "Helvetica", "Courier New",
                "Verdana", "Georgia", "Comic Sans MS", "Trebuchet MS",
                "Impact", "Arial Black", "Tahoma", "Microsoft Sans Serif",
                "Segoe UI", "Calibri", "Cambria", "Consolas"
            ],
            "mac": [
                "Arial", "Times New Roman", "Helvetica", "Courier",
                "Verdana", "Georgia", "Monaco", "Lucida Grande",
                "Gill Sans", "Optima", "Futura", "Palatino",
                "San Francisco", "Helvetica Neue", "Avenir"
            ],
            "linux": [
                "DejaVu Sans", "Ubuntu", "Liberation Sans", "Droid Sans",
                "Bitstream Vera Sans", "FreeSans", "Nimbus Sans L",
                "Cantarell", "Open Sans", "Roboto", "Noto Sans"
            ]
        }

        self.plugins = [
            "Chrome PDF Plugin", "Chrome PDF Viewer", "Native Client",
            "Shockwave Flash", "Widevine Content Decryption Module",
            "Microsoft Silverlight", "Java Applet Plug-in",
            "QuickTime Plug-in", "VLC Web Plugin", "Adobe Acrobat"
        ]

        # WebGL vendor/renderer combinations (must match ANGLE format on Chrome)
        self.webgl_vendors = [
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) HD Graphics 4000 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Intel)", "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (NVIDIA)", "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (AMD)", "ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)"),
            ("Google Inc. (Apple)", "ANGLE (Apple, Apple M1, OpenGL 4.1)"),
            ("Google Inc. (Apple)", "ANGLE (Apple, Apple M2, OpenGL 4.1)"),
        ]

    def generate_profile(self, profile_name: str = None, is_mobile: bool = False) -> Dict:
        """Generate a complete browser profile.
        
        Args:
            profile_name: Name for the profile
            is_mobile: If True, generate a mobile (Android) profile
        """
        try:
            # Pick mobile device if needed
            device_info = None
            if is_mobile:
                device_info = random.choice(self.mobile_devices)

            profile = {
                "name": profile_name or f"Profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "created_at": datetime.utcnow().isoformat(),
                "is_mobile": is_mobile,

                # Basic browser settings
                "user_agent": self._generate_user_agent(is_mobile=is_mobile, device_info=device_info),
                "platform": self._generate_platform(is_mobile=is_mobile),
                "language": random.choice(self.languages),
                "timezone": random.choice(self.timezones),

                # Screen and viewport
                "screen": self._generate_screen_settings(is_mobile=is_mobile),
                "viewport": self._generate_viewport_settings(is_mobile=is_mobile),

                # Fingerprinting data
                "canvas_fingerprint": self._generate_canvas_fingerprint(),
                "webgl_fingerprint": self._generate_webgl_fingerprint(is_mobile=is_mobile),
                "audio_fingerprint": self._generate_audio_fingerprint(),
                "fonts": self._generate_font_list(),
                "plugins": self._generate_plugin_list(),

                # Privacy settings
                "webrtc_policy": "disable_non_proxied_udp",
                "geolocation_enabled": False,
                "notifications_enabled": False,
                "camera_enabled": False,
                "microphone_enabled": False,

                # Browser preferences
                "do_not_track": random.choice([True, False]),
                "javascript_enabled": True,
                "images_enabled": True,
                "cookies_enabled": True,

                # Advanced settings
                "hardware_concurrency": random.choice([4, 6, 8]) if is_mobile else random.choice([2, 4, 6, 8, 12, 16]),
                "device_memory": random.choice([4, 6, 8]) if is_mobile else random.choice([2, 4, 8, 16, 32]),
                "max_touch_points": random.choice([5, 10]) if is_mobile else 0,

                # Chrome-specific settings
                "chrome_extensions": [],
                "chrome_flags": self._generate_chrome_flags(),

                # Proxy settings (to be filled later)
                "proxy": None
            }

            # Mobile-specific fields
            if is_mobile and device_info:
                profile["mobile_device"] = device_info

            # Generate profile hash for identification
            profile["profile_hash"] = self._generate_profile_hash(profile)

            return profile

        except Exception as e:
            logger.error(f"Error generating profile: {e}")
            raise

    # Modern Chrome UA templates matching actual Chrome 143-145 on server
    MODERN_CHROME_UAS = [
        # Windows 10 / 11
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
        # macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_7_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
        # Linux
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Safari/537.36",
    ]

    # Mobile Chrome UA template (Android)
    # {device} is replaced with model info from mobile_devices list
    MOBILE_CHROME_UAS = [
        "Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{ver} Mobile Safari/537.36",
    ]

    # Chrome version ranges matching what's actually installed (143-145)
    CHROME_VERSIONS = [
        "143.0.7544.{patch}",
        "144.0.7612.{patch}",
        "145.0.7632.{patch}",
    ]

    def _generate_user_agent(self, is_mobile: bool = False, device_info: dict = None) -> str:
        """Generate realistic user agent matching actual installed Chrome version."""
        version_template = random.choice(self.CHROME_VERSIONS)
        patch = random.randint(40, 120)
        version = version_template.format(patch=patch)

        if is_mobile and device_info:
            template = random.choice(self.MOBILE_CHROME_UAS)
            return template.format(
                ver=version,
                android=device_info['android'],
                model=device_info['model']
            )
        else:
            template = random.choice(self.MODERN_CHROME_UAS)
            return template.format(ver=version)

    def _generate_platform(self, is_mobile: bool = False) -> str:
        """Generate platform string."""
        if is_mobile:
            return "Linux armv81"
        platforms = [
            "Win32", "MacIntel", "Linux x86_64", "Linux i686"
        ]
        return random.choice(platforms)

    def _generate_screen_settings(self, is_mobile: bool = False) -> Dict:
        """Generate screen resolution and color depth."""
        if is_mobile:
            width, height = random.choice(self.mobile_screen_resolutions)
            return {
                "width": width,
                "height": height,
                "color_depth": 24,
                "pixel_ratio": random.choice([2, 2.5, 3, 3.5]),
                "orientation": "portrait-primary"
            }

        width, height = random.choice(self.screen_resolutions)

        return {
            "width": width,
            "height": height,
            "color_depth": random.choice([24, 32]),
            "pixel_ratio": random.choice([1, 1.25, 1.5, 2]),
            "orientation": "landscape-primary"
        }

    def _generate_viewport_settings(self, is_mobile: bool = False) -> Dict:
        """Generate viewport size based on screen resolution."""
        if is_mobile:
            width, height = random.choice(self.mobile_screen_resolutions)
            # Mobile viewport is usually screen size minus status/nav bars
            viewport_height = height - random.randint(50, 80)
            return {
                "width": width,
                "height": viewport_height
            }

        screen_width = random.choice([res[0] for res in self.screen_resolutions])
        # Viewport is usually slightly smaller than screen
        viewport_width = screen_width - random.randint(0, 100)
        viewport_height = random.randint(600, 1200)

        return {
            "width": viewport_width,
            "height": viewport_height
        }

    def _generate_canvas_fingerprint(self) -> str:
        """Generate unique canvas fingerprint."""
        try:
            # Simulate canvas rendering variations
            base_data = f"Canvas_{random.randint(1000000, 9999999)}"
            # Add some randomness that would come from actual canvas rendering
            noise = random.random() * 0.001
            fingerprint_data = f"{base_data}_{noise}"

            # Create hash
            return hashlib.md5(fingerprint_data.encode()).hexdigest()

        except Exception:
            return hashlib.md5(f"fallback_{random.randint(1000000, 9999999)}".encode()).hexdigest()

    def _generate_webgl_fingerprint(self, is_mobile: bool = False) -> Dict:
        """Generate WebGL fingerprint data."""
        if is_mobile:
            mobile_webgl = [
                ("Qualcomm", "Adreno (TM) 730"),
                ("Qualcomm", "Adreno (TM) 740"),
                ("Qualcomm", "Adreno (TM) 660"),
                ("ARM", "Mali-G710 MC10"),
                ("ARM", "Mali-G78 MC20"),
                ("Imagination Technologies", "PowerVR GE8320"),
            ]
            vendor, renderer = random.choice(mobile_webgl)
        else:
            vendor, renderer = random.choice(self.webgl_vendors)

        return {
            "vendor": vendor,
            "renderer": renderer,
            "version": f"OpenGL ES 2.0 ({renderer})",
            "shading_language_version": "WebGL GLSL ES 1.0",
            "max_texture_size": random.choice([4096, 8192, 16384]),
            "max_vertex_attribs": random.choice([16, 32]),
            "max_viewport_dims": random.choice([4096, 8192, 16384]),
            "aliased_line_width_range": [1, 1],
            "aliased_point_size_range": [1, random.choice([511, 1023, 8192])],
            "max_fragment_uniform_vectors": random.choice([256, 512, 1024]),
            "max_vertex_uniform_vectors": random.choice([256, 512, 1024])
        }

    def _generate_audio_fingerprint(self) -> str:
        """Generate audio context fingerprint."""
        # Simulate audio context variations
        sample_rate = random.choice([44100, 48000])
        base_frequency = random.choice([440, 523.251, 659.255])  # A4, C5, E5 notes

        # Create unique audio fingerprint
        audio_data = f"AudioContext_{sample_rate}_{base_frequency}_{random.random()}"
        return hashlib.md5(audio_data.encode()).hexdigest()

    def _generate_font_list(self) -> List[str]:
        """Generate list of available fonts."""
        platform = self._get_platform_from_ua()
        base_fonts = self.fonts.get(platform, self.fonts["windows"])

        # Randomly include/exclude fonts to create variation
        font_list = []
        for font in base_fonts:
            if random.random() > 0.2:  # 80% chance to include each font
                font_list.append(font)

        # Add some random system fonts
        additional_fonts = [
            "Arial Unicode MS", "Book Antiqua", "Bookman Old Style",
            "Century Gothic", "Century Schoolbook", "Garamond"
        ]

        for font in additional_fonts:
            if random.random() > 0.7:  # 30% chance to include additional fonts
                font_list.append(font)

        return sorted(font_list)

    def _generate_plugin_list(self) -> List[Dict]:
        """Generate list of browser plugins."""
        plugin_list = []

        for plugin in self.plugins:
            if random.random() > 0.3:  # 70% chance to include each plugin
                plugin_data = {
                    "name": plugin,
                    "description": f"{plugin} plugin",
                    "filename": f"{plugin.lower().replace(' ', '_')}.dll",
                    "version": f"{random.randint(1, 30)}.{random.randint(0, 9)}.{random.randint(0, 999)}"
                }
                plugin_list.append(plugin_data)

        return plugin_list

    def _generate_chrome_flags(self) -> List[str]:
        """Generate Chrome command line flags for stealth.
        
        Only include flags that a normal Chrome user might have.
        """
        flags = [
            "--disable-features=TranslateUI",
        ]
        return flags

    def _get_platform_from_ua(self) -> str:
        """Determine platform from user agent."""
        # This is simplified - in real implementation would parse UA
        return random.choice(["windows", "mac", "linux"])

    def _generate_profile_hash(self, profile: Dict) -> str:
        """Generate unique hash for profile identification."""
        # Create hash from key profile characteristics
        key_data = {
            "user_agent": profile["user_agent"],
            "screen": profile["screen"],
            "timezone": profile["timezone"],
            "language": profile["language"],
            "canvas": profile["canvas_fingerprint"],
            "webgl": profile["webgl_fingerprint"]["vendor"]
        }

        hash_string = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(hash_string.encode()).hexdigest()[:16]

    def generate_multiple_profiles(self, count: int) -> List[Dict]:
        """Generate multiple unique profiles."""
        profiles = []
        used_hashes = set()

        for i in range(count):
            attempts = 0
            while attempts < 10:  # Max 10 attempts to generate unique profile
                profile = self.generate_profile(f"Profile_{i+1}")

                if profile["profile_hash"] not in used_hashes:
                    profiles.append(profile)
                    used_hashes.add(profile["profile_hash"])
                    break

                attempts += 1

            if attempts >= 10:
                logger.warning(f"Could not generate unique profile #{i+1}")
                # Add it anyway with modified name
                profile["name"] = f"Profile_{i+1}_duplicate"
                profiles.append(profile)

        return profiles

    def update_profile_fingerprints(self, profile: Dict) -> Dict:
        """Update fingerprints for existing profile to make it fresh."""
        profile["canvas_fingerprint"] = self._generate_canvas_fingerprint()
        profile["webgl_fingerprint"] = self._generate_webgl_fingerprint()
        profile["audio_fingerprint"] = self._generate_audio_fingerprint()
        profile["profile_hash"] = self._generate_profile_hash(profile)
        profile["updated_at"] = datetime.utcnow().isoformat()

        return profile