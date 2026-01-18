#!/bin/bash
# Build DuckDB Lambda Layer for Python 3.11
# Run this script to create a Lambda layer zip file

set -e

echo "Building DuckDB Lambda Layer..."

# Clean up previous builds
rm -rf python duckdb-layer.zip

# Create layer directory structure
LAYER_DIR="python"
mkdir -p $LAYER_DIR

# Install DuckDB for Lambda (x86_64 architecture)
echo "Installing DuckDB..."
pip install \
    --platform manylinux2014_x86_64 \
    --target=$LAYER_DIR \
    --implementation cp \
    --python-version 3.11 \
    --only-binary=:all: \
    duckdb==0.10.0

# Create the layer zip
echo "Creating zip file..."
zip -r9 duckdb-layer.zip $LAYER_DIR

# Show result
echo ""
echo "Layer built successfully!"
echo "File: $(pwd)/duckdb-layer.zip"
echo "Size: $(du -h duckdb-layer.zip | cut -f1)"
echo ""
echo "Next steps:"
echo "1. Upload to AWS Lambda Layers:"
echo "   aws lambda publish-layer-version \\"
echo "     --layer-name duckdb-python311 \\"
echo "     --zip-file fileb://duckdb-layer.zip \\"
echo "     --compatible-runtimes python3.11 \\"
echo "     --compatible-architectures x86_64"
