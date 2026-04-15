import json
from playwright.sync_api import sync_playwright

JS_CODE = """() => {
    const canvas = document.createElement("canvas");
    const gl = canvas.getContext("webgl");
    const ext = gl.getExtension("WEBGL_debug_renderer_info");
    const canvas2 = document.createElement("canvas");
    const gl2 = canvas2.getContext("webgl2");
    const ext2 = gl2 ? gl2.getExtension("WEBGL_debug_renderer_info") : null;
    return {
        vendor: gl.getParameter(gl.VENDOR),
        renderer: gl.getParameter(gl.RENDERER),
        unmaskedVendor: ext ? gl.getParameter(ext.UNMASKED_VENDOR_WEBGL) : null,
        unmaskedRenderer: ext ? gl.getParameter(ext.UNMASKED_RENDERER_WEBGL) : null,
        version: gl.getParameter(gl.VERSION),
        shadingLanguage: gl.getParameter(gl.SHADING_LANGUAGE_VERSION),
        maxTextureSize: gl.getParameter(gl.MAX_TEXTURE_SIZE),
        maxViewportDims: Array.from(gl.getParameter(gl.MAX_VIEWPORT_DIMS)),
        maxVertexAttribs: gl.getParameter(gl.MAX_VERTEX_ATTRIBS),
        maxFragmentUniformVectors: gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_VECTORS),
        maxVertexUniformVectors: gl.getParameter(gl.MAX_VERTEX_UNIFORM_VECTORS),
        maxVaryingVectors: gl.getParameter(gl.MAX_VARYING_VECTORS),
        maxCombinedTextureImageUnits: gl.getParameter(gl.MAX_COMBINED_TEXTURE_IMAGE_UNITS),
        maxTextureImageUnits: gl.getParameter(gl.MAX_TEXTURE_IMAGE_UNITS),
        maxVertexTextureImageUnits: gl.getParameter(gl.MAX_VERTEX_TEXTURE_IMAGE_UNITS),
        maxCubeMapTextureSize: gl.getParameter(gl.MAX_CUBE_MAP_TEXTURE_SIZE),
        maxRenderBufferSize: gl.getParameter(gl.MAX_RENDERBUFFER_SIZE),
        aliasedLineWidthRange: Array.from(gl.getParameter(gl.ALIASED_LINE_WIDTH_RANGE)),
        aliasedPointSizeRange: Array.from(gl.getParameter(gl.ALIASED_POINT_SIZE_RANGE)),
        extensions: gl.getSupportedExtensions(),
        version2: gl2 ? gl2.getParameter(gl2.VERSION) : null,
        shadingLanguage2: gl2 ? gl2.getParameter(gl2.SHADING_LANGUAGE_VERSION) : null,
        extensions2: gl2 ? gl2.getSupportedExtensions() : null,
        unmaskedVendor2: ext2 ? gl2.getParameter(ext2.UNMASKED_VENDOR_WEBGL) : null,
        unmaskedRenderer2: ext2 ? gl2.getParameter(ext2.UNMASKED_RENDERER_WEBGL) : null,
    };
}"""

with sync_playwright() as p:
    ctx = p.chromium.launch_persistent_context(
        "/tmp/test_swiftshader",
        headless=False,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--enable-unsafe-swiftshader",
        ],
        viewport={"width": 800, "height": 600},
    )
    page = ctx.pages[0] if ctx.pages else ctx.new_page()
    result = page.evaluate(JS_CODE)
    print(json.dumps(result, indent=2))
    ctx.close()
