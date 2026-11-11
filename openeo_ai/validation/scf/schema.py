"""SCF JSON schema (lightweight definition for documentation and validation)."""

SCF_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "SCF",
    "type": "object",
    "required": [
        "scf_version",
        "domain_profile",
        "policy_profile",
        "data",
        "transformations",
        "output",
    ],
    "properties": {
        "scf_version": {"type": "string"},
        "domain_profile": {"type": "string"},
        "policy_profile": {"type": "object"},
        "data": {
            "type": "object",
            "required": ["collection", "bands", "spatial_extent", "temporal_extent"],
            "properties": {
                "collection": {"type": "string"},
                "bands": {"type": "array", "items": {"type": "string"}},
                "spatial_extent": {
                    "type": "object",
                    "required": ["west", "south", "east", "north"],
                },
                "temporal_extent": {
                    "type": "object",
                    "required": ["start", "end"],
                },
                "crs": {"type": "string"},
                "resolution": {
                    "type": "object",
                    "properties": {
                        "x": {"type": "number"},
                        "y": {"type": "number"},
                        "unit": {"type": "string"},
                    },
                },
            },
        },
        "quality_constraints": {"type": "object"},
        "transformations": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "type"],
                "properties": {
                    "id": {"type": "string"},
                    "type": {"type": "string"},
                    "formula": {"type": "string"},
                    "expression": {"type": "string"},
                    "dimension": {"type": "string"},
                    "reducer": {"type": "string"},
                    "period": {"type": "string"},
                    "temporal_step": {"type": "string"},
                    "polarization": {"type": "string"},
                    "scale": {"type": "string"},
                    "threshold": {"type": "number"},
                    "base_temperature": {"type": "number"},
                    "band": {"type": "string"},
                    "variable": {"type": "string"},
                },
            },
        },
        "output": {
            "type": "object",
            "required": ["type", "dimensions"],
            "properties": {
                "type": {"type": "string"},
                "dimensions": {"type": "array", "items": {"type": "string"}},
                "format": {"type": "string"},
            },
        },
        "notes": {"type": "object"},
    },
}
