"""Offline validation of the five proposed schemas; never loads the runtime TTL."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

FILES = ("common.ttl", "bond_kr.ttl", "etf_kr.ttl", "etf_gl.ttl", "fund_pub.ttl")
DIRECTORY = Path(__file__).resolve().parents[1] / "ontology"


def load_schema(directory: Path = DIRECTORY):
    from rdflib import Graph

    graph = Graph()
    for name in FILES:
        graph.parse(directory / name, format="turtle")
    return graph


def validate_data(data, schema):
    from pyshacl import validate

    return validate(
        data, shacl_graph=schema, ont_graph=schema,
        inference="none", do_owl_imports=False, meta_shacl=True,
        advanced=False, js=False,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, help="One product snapshot graph, in Turtle")
    args = parser.parse_args(argv)
    try:
        from rdflib import Graph

        schema = load_schema()
        data = Graph().parse(args.data, format="turtle") if args.data else Graph()
        conforms, _, report = validate_data(data, schema)
    except ImportError:
        print('Install validation dependencies: python -m pip install -e ".[ontology-validation]"', file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"Ontology validation failed: {exc}", file=sys.stderr)
        return 1
    if not conforms:
        print(report, file=sys.stderr)
        return 1
    print(f"PASS: five schemas / meta-SHACL ({len(schema)} triples)"
          + (f" / data: {args.data}" if args.data else " (no product data supplied)"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
