#!/usr/bin/env python3
"""
LeafyGreen skill graph query helper.

Usage:
  python query_graph.py tokens [--theme light|dark] [--section palette|typography|spacing|...]
  python query_graph.py components [--category form|feedback|navigation|layout|dataDisplay]
  python query_graph.py component <name>
  python query_graph.py contract

This script provides efficient parameter lookups from graph files.
Instead of reading an entire JSON file (7000+ tokens) to get one theme's palette,
this returns only the requested subset (~700 tokens).
"""

import json
import sys
import os
import argparse

GRAPH_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'graph')

def load_json(filename):
    path = os.path.join(GRAPH_DIR, filename)
    with open(path, 'r') as f:
        return json.load(f)

def query_tokens(args):
    data = load_json('tokens.json')
    
    if args.theme:
        theme = args.theme
        if theme in data.get('themes', {}):
            result = {'theme': theme, 'tokens': data['themes'][theme]}
            if args.section:
                if args.section in data:
                    result[args.section] = data[args.section]
            print(json.dumps(result, indent=2))
            return
        else:
            print(f"Unknown theme: {theme}. Available: {list(data.get('themes', {}).keys())}")
            sys.exit(1)
    
    if args.section:
        section = args.section
        if section in data:
            print(json.dumps({section: data[section]}, indent=2))
            return
        else:
            available = [k for k in data.keys() if k not in ('_id', 'skill_id', 'version')]
            print(f"Unknown section: {section}. Available: {available}")
            sys.exit(1)
    
    # No filters: list available sections
    available = [k for k in data.keys() if k not in ('_id', 'skill_id', 'version')]
    print(f"Available token sections: {available}")
    print("Use --theme light|dark for theme-specific tokens")
    print("Use --section <name> for a specific token category")

def query_components(args):
    data = load_json('components.json')
    categories = data.get('categories', {})
    
    if args.category:
        cat = args.category
        if cat in categories:
            print(json.dumps({cat: categories[cat]}, indent=2))
            return
        else:
            print(f"Unknown category: {cat}. Available: {list(categories.keys())}")
            sys.exit(1)
    
    # List categories with component counts
    for cat_name, cat_data in categories.items():
        components = [k for k in cat_data.keys()]
        print(f"{cat_name} ({len(components)}): {', '.join(components)}")

def query_component(args):
    data = load_json('components.json')
    categories = data.get('categories', {})
    name = args.name.lower()
    
    for cat_name, cat_data in categories.items():
        for comp_name, comp_data in cat_data.items():
            if comp_name.lower() == name:
                print(json.dumps({comp_name: comp_data, '_category': cat_name}, indent=2))
                return
    
    # Not found - list all
    all_components = []
    for cat_data in categories.values():
        all_components.extend(cat_data.keys())
    print(f"Component '{args.name}' not found. Available: {sorted(all_components)}")
    sys.exit(1)

def query_contract(args):
    data = load_json('contract.json')
    print(json.dumps(data, indent=2))

def main():
    parser = argparse.ArgumentParser(description='LeafyGreen skill graph query helper')
    subparsers = parser.add_subparsers(dest='command', help='Query type')
    
    # tokens subcommand
    tokens_parser = subparsers.add_parser('tokens', help='Query design tokens')
    tokens_parser.add_argument('--theme', choices=['light', 'dark'], help='Theme-specific tokens')
    tokens_parser.add_argument('--section', help='Token section (palette, typography, spacing, etc.)')
    
    # components subcommand
    components_parser = subparsers.add_parser('components', help='Query component catalog')
    components_parser.add_argument('--category', choices=['form', 'feedback', 'navigation', 'layout', 'dataDisplay', 'utility'], help='Component category')
    
    # component (singular) subcommand
    component_parser = subparsers.add_parser('component', help='Query a specific component')
    component_parser.add_argument('name', help='Component name (e.g., button, table, modal)')
    
    # contract subcommand
    subparsers.add_parser('contract', help='Show skill contract/ABI')
    
    args = parser.parse_args()
    
    if args.command == 'tokens':
        query_tokens(args)
    elif args.command == 'components':
        query_components(args)
    elif args.command == 'component':
        query_component(args)
    elif args.command == 'contract':
        query_contract(args)
    else:
        parser.print_help()

if __name__ == '__main__':
    main()
