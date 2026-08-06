#!/usr/bin/env python3
import os

base_path = r"E:\adel\scrapper magic\src\scrapling_tool\core"
print(f"Checking directory: {base_path}")

if os.path.exists(base_path):
    files = os.listdir(base_path)
    print(f"Files in core directory: {files}")
    for f in files:
        full_path = os.path.join(base_path, f)
        if os.path.isfile(full_path):
            print(f"  FILE: {f}")
        else:
            print(f"  DIR: {f}")
else:
    print("Directory does not exist")

# Also check parent directories
print("\nDirectory structure:")
for root, dirs, files in os.walk(r"E:\adel\scrapper magic\src"):
    level = root.replace(r"E:\adel\scrapper magic\src", "").count(os.sep)
    indent = " " * 2 * level
    print(f"{indent}{os.path.basename(root)}/")
    subindent = " " * 2 * (level + 1)
    for file in files:
        if file.endswith('.py'):
            print(f"{subindent}{file}")