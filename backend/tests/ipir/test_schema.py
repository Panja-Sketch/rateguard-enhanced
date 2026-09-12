from app.ipir.schema import generate_ipir_json_schema


def test_ipir_schema_generation() -> None:
    schema = generate_ipir_json_schema()
    assert isinstance(schema, dict)
    assert schema.get("title") == "IPIRPackage"
    properties = schema.get("properties", {})
    assert "ipir_version" in properties
    assert "inputs" in properties
    assert "tables" in properties
    assert "calculations" in properties
