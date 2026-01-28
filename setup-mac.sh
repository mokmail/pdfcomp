#!/bin/bash

# macOS Native Setup Script for pdfcomp
# This script installs all dependencies for running pdfcomp natively on macOS

echo "=== pdfcomp macOS Native Setup ==="
echo ""

# Check if Homebrew is installed
if ! command -v brew &> /dev/null; then
    echo "❌ Homebrew is not installed!"
    echo "Please install Homebrew first: https://brew.sh/"
    echo "Run: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
    exit 1
fi

echo "✓ Homebrew is installed"

# Check if Python 3 is installed
if ! command -v python3 &> /dev/null; then
    echo "⚠️  Python 3 is not installed. Installing via Homebrew..."
    brew install python3
else
    echo "✓ Python 3 is installed ($(python3 --version))"
fi

# Install poppler (required for pdf2image)
echo ""
echo "Installing poppler (required for PDF processing)..."
if brew list poppler &> /dev/null; then
    echo "✓ poppler is already installed"
else
    brew install poppler
    if [ $? -eq 0 ]; then
        echo "✓ poppler installed successfully"
    else
        echo "❌ Failed to install poppler"
        exit 1
    fi
fi

# Install Python dependencies
echo ""
echo "Installing Python dependencies..."
if [ -f "requirements.txt" ]; then
    pip3 install -r requirements.txt
else
    pip3 install pdf2image Pillow
fi

if [ $? -eq 0 ]; then
    echo "✓ Python dependencies installed successfully"
else
    echo "❌ Failed to install Python dependencies"
    exit 1
fi

# Verify installation
echo ""
echo "Verifying installation..."
python3 -c "import pdf2image; import PIL; print('✓ All Python packages imported successfully')" 2>/dev/null

if [ $? -ne 0 ]; then
    echo "❌ Package verification failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "✅ Setup complete!"
echo "=========================================="
echo ""
echo "You can now run the application with:"
echo "  python3 pdfcomp.py"
echo ""
echo "Or make it executable and run directly:"
echo "  chmod +x pdfcomp.py"
echo "  ./pdfcomp.py"
echo ""
