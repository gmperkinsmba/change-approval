#!/usr/bin/env python3
"""Start Review Before Action on loopback."""
import argparse
from rba.server import serve

parser = argparse.ArgumentParser(description="Run the local review and approval demo")
parser.add_argument("--host", default="127.0.0.1")
parser.add_argument("--port", type=int, default=8335)
parser.add_argument("--runtime", help="Private local runtime directory outside the checkout")
args = parser.parse_args()
serve(args.host, args.port, args.runtime)
