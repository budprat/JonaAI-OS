"""Fix pydantic v1 -> v2 compatibility issues in installed packages.

This script patches openeo_pg_parser_networkx and other packages that
use pydantic v1 patterns when pydantic v2 is installed.
"""
import re
import sys
import site
import os


def get_site_packages():
    """Get the site-packages directory."""
    for p in site.getsitepackages():
        if os.path.isdir(p):
            return p
    for p in sys.path:
        if 'site-packages' in p and os.path.isdir(p):
            return p
    return None


def patch_file(filepath, patches):
    """Apply text replacements to a file."""
    if not os.path.exists(filepath):
        return False
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    for old, new in patches:
        content = content.replace(old, new)
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        return True
    return False


def patch_pg_schema(site_pkg):
    """Patch openeo_pg_parser_networkx/pg_schema.py for pydantic v2."""
    filepath = os.path.join(site_pkg, 'openeo_pg_parser_networkx', 'pg_schema.py')
    if not os.path.exists(filepath):
        print(f"  Skip: {filepath} not found")
        return
    
    with open(filepath, 'r') as f:
        content = f.read()
    
    original = content
    
    content = content.replace('constr(regex=', 'constr(pattern=')
    content = content.replace('conlist(', 'List[')  
    content = content.replace(', min_items=', ']  # min_items=')
    content = content.replace(', max_items=', ', max_items=')
    
    content = re.sub(
        r'from pydantic import (.+)',
        lambda m: m.group(0) + '\nfrom pydantic import RootModel' if 'RootModel' not in m.group(0) else m.group(0),
        content,
        count=1
    )
    
    if content != original:
        with open(filepath, 'w') as f:
            f.write(content)
        print(f"  Patched: {filepath}")
    else:
        print(f"  Already patched: {filepath}")


def main():
    site_pkg = get_site_packages()
    if not site_pkg:
        pythonlibs = os.path.expanduser('~/.pythonlibs/lib/python3.11/site-packages')
        if os.path.isdir(pythonlibs):
            site_pkg = pythonlibs
        else:
            workspace_libs = '/home/runner/workspace/.pythonlibs/lib/python3.11/site-packages'
            if os.path.isdir(workspace_libs):
                site_pkg = workspace_libs
    
    if not site_pkg:
        print("Could not find site-packages directory")
        sys.exit(1)
    
    print(f"Site packages: {site_pkg}")
    
    pg_schema = os.path.join(site_pkg, 'openeo_pg_parser_networkx', 'pg_schema.py')
    if os.path.exists(pg_schema):
        patches = [
            ('constr(regex=', 'constr(pattern='),
        ]
        if patch_file(pg_schema, patches):
            print(f"  Patched regex->pattern in pg_schema.py")
    
    print("Pydantic compatibility patches applied.")


if __name__ == '__main__':
    main()
