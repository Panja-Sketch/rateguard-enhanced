import json
import sys
from pathlib import Path

# Ensure backend root is in sys.path when script is executed directly
backend_dir = Path(__file__).resolve().parent.parent
if str(backend_dir) not in sys.path:
    sys.path.insert(0, str(backend_dir))

from app.ipir.package import IPIRPackage  # noqa: E402


def generate_platform_configs() -> None:
    root_dir = Path(__file__).resolve().parent.parent.parent
    canonical_ipir_file = root_dir / "data" / "implementations" / "canonical" / "AZ_HO3_2026_09_ipir.json"
    defective_ipir_file = root_dir / "data" / "implementations" / "defective" / "AZ_HO3_2026_09_ipir.json"

    with open(canonical_ipir_file, encoding="utf-8") as f:
        can_pkg = IPIRPackage.model_validate_json(f.read())

    with open(defective_ipir_file, encoding="utf-8") as f:
        def_pkg = IPIRPackage.model_validate_json(f.read())

    config_dir = root_dir / "data" / "implementations" / "platform_config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # 1. Build Canonical Platform Config JSON
    can_config = {
        "vendor_neutral_format": "SYNTHETIC_RATING_PLATFORM_V1",
        "description": "Guidewire-inspired synthetic rating engine configuration representation.",
        "rateBook": {
            "bookCode": can_pkg.id,
            "bookName": can_pkg.name,
            "edition": can_pkg.version,
            "effectiveDate": str(can_pkg.effective_period.start),
            "jurisdiction": can_pkg.product.jurisdiction.state_or_province,
        },
        "ipir_payload": can_pkg.model_dump(mode="json"),
    }

    # 2. Build Defective Platform Config JSON
    def_config = {
        "vendor_neutral_format": "SYNTHETIC_RATING_PLATFORM_V1",
        "description": "Guidewire-inspired synthetic rating engine configuration with intentional defects.",
        "rateBook": {
            "bookCode": def_pkg.id,
            "bookName": def_pkg.name,
            "edition": def_pkg.version,
            "effectiveDate": str(def_pkg.effective_period.start),
            "jurisdiction": def_pkg.product.jurisdiction.state_or_province,
        },
        "ipir_payload": def_pkg.model_dump(mode="json"),
    }

    can_file = config_dir / "AZ_HO3_2026_09_platform_config.json"
    def_file = config_dir / "AZ_HO3_2026_09_defective_platform_config.json"

    with open(can_file, "w", encoding="utf-8") as f:
        json.dump(can_config, f, indent=2)

    with open(def_file, "w", encoding="utf-8") as f:
        json.dump(def_config, f, indent=2)

    print(f"Successfully generated canonical platform config at: {can_file}")
    print(f"Successfully generated defective platform config at: {def_file}")


if __name__ == "__main__":
    generate_platform_configs()

