import os

filepath = r"c:\Users\Bilal\Desktop\AI_Email_Automation\Pages\dashboard.html"
with open(filepath, 'r', encoding='utf-8') as f:
    lines = f.readlines()

start_idx = -1
end_idx = -1

for i, line in enumerate(lines):
    if "<!-- DELETING REST OF SCRIPT -->" in line:
        start_idx = i
    if start_idx != -1 and i > start_idx and "</script>" in line:
        end_idx = i
        break

if start_idx != -1 and end_idx != -1:
    new_lines = lines[:start_idx] + lines[end_idx+1:]
    with open(filepath, 'w', encoding='utf-8') as f:
        f.writelines(new_lines)
    print(f"Successfully deleted from line {start_idx+1} to {end_idx+1}")
else:
    print(f"Failed to find markers: start={start_idx}, end={end_idx}")
