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
        self.timezones = [
            "Europe/Moscow", "Europe/London", "Europe/Berlin", "Europe/Paris",
            "America/New_York", "America/Chicago", "America/Los_Angeles",
            "Asia/Tokyo", "Asia/Shanghai", "Asia/Seoul", "Asia/Bangkok",
            "Australia/Sydney", "Europe/Kiev", "Europe/Warsaw"
        ]

        self.languages = [
            "en-US", "ru-RU", "de-DE", "fr-FR", "es-ES", "it-IT",
            "ja-JP", "ko-KR", "zh-CN", "pt-BR", "pl-PL", "nl-NL"
        ]

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

        # WebGL vendor/renderer combinations
        self.webgl_vendors = [
            ("Google Inc.", "ANGLE (Intel HD Graphics 4000 Direct3D11 vs_5_0 ps_5_0)"),
            ("Google Inc.", "ANGLE (NVIDIA GeForce GTX 1060 Direct3D11 vs_5_0 ps_5_0)"),
            ("Google Inc.", "ANGLE (AMD Radeon R9 200 Series Direct3D11 vs_5_0 ps_5_0)"),
            ("Mozilla", "Intel Open Source Technology Center Mesa DRI Intel(R) HD Graphics"),
            ("WebKit", "AMD Radeon Pro 560X OpenGL Engine"),
            ("WebKit", "Intel(R) Iris(TM) Plus Graphics 640"),
        ]

    def generate_profile(self, profile_name: str = None) -> Dict:
        """Generate a complete browser profile."""
        try:
            profile = {
                "name": profile_name or f"Profile_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                "created_at": datetime.utcnow().isoformat(),

                # Basic browser settings
                "user_agent": self._generate_user_agent(),
                "platform": self._generate_platform(),
                "language": random.choice(self.languages),
                "timezone": random.choice(self.timezones),

                # Screen and viewport
                "screen": self._generate_screen_settings(),
                "viewport": self._generate_viewport_settings(),

                # Fingerprinting data
                "canvas_fingerprint": self._generate_canvas_fingerprint(),
                "webgl_fingerprint": self._generate_webgl_fingerprint(),
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
                "hardware_concurrency": random.choice([2, 4, 6, 8, 12, 16]),
                "device_memory": random.choice([2, 4, 8, 16, 32]),
                "max_touch_points": 0,  # Desktop profile

                # Chrome-specific settings
                "chrome_extensions": [],
                "chrome_flags": self._generate_chrome_flags(),

                # Proxy settings (to be filled later)
                "proxy": None
            }

            # Generate profile hash for identification
            profile["profile_hash"] = self._generate_profile_hash(profile)

            return profile

        except Exception as e:
            logger.error(f"Error generating profile: {e}")
            raise

    def _generate_user_agent(self) -> str:
        """Generate realistic user agent string."""
        try:
            # Get random user agent
            ua_string = self.ua.random

            # Occasionally modify version numbers to make unique
            if random.random() < 0.3:
                # Slightly modify Chrome version
                import re
                chrome_match = re.search(r'Chrome/(\d+)\.(\d+)\.(\d+)\.(\d+)', ua_string)
                if chrome_match:
                    major, minor, build, patch = chrome_match.groups()
                    new_build = str(int(build) + random.randint(-10, 10))
                    new_patch = str(int(patch) + random.randint(-50, 50))
                    ua_string = ua_string.replace(
                        f"Chrome/{major}.{minor}.{build}.{patch}",
                        f"Chrome/{major}.{minor}.{new_build}.{new_patch}"
                    )

            return ua_string

        except Exception:
            # Fallback user agent
            return "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"

    def _generate_platform(self) -> str:
        """Generate platform string."""
        platforms = [
            "Win32", "MacIntel", "Linux x86_64", "Linux i686"
        ]
        return random.choice(platforms)

    def _generate_screen_settings(self) -> Dict:
        """Generate screen resolution and color depth."""
        width, height = random.choice(self.screen_resolutions)

        return {
            "width": width,
            "height": height,
            "color_depth": random.choice([24, 32]),
            "pixel_ratio": random.choice([1, 1.25, 1.5, 2]),
            "orientation": "landscape-primary"
        }

    def _generate_viewport_settings(self) -> Dict:
        """Generate viewport size based on screen resolution."""
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

    def _generate_webgl_fingerprint(self) -> Dict:
        """Generate WebGL fingerprint data."""
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
        """Generate Chrome command line flags for stealth."""
        flags = [
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-renderer-backgrounding",
            "--disable-backgrounding-occluded-windows",
            "--disable-features=TranslateUI",
            "--disable-ipc-flooding-protection",
            "--disable-background-networking",
            "--disable-client-side-phishing-detection",
            "--disable-default-apps",
            "--disable-hang-monitor",
            "--disable-popup-blocking",
            "--disable-prompt-on-repost",
            "--disable-sync",
            "--disable-web-security",
            "--metrics-recording-only",
            "--no-sandbox",
            "--safebrowsing-disable-auto-update",
            "--use-mock-keychain",
            "--disable-dev-shm-usage"
        ]

        # Randomly include/exclude some flags
        selected_flags = []
        for flag in flags:
            if random.random() > 0.2:  # 80% chance to include each flag
                selected_flags.append(flag)

        return selected_flags

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