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

        # Mobile device models for UA metadata — popular in Russia
        self.mobile_devices = [
            # Samsung
            {"model": "SM-S928B", "android": "15", "build": "AP3A.250105.002"},  # Galaxy S24 Ultra
            {"model": "SM-S926B", "android": "15", "build": "AP3A.250105.002"},  # Galaxy S24+
            {"model": "SM-S921B", "android": "15", "build": "AP3A.250105.002"},  # Galaxy S24
            {"model": "SM-A556B", "android": "15", "build": "AP3A.250105.002"},  # Galaxy A55
            {"model": "SM-A546B", "android": "14", "build": "UP1A.231005.007"},  # Galaxy A54
            {"model": "SM-S911B", "android": "14", "build": "UP1A.231005.007"},  # Galaxy S23
            {"model": "SM-A155F", "android": "14", "build": "UP1A.231005.007"},  # Galaxy A15
            # Xiaomi / Redmi / POCO
            {"model": "2407FPN8EG", "android": "14", "build": "UKQ1.240118.001"},  # Xiaomi 14T
            {"model": "23129RAA4G", "android": "14", "build": "UKQ1.231003.002"},  # Redmi Note 13 Pro+
            {"model": "2312DRA50G", "android": "14", "build": "UKQ1.231003.002"},  # POCO X6 Pro
            {"model": "22101316G", "android": "14", "build": "UKQ1.231003.002"},  # Xiaomi 13
            {"model": "2409BPN8EG", "android": "15", "build": "AP3A.250105.002"},  # Xiaomi 15
            # Google Pixel
            {"model": "Pixel 8", "android": "15", "build": "AP3A.250105.002"},
            {"model": "Pixel 8a", "android": "15", "build": "AP3A.250105.002"},
            {"model": "Pixel 9", "android": "15", "build": "AP3A.250105.002"},
            # Realme
            {"model": "RMX3890", "android": "14", "build": "UP1A.231005.007"},  # Realme GT5 Pro
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

        # ============================================================
        # GPU DATABASE — complete WebGL1/WebGL2 profiles per GPU
        # Each entry contains ALL parameters that fingerprinting
        # scripts check: vendor, renderer, limits, precision,
        # extensions, context attributes, etc.
        # Values are taken from real device measurements.
        # ============================================================
        self._gpu_profiles_desktop = self._build_gpu_database_desktop()
        self._gpu_profiles_mobile = self._build_gpu_database_mobile()

    @staticmethod
    def _webgl_context_defaults():
        """Default WebGL context attributes (constant across GPUs)."""
        return {
            "alpha": True, "antialias": True, "depth": True,
            "desynchronized": False, "failIfMajorPerformanceCaveat": False,
            "powerPreference": "default", "premultipliedAlpha": True,
            "preserveDrawingBuffer": False, "stencil": False, "xrCompatible": False,
        }

    @staticmethod
    def _shader_precision_desktop():
        """Shader precision values typical for desktop GPUs."""
        return {
            "vertexShaderHighFloat":   {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "vertexShaderMediumFloat": {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "vertexShaderLowFloat":    {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "fragmentShaderHighFloat":   {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "fragmentShaderMediumFloat": {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "fragmentShaderLowFloat":    {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "vertexShaderHighInt":   {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "vertexShaderMediumInt": {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "vertexShaderLowInt":    {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "fragmentShaderHighInt":   {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "fragmentShaderMediumInt": {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "fragmentShaderLowInt":    {"precision": 0, "rangeMin": 31, "rangeMax": 31},
        }

    @staticmethod
    def _shader_precision_mobile():
        """Shader precision values typical for mobile GPUs (medium/low float differ)."""
        return {
            "vertexShaderHighFloat":   {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "vertexShaderMediumFloat": {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "vertexShaderLowFloat":    {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "fragmentShaderHighFloat":   {"precision": 23, "rangeMin": 127, "rangeMax": 127},
            "fragmentShaderMediumFloat": {"precision": 10, "rangeMin": 15, "rangeMax": 15},
            "fragmentShaderLowFloat":    {"precision": 10, "rangeMin": 15, "rangeMax": 15},
            "vertexShaderHighInt":   {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "vertexShaderMediumInt": {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "vertexShaderLowInt":    {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "fragmentShaderHighInt":   {"precision": 0, "rangeMin": 31, "rangeMax": 31},
            "fragmentShaderMediumInt": {"precision": 0, "rangeMin": 15, "rangeMax": 15},
            "fragmentShaderLowInt":    {"precision": 0, "rangeMin": 15, "rangeMax": 15},
        }

    def _build_gpu_database_desktop(self) -> list:
        """Build complete GPU database for desktop devices."""
        ctx_defaults = self._webgl_context_defaults()
        precision = self._shader_precision_desktop()

        # Common desktop WebGL1 extensions
        ext_desktop = (
            "ANGLE_instanced_arrays,EXT_blend_minmax,EXT_clip_control,"
            "EXT_color_buffer_half_float,EXT_depth_clamp,EXT_float_blend,"
            "EXT_polygon_offset_clamp,EXT_texture_compression_bptc,"
            "EXT_texture_compression_rgtc,EXT_texture_filter_anisotropic,"
            "EXT_sRGB,OES_element_index_uint,OES_fbo_render_mipmap,"
            "OES_standard_derivatives,OES_texture_float,OES_texture_float_linear,"
            "OES_texture_half_float,OES_texture_half_float_linear,"
            "OES_vertex_array_object,WEBGL_color_buffer_float,"
            "WEBGL_compressed_texture_s3tc,WEBGL_compressed_texture_s3tc_srgb,"
            "WEBGL_debug_renderer_info,WEBGL_debug_shaders,"
            "WEBGL_depth_texture,WEBGL_lose_context,WEBGL_multi_draw"
        )
        ext2_desktop = (
            "EXT_clip_control,EXT_color_buffer_float,EXT_color_buffer_half_float,"
            "EXT_depth_clamp,EXT_float_blend,EXT_polygon_offset_clamp,"
            "EXT_texture_compression_bptc,EXT_texture_compression_rgtc,"
            "EXT_texture_filter_anisotropic,EXT_texture_norm16,"
            "OES_draw_buffers_indexed,OES_texture_float_linear,"
            "WEBGL_clip_cull_distance,WEBGL_compressed_texture_s3tc,"
            "WEBGL_compressed_texture_s3tc_srgb,WEBGL_debug_renderer_info,"
            "WEBGL_debug_shaders,WEBGL_lose_context,WEBGL_multi_draw"
        )

        def desktop_gpu(unmasked_vendor, unmasked_renderer, params_override=None):
            """Create a complete desktop GPU profile."""
            base = {
                "unmaskedVendor": unmasked_vendor,
                "unmaskedRenderer": unmasked_renderer,
                "vendor": "WebKit",
                "renderer": "WebKit WebGL",
                "shadingLanguage": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
                "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
                "shadingLanguage2": "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)",
                "version2": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
                "maxAnisotropy": "16",
                # WebGL1 limits
                "aliasedLineWidthRange": [1, 1],
                "aliasedPointSizeRange": [1, 1024],
                "alphaBits": "8", "blueBits": "8", "greenBits": "8", "redBits": "8",
                "depthBits": "24", "stencilBits": "8", "subpixelBits": "4",
                "sampleBuffers": "1", "samples": "4",
                "maxCombinedTextureImageUnits": "32",
                "maxCubeMapTextureSize": "16384",
                "maxFragmentUniformVectors": "1024",
                "maxRenderBufferSize": "16384",
                "maxTextureImageUnits": "16",
                "maxTextureSize": "16384",
                "maxVaryingVectors": "30",
                "maxVertexAttribs": "16",
                "maxVertexTextureImageUnits": "16",
                "maxVertexUniformVectors": "4096",
                "maxViewportDims": [32767, 32767],
                "stencilBackValueMask": "2147483647",
                "stencilBackWritemask": "2147483647",
                "stencilValueMask": "2147483647",
                "stencilWritemask": "2147483647",
                "extensions": ext_desktop,
                "contextAttributes": ctx_defaults,
                # WebGL2-specific limits
                "maxVertexUniformComponents2": "16384",
                "maxVertexUniformBlocks2": "16",
                "maxVertexOutputComponents2": "128",
                "maxVaryingComponents2": "124",
                "maxTransformFeedbackInterleavedComponents2": "128",
                "maxTransformFeedbackSeparateAttribs2": "4",
                "maxTransformFeedbackSeparateComponents2": "4",
                "maxFragmentUniformComponents2": "4096",
                "maxFragmentUniformBlocks2": "16",
                "maxFragmentInputComponents2": "128",
                "minProgramTexelOffset2": "-8",
                "maxProgramTexelOffset2": "7",
                "maxDrawBuffers2": "8",
                "maxColorAttachments2": "8",
                "maxSamples2": "4",
                "max3DTextureSize2": "2048",
                "maxArrayTextureLayers2": "2048",
                "maxClientWaitTimeoutWebgl2": "0",
                "maxElementIndex2": "4294967295",
                "maxServerWaitTimeout2": "0",
                "maxTextureLodBias2": "2",
                "maxUniformBufferBindings2": "72",
                "maxUniformBlockSize2": "65536",
                "uniformBufferOffsetAlignment2": "256",
                "maxCombinedUniformBlocks2": "48",
                "maxCombinedVertexUniformComponents2": "212992",
                "maxCombinedFragmentUniformComponents2": "200704",
                "maxElementsVertices2": "2147483647",
                "maxElementsIndices2": "2147483647",
                "aliasedLineWidthRange2": [1, 1],
                "aliasedPointSizeRange2": [1, 1024],
                "contextAttributes2": ctx_defaults,
                "alphaBits2": "8", "blueBits2": "8", "greenBits2": "8", "redBits2": "8",
                "depthBits2": "24", "stencilBits2": "8", "subpixelBits2": "4",
                "sampleBuffers2": "1", "samples2": "4",
                "maxCombinedTextureImageUnits2": "32",
                "maxCubeMapTextureSize2": "16384",
                "maxFragmentUniformVectors2": "1024",
                "maxRenderBufferSize2": "16384",
                "maxTextureImageUnits2": "16",
                "maxTextureSize2": "16384",
                "maxVaryingVectors2": "30",
                "maxVertexAttribs2": "16",
                "maxVertexTextureImageUnits2": "16",
                "maxVertexUniformVectors2": "4096",
                "maxViewportDims2": [32767, 32767],
                "stencilBackValueMask2": "2147483647",
                "stencilBackWritemask2": "2147483647",
                "stencilValueMask2": "2147483647",
                "stencilWritemask2": "2147483647",
                "extensions2": ext2_desktop,
                "precision": precision,
            }
            if params_override:
                base.update(params_override)
            return base

        return [
            desktop_gpu(
                "Google Inc. (Intel)",
                "ANGLE (Intel, Intel(R) UHD Graphics 630 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ),
            desktop_gpu(
                "Google Inc. (Intel)",
                "ANGLE (Intel, Intel(R) Iris(R) Xe Graphics Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ),
            desktop_gpu(
                "Google Inc. (Intel)",
                "ANGLE (Intel, Intel(R) HD Graphics 4000 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxTextureSize": "8192", "maxCubeMapTextureSize": "8192",
                 "maxRenderBufferSize": "8192", "maxTextureSize2": "8192",
                 "maxCubeMapTextureSize2": "8192", "maxRenderBufferSize2": "8192",
                 "maxViewportDims": [16384, 16384], "maxViewportDims2": [16384, 16384],
                 "aliasedPointSizeRange": [1, 511], "aliasedPointSizeRange2": [1, 511]},
            ),
            desktop_gpu(
                "Google Inc. (NVIDIA)",
                "ANGLE (NVIDIA, NVIDIA GeForce GTX 1060 6GB Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024",
                 "maxVertexUniformComponents2": "16384", "maxFragmentUniformComponents2": "4096"},
            ),
            desktop_gpu(
                "Google Inc. (NVIDIA)",
                "ANGLE (NVIDIA, NVIDIA GeForce GTX 1650 Direct3D11 vs_5_0 ps_5_0, D3D11)",
            ),
            desktop_gpu(
                "Google Inc. (NVIDIA)",
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024",
                 "aliasedPointSizeRange": [1, 1024], "aliasedPointSizeRange2": [1, 1024]},
            ),
            desktop_gpu(
                "Google Inc. (NVIDIA)",
                "ANGLE (NVIDIA, NVIDIA GeForce RTX 4060 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024"},
            ),
            desktop_gpu(
                "Google Inc. (AMD)",
                "ANGLE (AMD, AMD Radeon RX 580 Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024"},
            ),
            desktop_gpu(
                "Google Inc. (AMD)",
                "ANGLE (AMD, AMD Radeon RX 6600 XT Direct3D11 vs_5_0 ps_5_0, D3D11)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024"},
            ),
            desktop_gpu(
                "Google Inc. (Apple)",
                "ANGLE (Apple, Apple M1, OpenGL 4.1)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024",
                 "maxViewportDims": [16384, 16384], "maxViewportDims2": [16384, 16384],
                 "aliasedPointSizeRange": [1, 1023], "aliasedPointSizeRange2": [1, 1023]},
            ),
            desktop_gpu(
                "Google Inc. (Apple)",
                "ANGLE (Apple, Apple M2, OpenGL 4.1)",
                {"maxVertexUniformVectors": "4096", "maxFragmentUniformVectors": "1024",
                 "maxViewportDims": [16384, 16384], "maxViewportDims2": [16384, 16384],
                 "aliasedPointSizeRange": [1, 1023], "aliasedPointSizeRange2": [1, 1023]},
            ),
        ]

    def _build_gpu_database_mobile(self) -> list:
        """Build complete GPU database for mobile devices."""
        ctx_defaults = self._webgl_context_defaults()
        precision = self._shader_precision_mobile()

        # Mobile WebGL1 extensions (include mobile-specific: ETC, ASTC)
        ext_mobile = (
            "ANGLE_instanced_arrays,EXT_blend_minmax,EXT_clip_control,"
            "EXT_color_buffer_half_float,EXT_depth_clamp,EXT_float_blend,"
            "EXT_polygon_offset_clamp,EXT_texture_compression_bptc,"
            "EXT_texture_compression_rgtc,EXT_texture_filter_anisotropic,"
            "EXT_sRGB,OES_element_index_uint,OES_fbo_render_mipmap,"
            "OES_standard_derivatives,OES_texture_float,OES_texture_float_linear,"
            "OES_texture_half_float,OES_texture_half_float_linear,"
            "OES_vertex_array_object,WEBGL_color_buffer_float,"
            "WEBGL_compressed_texture_astc,WEBGL_compressed_texture_etc,"
            "WEBGL_compressed_texture_etc1,WEBGL_compressed_texture_s3tc,"
            "WEBGL_compressed_texture_s3tc_srgb,WEBGL_debug_renderer_info,"
            "WEBGL_debug_shaders,WEBGL_depth_texture,WEBGL_lose_context,"
            "WEBGL_multi_draw"
        )
        ext2_mobile = (
            "EXT_clip_control,EXT_color_buffer_float,EXT_color_buffer_half_float,"
            "EXT_depth_clamp,EXT_float_blend,EXT_polygon_offset_clamp,"
            "EXT_texture_compression_bptc,EXT_texture_compression_rgtc,"
            "EXT_texture_filter_anisotropic,EXT_texture_norm16,"
            "NV_shader_noperspective_interpolation,OES_draw_buffers_indexed,"
            "OES_texture_float_linear,WEBGL_clip_cull_distance,"
            "WEBGL_compressed_texture_astc,WEBGL_compressed_texture_etc,"
            "WEBGL_compressed_texture_etc1,WEBGL_compressed_texture_s3tc,"
            "WEBGL_compressed_texture_s3tc_srgb,WEBGL_debug_renderer_info,"
            "WEBGL_debug_shaders,WEBGL_lose_context,WEBGL_multi_draw"
        )

        def mobile_gpu(unmasked_vendor, unmasked_renderer, params_override=None):
            """Create a complete mobile GPU profile."""
            base = {
                "unmaskedVendor": unmasked_vendor,
                "unmaskedRenderer": unmasked_renderer,
                "vendor": "WebKit",
                "renderer": "WebKit WebGL",
                "shadingLanguage": "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)",
                "version": "WebGL 1.0 (OpenGL ES 2.0 Chromium)",
                "shadingLanguage2": "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)",
                "version2": "WebGL 2.0 (OpenGL ES 3.0 Chromium)",
                "maxAnisotropy": "16",
                # WebGL1 limits — mobile defaults
                "aliasedLineWidthRange": [1, 8],
                "aliasedPointSizeRange": [1, 1023],
                "alphaBits": "8", "blueBits": "8", "greenBits": "8", "redBits": "8",
                "depthBits": "24", "stencilBits": "8", "subpixelBits": "4",
                "sampleBuffers": "1", "samples": "4",
                "maxCombinedTextureImageUnits": "48",
                "maxCubeMapTextureSize": "4096",
                "maxFragmentUniformVectors": "256",
                "maxRenderBufferSize": "16384",
                "maxTextureImageUnits": "16",
                "maxTextureSize": "4096",
                "maxVaryingVectors": "31",
                "maxVertexAttribs": "16",
                "maxVertexTextureImageUnits": "16",
                "maxVertexUniformVectors": "256",
                "maxViewportDims": [16384, 16384],
                "stencilBackValueMask": "2147483647",
                "stencilBackWritemask": "2147483647",
                "stencilValueMask": "2147483647",
                "stencilWritemask": "2147483647",
                "extensions": ext_mobile,
                "contextAttributes": ctx_defaults,
                # WebGL2-specific limits — mobile
                "maxVertexUniformComponents2": "1024",
                "maxVertexUniformBlocks2": "14",
                "maxVertexOutputComponents2": "128",
                "maxVaryingComponents2": "124",
                "maxTransformFeedbackInterleavedComponents2": "128",
                "maxTransformFeedbackSeparateAttribs2": "4",
                "maxTransformFeedbackSeparateComponents2": "4",
                "maxFragmentUniformComponents2": "1024",
                "maxFragmentUniformBlocks2": "14",
                "maxFragmentInputComponents2": "128",
                "minProgramTexelOffset2": "-8",
                "maxProgramTexelOffset2": "7",
                "maxDrawBuffers2": "8",
                "maxColorAttachments2": "8",
                "maxSamples2": "4",
                "max3DTextureSize2": "2048",
                "maxArrayTextureLayers2": "2048",
                "maxClientWaitTimeoutWebgl2": "0",
                "maxElementIndex2": "2147483647",
                "maxServerWaitTimeout2": "4294967295000000",
                "maxTextureLodBias2": "15.99609375",
                "maxUniformBufferBindings2": "72",
                "maxUniformBlockSize2": "65536",
                "uniformBufferOffsetAlignment2": "32",
                "maxCombinedUniformBlocks2": "42",
                "maxCombinedVertexUniformComponents2": "230400",
                "maxCombinedFragmentUniformComponents2": "230400",
                "maxElementsVertices2": "2147483647",
                "maxElementsIndices2": "2147483647",
                "aliasedLineWidthRange2": [1, 8],
                "aliasedPointSizeRange2": [1, 1023],
                "contextAttributes2": ctx_defaults,
                "alphaBits2": "8", "blueBits2": "8", "greenBits2": "8", "redBits2": "8",
                "depthBits2": "24", "stencilBits2": "8", "subpixelBits2": "4",
                "sampleBuffers2": "1", "samples2": "4",
                "maxCombinedTextureImageUnits2": "48",
                "maxCubeMapTextureSize2": "4096",
                "maxFragmentUniformVectors2": "256",
                "maxRenderBufferSize2": "16384",
                "maxTextureImageUnits2": "16",
                "maxTextureSize2": "4096",
                "maxVaryingVectors2": "31",
                "maxVertexAttribs2": "16",
                "maxVertexTextureImageUnits2": "16",
                "maxVertexUniformVectors2": "256",
                "maxViewportDims2": [16384, 16384],
                "stencilBackValueMask2": "2147483647",
                "stencilBackWritemask2": "2147483647",
                "stencilValueMask2": "2147483647",
                "stencilWritemask2": "2147483647",
                "extensions2": ext2_mobile,
                "precision": precision,
            }
            if params_override:
                base.update(params_override)
            return base

        return [
            mobile_gpu(
                "Google Inc. (Qualcomm)",
                "Adreno (TM) 730",
            ),
            mobile_gpu(
                "Google Inc. (Qualcomm)",
                "Adreno (TM) 740",
            ),
            mobile_gpu(
                "Google Inc. (Qualcomm)",
                "Adreno (TM) 660",
                {"maxRenderBufferSize": "8192", "maxRenderBufferSize2": "8192"},
            ),
            mobile_gpu(
                "Google Inc. (ARM)",
                "Mali-G710 MC10",
                {"maxCombinedTextureImageUnits": "64", "maxCombinedTextureImageUnits2": "64"},
            ),
            mobile_gpu(
                "Google Inc. (ARM)",
                "Mali-G78 MC20",
                {"maxCombinedTextureImageUnits": "64", "maxCombinedTextureImageUnits2": "64"},
            ),
            mobile_gpu(
                "Google Inc. (Imagination Technologies)",
                "PowerVR Rogue GE8320",
            ),
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
                "webgpu_fingerprint": self._generate_webgpu_fingerprint(is_mobile=is_mobile),
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

            # Sensor data (mobile only)
            if is_mobile:
                profile["sensor"] = self._generate_sensor_data()

            # CSS media queries
            profile["css_media"] = self._generate_css_media(is_mobile=is_mobile)

            # Speech synthesis, feature flags, audio properties
            profile["speech_voices"] = self._generate_speech_voices(is_mobile=is_mobile)
            profile["feature_flags"] = self._generate_feature_flags(is_mobile=is_mobile)
            profile["audio_properties"] = self._generate_audio_properties(is_mobile=is_mobile)

            # Network/hardware fingerprints
            profile["connection_info"] = self._generate_connection_info(is_mobile=is_mobile)
            profile["storage_quota"] = self._generate_storage_quota(is_mobile=is_mobile)
            profile["heap_size"] = self._generate_heap_size()
            profile["system_colors"] = self._generate_system_colors()
            profile["system_fonts"] = self._generate_system_fonts()
            profile["codecs"] = self._generate_codecs()
            profile["keyboard_layout"] = self._generate_keyboard_layout()

            # Generate profile hash for identification
            profile["profile_hash"] = self._generate_profile_hash(profile)

            return profile

        except Exception as e:
            logger.error(f"Error generating profile: {e}")
            raise

    # Desktop YaBrowser UA templates (Windows only)
    MODERN_CHROME_UAS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cver} YaBrowser/{yver} Yowser/2.5 Safari/537.36",
    ]

    # Mobile YaBrowser UA templates (Android)
    MOBILE_CHROME_UAS = [
        "Mozilla/5.0 (Linux; Android {android}; {model}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{cver} YaBrowser/{yver} Mobile Safari/537.36",
    ]

    # YaBrowser version pairs: (Chrome base version, YaBrowser version)
    # YaBrowser 25.x = Chromium 132-134, YaBrowser 26.x = Chromium 134-136
    # download URL showed 26.3.0 in April 2026
    YABROWSER_VERSIONS = [
        ("132.0.6834.{patch}", "25.2.3.{ypatch}"),
        ("132.0.6834.{patch}", "25.2.5.{ypatch}"),
        ("134.0.6998.{patch}", "25.4.2.{ypatch}"),
        ("134.0.6998.{patch}", "25.4.5.{ypatch}"),
        ("134.0.6998.{patch}", "26.1.2.{ypatch}"),
        ("136.0.7103.{patch}", "26.3.0.{ypatch}"),
        ("136.0.7103.{patch}", "26.3.2.{ypatch}"),
    ]

    def _generate_user_agent(self, is_mobile: bool = False, device_info: dict = None) -> str:
        """Generate realistic YaBrowser user agent."""
        chrome_ver_tpl, ya_ver_tpl = random.choice(self.YABROWSER_VERSIONS)
        patch = random.randint(40, 120)
        ypatch = random.randint(100, 999)
        chrome_version = chrome_ver_tpl.format(patch=patch)
        ya_version = ya_ver_tpl.format(ypatch=ypatch)

        if is_mobile and device_info:
            template = random.choice(self.MOBILE_CHROME_UAS)
            return template.format(
                cver=chrome_version,
                yver=ya_version,
                android=device_info['android'],
                model=device_info['model']
            )
        else:
            template = random.choice(self.MODERN_CHROME_UAS)
            return template.format(cver=chrome_version, yver=ya_version)

    def _generate_platform(self, is_mobile: bool = False) -> str:
        """Generate platform string."""
        if is_mobile:
            return "Linux armv81"
        return "Win32"

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
        """Generate WebGL fingerprint data from GPU-consistent database."""
        if is_mobile:
            gpu = random.choice(self._gpu_profiles_mobile)
        else:
            gpu = random.choice(self._gpu_profiles_desktop)

        # Return complete GPU profile (used by browser_manager for JS injection)
        return gpu

    def _generate_webgpu_fingerprint(self, is_mobile: bool = False) -> Dict:
        """Generate WebGPU fingerprint data consistent with selected WebGL GPU."""
        # WebGPU adapter limits (highPerformance — what requestAdapter returns)
        _adapter_limits = {
            "maxTextureDimension1D": "16384", "maxTextureDimension2D": "16384",
            "maxTextureDimension3D": "2048", "maxTextureArrayLayers": "256",
            "maxBindGroups": "4", "maxBindGroupsPlusVertexBuffers": "24",
            "maxBindingsPerBindGroup": "1000",
            "maxDynamicUniformBuffersPerPipelineLayout": "10",
            "maxDynamicStorageBuffersPerPipelineLayout": "8",
            "maxSampledTexturesPerShaderStage": "16",
            "maxSamplersPerShaderStage": "16",
            "maxStorageBuffersPerShaderStage": "10",
            "maxStorageTexturesPerShaderStage": "8",
            "maxUniformBuffersPerShaderStage": "12",
            "maxUniformBufferBindingSize": "65536",
            "maxStorageBufferBindingSize": "134217728",
            "minUniformBufferOffsetAlignment": "256",
            "minStorageBufferOffsetAlignment": "256",
            "maxVertexBuffers": "8", "maxBufferSize": "2147483648",
            "maxVertexAttributes": "30", "maxVertexBufferArrayStride": "2048",
            "maxInterStageShaderComponents": "60",
            "maxInterStageShaderVariables": "16",
            "maxColorAttachments": "8",
            "maxColorAttachmentBytesPerSample": "32",
            "maxComputeWorkgroupStorageSize": "32768",
            "maxComputeInvocationsPerWorkgroup": "1024",
            "maxComputeWorkgroupSizeX": "1024",
            "maxComputeWorkgroupSizeY": "1024",
            "maxComputeWorkgroupSizeZ": "64",
            "maxComputeWorkgroupsPerDimension": "65535",
        }
        # GPUDevice limits (lower defaults, what you get after requestDevice)
        _device_limits = {
            "maxTextureDimension1D": "8192", "maxTextureDimension2D": "8192",
            "maxTextureDimension3D": "2048", "maxTextureArrayLayers": "256",
            "maxBindGroups": "4", "maxBindGroupsPlusVertexBuffers": "24",
            "maxBindingsPerBindGroup": "1000",
            "maxDynamicUniformBuffersPerPipelineLayout": "8",
            "maxDynamicStorageBuffersPerPipelineLayout": "4",
            "maxSampledTexturesPerShaderStage": "16",
            "maxSamplersPerShaderStage": "16",
            "maxStorageBuffersPerShaderStage": "8",
            "maxStorageTexturesPerShaderStage": "4",
            "maxUniformBuffersPerShaderStage": "12",
            "maxUniformBufferBindingSize": "65536",
            "maxStorageBufferBindingSize": "134217728",
            "minUniformBufferOffsetAlignment": "256",
            "minStorageBufferOffsetAlignment": "256",
            "maxVertexBuffers": "8", "maxBufferSize": "268435456",
            "maxVertexAttributes": "16", "maxVertexBufferArrayStride": "2048",
            "maxInterStageShaderComponents": "60",
            "maxInterStageShaderVariables": "16",
            "maxColorAttachments": "8",
            "maxColorAttachmentBytesPerSample": "32",
            "maxComputeWorkgroupStorageSize": "16384",
            "maxComputeInvocationsPerWorkgroup": "256",
            "maxComputeWorkgroupSizeX": "256",
            "maxComputeWorkgroupSizeY": "256",
            "maxComputeWorkgroupSizeZ": "64",
            "maxComputeWorkgroupsPerDimension": "65535",
        }

        # GPU vendor/arch maps linked to WebGL GPU vendors
        _desktop_gpu_info = [
            {"vendor": "google", "architecture": "gen-12",
             "features": ["texture-compression-bc", "depth-clip-control", "depth32float-stencil8",
                          "timestamp-query", "indirect-first-instance", "rg11b10ufloat-renderable"]},
            {"vendor": "nvidia", "architecture": "turing",
             "features": ["texture-compression-bc", "depth-clip-control", "depth32float-stencil8",
                          "timestamp-query", "indirect-first-instance", "rg11b10ufloat-renderable",
                          "shader-f16"]},
            {"vendor": "amd", "architecture": "rdna-2",
             "features": ["texture-compression-bc", "depth-clip-control", "depth32float-stencil8",
                          "timestamp-query", "indirect-first-instance", "rg11b10ufloat-renderable"]},
            {"vendor": "apple", "architecture": "common-3",
             "features": ["texture-compression-bc", "depth-clip-control", "depth32float-stencil8",
                          "timestamp-query", "indirect-first-instance", "rg11b10ufloat-renderable",
                          "shader-f16"]},
        ]
        _mobile_gpu_info = [
            {"vendor": "qualcomm", "architecture": "adreno-7xx",
             "features": ["indirect-first-instance", "texture-compression-etc2",
                          "texture-compression-astc", "depth-clip-control",
                          "depth32float-stencil8", "timestamp-query",
                          "texture-compression-bc", "rg11b10ufloat-renderable"]},
            {"vendor": "arm", "architecture": "valhall",
             "features": ["indirect-first-instance", "texture-compression-etc2",
                          "texture-compression-astc", "depth-clip-control",
                          "depth32float-stencil8", "rg11b10ufloat-renderable"]},
            {"vendor": "imagination-technologies", "architecture": "powervr-rogue",
             "features": ["texture-compression-etc2", "texture-compression-astc",
                          "depth32float-stencil8", "rg11b10ufloat-renderable"]},
        ]

        if is_mobile:
            gpu_info = random.choice(_mobile_gpu_info)
            canvas_format = "rgba8unorm"
        else:
            gpu_info = random.choice(_desktop_gpu_info)
            canvas_format = "bgra8unorm"

        adapter_data = {
            "isFallbackAdapter": False,
            "features": gpu_info["features"],
            "info": {
                "vendor": gpu_info["vendor"],
                "architecture": gpu_info["architecture"],
                "device": "",
                "description": "",
            },
            "limits": _adapter_limits,
            "limits_gpudevice": _device_limits,
        }

        return {
            "isEnabled": True,
            "highPerformance": adapter_data,
            "lowPerformance": adapter_data,
            "fallback": None,
            "preferredCanvasFormat": canvas_format,
        }

    def _generate_sensor_data(self) -> Dict:
        """Generate sensor data for mobile profiles (stationary device)."""
        # Small orientation variations per profile (device lying on table)
        qz = round(random.uniform(-0.8, 0.8), 6)
        return {
            "gyroscope": {"x": 0, "y": 0, "z": 0},
            "gravity": {"x": 0, "y": 0, "z": 9.8},
            "accelerometer": {"x": 0, "y": 0, "z": 9.8},
            "linearAcceleration": {"x": 0, "y": 0, "z": 0},
            "orientationQuaternionZ": qz,
        }

    def _generate_css_media(self, is_mobile: bool = False) -> Dict:
        """Generate CSS media query values matching device type."""
        if is_mobile:
            return {
                "anyHover": "none", "anyPointer": "coarse",
                "hover": "none", "pointer": "coarse",
                "prefersColorScheme": random.choice(["light", "dark"]),
                "prefersReducedMotion": "no-preference",
                "prefersContrast": "no-preference",
                "forcedColors": "none",
                "invertedColors": "none",
                "dynamicRange": "high",
                "videoDynamicRange": "high",
                "colorGamut": "srgb",
                "color": 8,
                "colorIndex": 0,
                "grid": "0",
                "monochrome": 0,
                "orientation": "portrait",
                "overflowBlock": "scroll",
                "prefersReducedTransparency": "no-preference",
                "resolution": random.choice([320, 420, 480]),
                "update": "fast",
            }
        return {
            "anyHover": "hover", "anyPointer": "fine",
            "hover": "hover", "pointer": "fine",
            "prefersColorScheme": random.choice(["light", "dark"]),
            "prefersReducedMotion": random.choice(["no-preference", "reduce"]),
            "prefersContrast": "no-preference",
            "forcedColors": "none",
            "invertedColors": "none",
            "dynamicRange": "high",
            "videoDynamicRange": "high",
            "colorGamut": "srgb",
            "color": 8,
            "colorIndex": 0,
            "grid": "0",
            "monochrome": 0,
            "orientation": "landscape",
            "overflowBlock": "scroll",
            "prefersReducedTransparency": random.choice(["no-preference", "reduce"]),
            "resolution": 96,
            "update": "fast",
        }

    def _generate_audio_fingerprint(self) -> str:
        """Generate audio context fingerprint."""
        # Simulate audio context variations
        sample_rate = random.choice([44100, 48000])
        base_frequency = random.choice([440, 523.251, 659.255])  # A4, C5, E5 notes

        # Create unique audio fingerprint
        audio_data = f"AudioContext_{sample_rate}_{base_frequency}_{random.random()}"
        return hashlib.md5(audio_data.encode()).hexdigest()

    # ---- Android TTS voices (Russian locale, typical Samsung/Pixel) ----
    _ANDROID_VOICES = [
        {"name": "немецкий Германия", "lang": "de_DE", "default": True},
        {"name": "английский Великобритания", "lang": "en_GB", "default": False},
        {"name": "английский Соединенные Штаты", "lang": "en_US", "default": False},
        {"name": "испанский Испания", "lang": "es_ES", "default": False},
        {"name": "испанский Мексика", "lang": "es_MX", "default": False},
        {"name": "французский Франция", "lang": "fr_FR", "default": False},
        {"name": "итальянский Италия", "lang": "it_IT", "default": False},
        {"name": "португальский Бразилия", "lang": "pt_BR", "default": False},
        {"name": "русский Россия", "lang": "ru_RU", "default": False},
    ]

    # ---- Desktop Chrome voices (varies by OS) ----
    _DESKTOP_VOICES = [
        {"name": "Microsoft Irina - Russian (Russia)", "lang": "ru-RU", "default": True},
        {"name": "Microsoft Pavel - Russian (Russia)", "lang": "ru-RU", "default": False},
        {"name": "Google US English", "lang": "en-US", "default": False},
        {"name": "Google UK English Female", "lang": "en-GB", "default": False},
        {"name": "Google Deutsch", "lang": "de-DE", "default": False},
        {"name": "Google français", "lang": "fr-FR", "default": False},
        {"name": "Google español", "lang": "es-ES", "default": False},
        {"name": "Google italiano", "lang": "it-IT", "default": False},
        {"name": "Google русский", "lang": "ru-RU", "default": False},
    ]

    def _generate_speech_voices(self, is_mobile: bool = False) -> list:
        """Generate speechSynthesis voice list for the platform."""
        voices = self._ANDROID_VOICES if is_mobile else self._DESKTOP_VOICES
        result = []
        for v in voices:
            result.append({
                "name": v["name"],
                "lang": v["lang"],
                "localService": True,
                "voiceURI": v["name"],
                "default": v["default"],
            })
        return result

    # ---- Mobile vs Desktop feature detection ----
    _MOBILE_FEATURES = {
        "SharedWorker": False, "OrientationEvent": True,
        "WebHID": False, "Serial": False,
        "ContactsManager": True, "BarcodeDetector": True,
        "WebNFC": True, "PictureInPictureAPI": True,
        "EyeDropperAPI": False, "GetDisplayMedia": False,
        "FileSystemAccess": True, "ContentIndex": True,
        "FontAccess": False, "AudioOutputDevices": False,
        "OnDeviceChange": False, "DisplayCutoutAPI": False,
    }
    _DESKTOP_FEATURES = {
        "SharedWorker": True, "OrientationEvent": False,
        "WebHID": True, "Serial": True,
        "ContactsManager": False, "BarcodeDetector": True,
        "WebNFC": False, "PictureInPictureAPI": True,
        "EyeDropperAPI": True, "GetDisplayMedia": True,
        "FileSystemAccess": True, "ContentIndex": False,
        "FontAccess": True, "AudioOutputDevices": True,
        "OnDeviceChange": True, "DisplayCutoutAPI": False,
    }

    def _generate_feature_flags(self, is_mobile: bool = False) -> Dict:
        """Generate feature detection flags matching device type."""
        base = dict(self._MOBILE_FEATURES if is_mobile else self._DESKTOP_FEATURES)
        # Extended flags matching competitor profiles
        if is_mobile:
            base.update({
                "NavigatorContentUtils": False,
                "CaptureHandle": False,
                "RegionCapture": False,
                "CaptureController": False,
                "ConditionalFocus": False,
                "DocumentPictureInPictureAPI": False,
                "SmartCard": False,
                "WebAppLaunchQueue": False,
                "WebAppLaunchHandler": False,
                "WebAppWindowControlsOverlay": False,
                "AttributionReporting": False,
                "FencedFrames": False,
                "FencedFramesAPIChanges": False,
                "Fledge": False,
                "PrivacySandboxAdsAPIs": False,
                "SharedStorageAPI": False,
                "TopicsAPI": False,
                "TopicsDocumentAPI": False,
                "TopicsXHR": False,
                "CapturedSurfaceControl": False,
                "ElementCapture": False,
                "WebPrinting": False,
                "PrivateNetworkAccessPermissionPrompt": False,
                "DigitalGoodsV2_1": False,
                "DigitalGoods": False,
                "SerialPortForget": False,
                "SerialPortConnected": False,
                "FileSystemAccessLocal": True,
                "FileSystemAccessOriginPrivate": True,
            })
        else:
            base.update({
                "NavigatorContentUtils": True,
                "CaptureHandle": True,
                "RegionCapture": True,
                "CaptureController": True,
                "ConditionalFocus": True,
                "DocumentPictureInPictureAPI": True,
                "SmartCard": False,
                "WebAppLaunchQueue": True,
                "WebAppLaunchHandler": True,
                "WebAppWindowControlsOverlay": True,
                "AttributionReporting": False,
                "FencedFrames": False,
                "FencedFramesAPIChanges": False,
                "Fledge": False,
                "PrivacySandboxAdsAPIs": False,
                "SharedStorageAPI": False,
                "TopicsAPI": False,
                "TopicsDocumentAPI": False,
                "TopicsXHR": False,
                "CapturedSurfaceControl": False,
                "ElementCapture": False,
                "WebPrinting": False,
                "PrivateNetworkAccessPermissionPrompt": False,
                "DigitalGoodsV2_1": False,
                "DigitalGoods": False,
                "SerialPortForget": True,
                "SerialPortConnected": False,
                "FileSystemAccessLocal": True,
                "FileSystemAccessOriginPrivate": True,
            })
        return base

    def _generate_audio_properties(self, is_mobile: bool = False) -> Dict:
        """Generate comprehensive audio context properties (all AudioNode defaults)."""
        sr = 48000 if is_mobile else random.choice([44100, 48000])
        max_freq = sr / 2  # Nyquist frequency
        flt = 3.4028234663852886e+38
        return {
            "BaseAudioContextSampleRate": sr,
            "AudioContextBaseLatency": round(random.uniform(0.005, 0.02), 4) if not is_mobile else round(random.uniform(0.002, 0.01), 4),
            "AudioContextOutputLatency": 0,
            "AudioDestinationNodeMaxChannelCount": 2,
            "AnalyzerNodeFftSize": 2048,
            "AnalyzerNodeFrequencyBinCount": 1024,
            "AnalyzerNodeMinDecibels": -100,
            "AnalyzerNodeMaxDecibels": -30,
            "AnalyzerNodeSmoothingTimeConstant": 0.8,
            "BiquadFilterNodeFrequencyDefaultValue": 350,
            "BiquadFilterNodeFrequencyMaxValue": max_freq,
            "BiquadFilterNodeFrequencyMinValue": 0,
            "BiquadFilterNodeDetuneDefaultValue": 0,
            "BiquadFilterNodeDetuneMaxValue": 153600,
            "BiquadFilterNodeDetuneMinValue": -153600,
            "BiquadFilterNodeQDefaultValue": 1,
            "BiquadFilterNodeQMaxValue": flt,
            "BiquadFilterNodeQMinValue": -flt,
            "BiquadFilterNodeGainDefaultValue": 0,
            "BiquadFilterNodeGainMaxValue": 1541.273681640625,
            "BiquadFilterNodeGainMinValue": -flt,
            "BiquadFilterNodeType": "lowpass",
            "AudioBufferSourceNodeDetuneDefaultValue": 0,
            "AudioBufferSourceNodeDetuneMaxValue": flt,
            "AudioBufferSourceNodeDetuneMinValue": -flt,
            "AudioBufferSourceNodePlaybackRateDefaultValue": 1,
            "AudioBufferSourceNodePlaybackRateMaxValue": flt,
            "AudioBufferSourceNodePlaybackRateMinValue": -flt,
            "ConstantSourceNodeOffsetDefaultValue": 1,
            "ConstantSourceNodeOffsetMaxValue": flt,
            "ConstantSourceNodeOffsetMinValue": -flt,
            "DelayNodeDelayTimeDefaultValue": 0,
            "DelayNodeDelayTimeMaxValue": 1,
            "DelayNodeDelayTimeMinValue": 0,
            "DynamicsCompressorNodeThresholdDefaultValue": -24,
            "DynamicsCompressorNodeThresholdMaxValue": 0,
            "DynamicsCompressorNodeThresholdMinValue": -100,
            "DynamicsCompressorNodeKneeDefaultValue": 30,
            "DynamicsCompressorNodeKneeMaxValue": 40,
            "DynamicsCompressorNodeKneeMinValue": 0,
            "DynamicsCompressorNodeRatioDefaultValue": 12,
            "DynamicsCompressorNodeRatioMaxValue": 20,
            "DynamicsCompressorNodeRatioMinValue": 1,
            "DynamicsCompressorNodeReduction": 0,
            "DynamicsCompressorNodeAttackDefaultValue": 0.003000000026077032,
            "DynamicsCompressorNodeAttackMaxValue": 1,
            "DynamicsCompressorNodeAttackMinValue": 0,
            "DynamicsCompressorNodeReleaseDefaultValue": 0.25,
            "DynamicsCompressorNodeReleaseMaxValue": 1,
            "DynamicsCompressorNodeReleaseMinValue": 0,
            "GainNodeGainDefaultValue": 1,
            "GainNodeGainMaxValue": flt,
            "GainNodeGainMinValue": -flt,
            "OscillatorNodeFrequencyDefaultValue": 440,
            "OscillatorNodeFrequencyMaxValue": max_freq,
            "OscillatorNodeFrequencyMinValue": -max_freq,
            "OscillatorNodeDetuneDefaultValue": 0,
            "OscillatorNodeDetuneMaxValue": 153600,
            "OscillatorNodeDetuneMinValue": -153600,
            "OscillatorNodeType": "sine",
            "StereoPannerNodePanDefaultValue": 0,
            "StereoPannerNodePanMaxValue": 1,
            "StereoPannerNodePanMinValue": -1,
            "AudioListenerPositionXDefaultValue": 0,
            "AudioListenerPositionXMaxValue": flt,
            "AudioListenerPositionXMinValue": -flt,
            "AudioListenerPositionYDefaultValue": 0,
            "AudioListenerPositionYMaxValue": flt,
            "AudioListenerPositionYMinValue": -flt,
            "AudioListenerPositionZDefaultValue": 0,
            "AudioListenerPositionZMaxValue": flt,
            "AudioListenerPositionZMinValue": -flt,
            "AudioListenerForwardXDefaultValue": 0,
            "AudioListenerForwardXMaxValue": flt,
            "AudioListenerForwardXMinValue": -flt,
            "AudioListenerForwardYDefaultValue": 0,
            "AudioListenerForwardYMaxValue": flt,
            "AudioListenerForwardYMinValue": -flt,
            "AudioListenerForwardZDefaultValue": -1,
            "AudioListenerForwardZMaxValue": flt,
            "AudioListenerForwardZMinValue": -flt,
            "AudioListenerUpXDefaultValue": 0,
            "AudioListenerUpXMaxValue": flt,
            "AudioListenerUpXMinValue": -flt,
            "AudioListenerUpYDefaultValue": 1,
            "AudioListenerUpYMaxValue": flt,
            "AudioListenerUpYMinValue": -flt,
            "AudioListenerUpZDefaultValue": 0,
            "AudioListenerUpZMaxValue": flt,
            "AudioListenerUpZMinValue": -flt,
            "PannerNodePositionXDefaultValue": 0,
            "PannerNodePositionXMaxValue": flt,
            "PannerNodePositionXMinValue": -flt,
            "PannerNodePositionYDefaultValue": 0,
            "PannerNodePositionYMaxValue": flt,
            "PannerNodePositionYMinValue": -flt,
            "PannerNodePositionZDefaultValue": 0,
            "PannerNodePositionZMaxValue": flt,
            "PannerNodePositionZMinValue": -flt,
            "PannerNodeOrientationXDefaultValue": 1,
            "PannerNodeOrientationXMaxValue": flt,
            "PannerNodeOrientationXMinValue": -flt,
            "PannerNodeOrientationYDefaultValue": 0,
            "PannerNodeOrientationYMaxValue": flt,
            "PannerNodeOrientationYMinValue": -flt,
            "PannerNodeOrientationZDefaultValue": 0,
            "PannerNodeOrientationZMaxValue": flt,
            "PannerNodeOrientationZMinValue": -flt,
        }

    def _generate_font_list(self) -> List[str]:
        """Generate realistic font list matching competitor profiles (80 fonts)."""
        # Windows-like font set that most fingerprinting scripts detect
        _COMMON_FONTS = [
            "Arial", "Arial Black", "Baskerville", "Book Antiqua",
            "Bookman Old Style", "Calibri", "Cambria", "Candara",
            "Cardo", "Casual", "Century Gothic", "Century Schoolbook",
            "Comic Sans MS", "Consolas", "Constantia", "Corbel",
            "Courier", "Courier New", "DejaVu Sans", "DejaVu Serif",
            "Droid Sans", "Droid Sans Mono", "Garamond", "Georgia",
            "Gill Sans", "Helvetica", "Helvetica Neue", "Impact",
            "Lucida Console", "Lucida Grande", "Lucida Sans",
            "Lucida Sans Unicode", "Microsoft Sans Serif", "Monaco",
            "MS Gothic", "MS Mincho", "MS PGothic", "MS PMincho",
            "MS Sans Serif", "MS Serif", "Noto Sans", "Optima",
            "Palatino", "Palatino Linotype", "Segoe Print",
            "Segoe Script", "Segoe UI", "Segoe UI Symbol",
            "Sylfaen", "Symbol", "Tahoma", "Times",
            "Times New Roman", "Trebuchet MS", "Ubuntu",
            "Verdana", "Webdings", "Wingdings",
        ]
        # Pick 50-80 fonts with some randomness
        count = random.randint(50, min(80, len(_COMMON_FONTS)))
        selected = random.sample(_COMMON_FONTS, count)
        return sorted(selected)

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
            "--disable-features=TranslateUI",
        ]
        return flags

    def _generate_connection_info(self, is_mobile: bool = False) -> Dict:
        """Generate navigator.connection data."""
        if is_mobile:
            return {
                "effectiveType": random.choice(["4g", "4g", "4g", "3g"]),
                "rtt": random.choice([50, 100, 150, 200, 250]),
                "downlink": str(round(random.uniform(1.5, 10.0), 1)),
                "saveData": False,
            }
        return {
            "effectiveType": "4g",
            "rtt": random.choice([50, 100, 150]),
            "downlink": str(round(random.uniform(4.0, 10.0), 1)),
            "saveData": False,
        }

    def _generate_storage_quota(self, is_mobile: bool = False) -> int:
        """Generate navigator.storage.estimate() quota value."""
        if is_mobile:
            return random.choice([
                107374182400,  # ~100GB
                214748364800,  # ~200GB
                322122547200,  # ~300GB
            ])
        return random.choice([
            322122547200,  # ~300GB
            429496729600,  # ~400GB
            599720927232,  # ~559GB (matches competitor)
            644245094400,  # ~600GB
        ])

    def _generate_heap_size(self) -> int:
        """Generate performance.memory jsHeapSizeLimit."""
        return random.choice([
            2172649472,   # ~2GB
            4294705152,   # ~4GB (most common, matches competitor)
            4294705152,
            4294705152,
        ])

    def _generate_system_colors(self) -> Dict:
        """Generate getComputedStyle system colors (Windows-style)."""
        return {
            "ActiveBorder": "rgb(180, 180, 180)",
            "ActiveCaption": "rgb(153, 180, 209)",
            "ActiveText": "rgb(0, 102, 204)",
            "AppWorkspace": "rgb(171, 171, 171)",
            "Background": "rgb(0, 0, 0)",
            "ButtonBorder": "rgb(240, 240, 240)",
            "ButtonFace": "rgb(240, 240, 240)",
            "ButtonHighlight": "rgb(255, 255, 255)",
            "ButtonShadow": "rgb(160, 160, 160)",
            "ButtonText": "rgb(0, 0, 0)",
            "Canvas": "rgb(255, 255, 255)",
            "CanvasText": "rgb(0, 0, 0)",
            "CaptionText": "rgb(0, 0, 0)",
            "Field": "rgb(255, 255, 255)",
            "FieldText": "rgb(0, 0, 0)",
            "GrayText": "rgb(109, 109, 109)",
            "Highlight": "rgb(0, 120, 215)",
            "HighlightText": "rgb(255, 255, 255)",
            "InactiveBorder": "rgb(244, 247, 252)",
            "InactiveCaption": "rgb(191, 205, 219)",
            "InactiveCaptionText": "rgb(0, 0, 0)",
            "InfoBackground": "rgb(255, 255, 225)",
            "InfoText": "rgb(0, 0, 0)",
            "LinkText": "rgb(0, 102, 204)",
            "Mark": "rgb(255, 255, 0)",
            "MarkText": "rgb(0, 0, 0)",
            "Menu": "rgb(240, 240, 240)",
            "MenuText": "rgb(0, 0, 0)",
            "Scrollbar": "rgb(200, 200, 200)",
            "SelectedItem": "rgb(0, 120, 215)",
            "SelectedItemText": "rgb(255, 255, 255)",
            "ThreeDDarkShadow": "rgb(105, 105, 105)",
            "ThreeDFace": "rgb(240, 240, 240)",
            "ThreeDHighlight": "rgb(255, 255, 255)",
            "ThreeDLightShadow": "rgb(227, 227, 227)",
            "ThreeDShadow": "rgb(160, 160, 160)",
            "VisitedText": "rgb(0, 102, 204)",
            "Window": "rgb(255, 255, 255)",
            "WindowFrame": "rgb(100, 100, 100)",
            "WindowText": "rgb(0, 0, 0)",
        }

    def _generate_system_fonts(self) -> List[str]:
        """Generate system font keywords for getComputedStyle."""
        return ["caption", "icon", "menu", "message-box", "small-caption", "status-bar"]

    def _generate_codecs(self) -> List[Dict]:
        """Generate MediaCapabilities codec support info."""
        return [
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": "audio/webm; codecs=opus"},
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": "audio/ogg; codecs=vorbis"},
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": "audio/ogg; codecs=flac"},
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": 'audio/mp4; codecs="mp4a.40.2"'},
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": 'audio/mpeg; codecs="mp3"'},
            {"supported": True, "smooth": True, "powerEfficient": False, "contentType": 'video/ogg; codecs="theora"'},
            {"supported": True, "smooth": True, "powerEfficient": True, "contentType": 'video/mp4; codecs="avc1.42E01E"'},
        ]

    def _generate_keyboard_layout(self) -> List[str]:
        """Generate keyboard.getLayoutMap() keys (US QWERTY)."""
        return [
            "KeyA", "KeyB", "KeyC", "KeyD", "KeyE", "KeyF", "KeyG", "KeyH",
            "KeyI", "KeyJ", "KeyK", "KeyL", "KeyM", "KeyN", "KeyO", "KeyP",
            "KeyQ", "KeyR", "KeyS", "KeyT", "KeyU", "KeyV", "KeyW", "KeyX",
            "KeyY", "KeyZ", "Digit0", "Digit1", "Digit2", "Digit3", "Digit4",
            "Digit5", "Digit6", "Digit7", "Digit8", "Digit9", "Minus", "Equal",
            "BracketLeft", "BracketRight", "Semicolon", "Quote", "Backquote",
            "Backslash", "Comma", "Period", "Slash", "Space", "Enter", "Tab",
            "Backspace", "Delete", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp",
            "ArrowDown", "Home", "End", "PageUp", "PageDown", "Insert",
            "F1", "F2", "F3", "F4", "F5", "F6", "F7", "F8", "F9", "F10",
            "F11", "F12", "NumLock", "ScrollLock", "Numpad0", "Numpad1",
            "Numpad2", "Numpad3", "Numpad4", "Numpad5", "Numpad6", "Numpad7",
            "Numpad8", "Numpad9", "NumpadAdd", "NumpadSubtract", "NumpadMultiply",
            "NumpadDivide", "NumpadDecimal", "NumpadEnter", "CapsLock",
            "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight",
            "AltLeft", "AltRight", "MetaLeft", "MetaRight",
            "ContextMenu", "PrintScreen", "Pause",
        ]

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