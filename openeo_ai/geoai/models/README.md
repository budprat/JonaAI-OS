# GeoAI Models Directory

This directory contains pre-trained model configurations for GeoAI inference tasks.

## Directory Structure

```
models/
├── segmentation_default/
│   ├── model_config.json    # Model metadata
│   └── model.onnx           # Model weights (optional)
├── change_default/
│   ├── model_config.json
│   └── model.onnx
└── canopy_height_default/
    ├── model_config.json
    └── model.onnx
```

## Model Configuration

Each model requires a `model_config.json` file with the following fields:

```json
{
  "name": "model_name",
  "task": "segmentation|change_detection|canopy_height",
  "version": "1.0.0",
  "description": "Model description",
  "input_bands": ["red", "green", "blue", "nir"],
  "input_size": 256,
  "output_classes": ["class1", "class2"],
  "framework": "onnx|pytorch",
  "model_file": "model.onnx"
}
```

## Supported Tasks

### Segmentation
- Semantic land cover classification
- Input: 4-band imagery (RGB+NIR)
- Output: Class labels per pixel

### Change Detection
- Bi-temporal change analysis
- Input: Two aligned images (before/after)
- Output: Binary change mask

### Canopy Height
- Tree height estimation from RGB
- Input: 3-band RGB imagery
- Output: Height values in meters

## Adding New Models

1. Create a directory with the model name
2. Add `model_config.json` with metadata
3. Add model weights as `model.onnx` or `model.pt`
4. Restart the application to auto-discover

## No model, no answer

If a model has no weights file, or its framework (onnxruntime or torch) isn't installed,
loading it raises `GeoAIModelUnavailable`, and the GeoAI tools answer with an error naming
the model and the reason (`no_model_file` or `missing_framework`). They don't run.

The random `StubModel` is for tests only, through `ModelRegistry(allow_stub=True)` or
`GeoAIInference(allow_stub=True)`. It is deliberately a constructor argument, not a setting,
so no deployment can turn it on. Until 2026-11-10 the registry fell back to it silently, and
the tools reported random numbers as land cover, change and canopy height.

## Supported Frameworks

- **ONNX** (recommended): Place `model.onnx` in model directory
- **PyTorch**: Place `model.pt` in model directory
