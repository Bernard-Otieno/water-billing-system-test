"""
Loads every Jinja2 template in app/templates and confirms it parses without
syntax errors - unbalanced {% block %}/{% endblock %} tags, bad {% if %}
nesting, malformed expressions, etc.

This catches the class of bug that would otherwise only surface when someone
actually loads that specific page in production (or worse, doesn't - broken
templates can sit undetected for weeks if a page is rarely visited).

Run locally with: python scripts/check_templates.py
"""

import sys
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, TemplateSyntaxError

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


def main():
    if not TEMPLATES_DIR.is_dir():
        print(f"Templates directory not found: {TEMPLATES_DIR}")
        sys.exit(1)

    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    template_files = sorted(TEMPLATES_DIR.glob("*.html"))

    if not template_files:
        print(f"No .html templates found in {TEMPLATES_DIR}")
        sys.exit(1)

    errors = []
    for path in template_files:
        name = path.name
        try:
            env.get_template(name)
            print(f"OK    {name}")
        except TemplateSyntaxError as e:
            errors.append(name)
            print(f"FAIL  {name}: line {e.lineno}: {e.message}")

    print()
    if errors:
        print(f"{len(errors)} of {len(template_files)} template(s) failed to parse.")
        sys.exit(1)

    print(f"All {len(template_files)} templates parsed successfully.")


if __name__ == "__main__":
    main()
