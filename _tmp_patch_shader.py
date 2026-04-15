filepath = '/root/yandex-maps-bot/core/browser_manager.py'

with open(filepath, 'r') as f:
    lines = f.readlines()

# Find and comment out getShaderPrecisionFormat patch
in_block = False
start_line = None
end_line = None

for i, line in enumerate(lines):
    stripped = line.strip()
    if '// --- Patch getShaderPrecisionFormat ---' in stripped:
        start_line = i
        in_block = True
    if in_block and i > start_line + 2:
        # Find the closing of the try block - next section starts with "// ---" or "}} catch"
        if stripped.startswith('// ---') or stripped == '}} catch(e) {{}}':
            end_line = i
            break

if start_line is not None and end_line is not None:
    new_lines = lines[:start_line]
    new_lines.append('                // --- getShaderPrecisionFormat: NOT overridden, real SwiftShader precision used ---\n')
    new_lines.append('\n')
    new_lines.extend(lines[end_line:])
    
    with open(filepath, 'w') as f:
        f.writelines(new_lines)
    print(f'OK: Removed getShaderPrecisionFormat patch (lines {start_line+1}-{end_line})')
else:
    print(f'WARN: Block not found. start={start_line}, end={end_line}')
