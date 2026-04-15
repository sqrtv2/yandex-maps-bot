import sys

filepath = '/root/yandex-maps-bot/core/browser_manager.py'

with open(filepath, 'r') as f:
    lines = f.readlines()

# Find and comment out getSupportedExtensions patch (around line 1177-1191)
in_block = False
start_line = None
end_line = None

for i, line in enumerate(lines):
    stripped = line.strip()
    if '// --- Patch getSupportedExtensions ---' in stripped:
        start_line = i
        in_block = True
    if in_block and '// --- Patch getContextAttributes ---' in stripped:
        end_line = i
        break

if start_line is not None and end_line is not None:
    # Replace lines from start_line to end_line (exclusive) with comment
    new_lines = lines[:start_line]
    new_lines.append('                // --- getSupportedExtensions: NOT overridden, real SwiftShader extensions used ---\n')
    new_lines.append('                // Overriding with profile-specific extensions creates mismatch with SwiftShader renderer\n')
    new_lines.append('\n')
    new_lines.extend(lines[end_line:])
    
    with open(filepath, 'w') as f:
        f.writelines(new_lines)
    print(f'OK: Removed getSupportedExtensions patch (lines {start_line+1}-{end_line})')
else:
    print(f'WARN: Block not found. start={start_line}, end={end_line}')
