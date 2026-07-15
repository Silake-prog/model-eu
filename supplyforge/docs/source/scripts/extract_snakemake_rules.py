import re
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
MAIN_SNAKEFILE = ROOT / "Snakefile"


# -----------------------------------------------------------------------------
# 1. Find included files recursively
# -----------------------------------------------------------------------------

def find_includes(file_path, visited=None):
    if visited is None:
        visited = set()

    file_path = file_path.resolve()
    if file_path in visited:
        return []

    visited.add(file_path)

    text = file_path.read_text()
    files = [file_path]

    include_pattern = re.compile(r'include:\s*"([^"]+)"')

    for match in include_pattern.finditer(text):
        rel_path = match.group(1)
        included_file = (file_path.parent / rel_path).resolve()

        if included_file.exists():
            files.extend(find_includes(included_file, visited))

    return files


# -----------------------------------------------------------------------------
# 2. Parse rules from text
# -----------------------------------------------------------------------------

def parse_rules(text, file_path):
    rules = []

    rule_pattern = re.compile(
        r'rule\s+(\w+):\s*(?:"""(.*?)""")?(.*?)(?=\nrule\s|\Z)',
        re.DOTALL,
    )

    for match in rule_pattern.finditer(text):
        name, doc, body = match.groups()

        def extract_field(field):
            pattern = rf"{field}:\s*(.*?)(?=\n\s*\w+:|\Z)"
            m = re.search(pattern, body, re.DOTALL)
            if not m:
                return []
            content = m.group(1)
            return re.findall(r'"([^"]+)"', content)

        script_match = re.search(r'script:\s*"([^"]+)"', body)

        rules.append({
            "name": name,
            "doc": doc.strip() if doc else "",
            "input": extract_field("input"),
            "output": extract_field("output"),
            "params": extract_field("params"),
            "script": script_match.group(1) if script_match else None,
            "basedir": str(file_path.parent),
        })

    return rules


# -----------------------------------------------------------------------------
# 3. Main
# -----------------------------------------------------------------------------

def main():
    if not MAIN_SNAKEFILE.exists():
        raise FileNotFoundError(f"Snakefile not found at {MAIN_SNAKEFILE}")

    all_files = find_includes(MAIN_SNAKEFILE)

    all_rules = []
    for file in all_files:
        text = file.read_text()
        rules = parse_rules(text, file)
        all_rules.extend(rules)

    print(json.dumps(all_rules))


if __name__ == "__main__":
    main()