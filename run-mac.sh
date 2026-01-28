#!/bin/bash

# macOS Docker run script for pdfcomp
# This script simplifies running the PDF comparison tool on macOS with Docker

echo "=== pdfcomp macOS Docker Setup ==="
echo ""

# Check if XQuartz is installed
if ! command -v xquartz &> /dev/null && [ ! -d "/Applications/Utilities/XQuartz.app" ]; then
    echo "❌ XQuartz is not installed!"
    echo "Please install it with: brew install --cask xquartz"
    echo "Or download from: https://www.xquartz.org/"
    exit 1
fi

# Check if XQuartz is running
if ! pgrep -x "Xquartz" > /dev/null && ! pgrep -x "X11" > /dev/null; then
    echo "⚠️  XQuartz is not running. Starting XQuartz..."
    open -a XQuartz
    echo "Please wait for XQuartz to start (5 seconds)..."
    sleep 5
fi

# Enable X11 forwarding
echo "Enabling X11 forwarding..."
xhost + 127.0.0.1 > /dev/null 2>&1

# Get IP address
IP=$(ifconfig en0 | grep inet | awk '$1=="inet" {print $2}')

if [ -z "$IP" ]; then
    echo "⚠️  Could not detect IP from en0, trying alternative interfaces..."
    IP=$(ifconfig en1 | grep inet | awk '$1=="inet" {print $2}')
fi

if [ -z "$IP" ]; then
    echo "❌ Could not detect your IP address. Using localhost..."
    IP="127.0.0.1"
fi

echo "✓ Using IP address: $IP"
echo "✓ Display will be set to: ${IP}:0"
echo ""

# Check if Docker image exists
if ! docker images pdfcomp:latest | grep -q pdfcomp; then
    echo "Docker image 'pdfcomp:latest' not found. Building now..."
    docker build . -t pdfcomp:latest
    if [ $? -ne 0 ]; then
        echo "❌ Failed to build Docker image"
        exit 1
    fi
    echo "✓ Docker image built successfully"
fi

echo "Starting pdfcomp in Docker..."
echo ""

# Run the container
docker run \
  -e DISPLAY=${IP}:0 \
  -v $(pwd):/app \
  --rm \
  pdfcomp:latest

# Clean up
echo ""
echo "Disabling X11 forwarding..."
xhost - 127.0.0.1 > /dev/null 2>&1
echo "✓ Done"
