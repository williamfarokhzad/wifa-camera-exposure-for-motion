"""Run one exposure-budget JSON example; never communicates with hardware."""
import argparse
import json
from pathlib import Path
from motion import run_document


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("configuration", type=Path, help="path to one example JSON file")
    args = parser.parse_args()
    try:
        document = json.loads(args.configuration.read_text(encoding="utf-8"))
        result = run_document(document)
        output = json.dumps(result, indent=2, allow_nan=False)
    except (OSError, UnicodeError, ValueError, OverflowError) as exc:
        parser.error(str(exc))
    print(output)


if __name__ == "__main__":
    main()
