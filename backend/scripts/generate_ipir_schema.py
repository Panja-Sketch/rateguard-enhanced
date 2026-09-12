import json
import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ipir.schema import generate_ipir_json_schema  # noqa: E402


def main() -> None:
    schema = generate_ipir_json_schema()
    root_dir = Path(__file__).resolve().parent.parent.parent
    output_path = root_dir / "docs" / "architecture" / "ipir-0.1.schema.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(schema, f, indent=2)
        f.write("\n")

    print(f"Successfully generated IPIR 0.1 JSON Schema at: {output_path}")


if __name__ == "__main__":
    main()

