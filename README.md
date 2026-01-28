# pdfcomp

This tool helps to find out changes made to a PDF file by comparing old and last state.

## Prerequisites

- Docker Desktop installed
- For macOS: XQuartz installed (for GUI display)
- Python 3.9+ (for native installation)

## Installation Options

### Option 1: Native Installation (Recommended for macOS)

#### macOS

```bash
# Install dependencies
brew install poppler  # Required for pdf2image

# Install Python packages
pip install pdf2image Pillow

# Run the application
python pdfcomp.py
```

#### Ubuntu/Linux

```bash
# Install system dependencies
sudo apt-get update
sudo apt-get install -y python3-tk poppler-utils

# Install Python packages
pip install pdf2image Pillow

# Run the application
python pdfcomp.py
```

#### Windows

```powershell
# Install Python packages
pip install pdf2image Pillow

# Download poppler for Windows from: https://github.com/oschwartz10612/poppler-windows/releases/
# Extract and add bin/ folder to PATH

# Run the application
python pdfcomp.py
```

### Option 2: Docker Installation

#### Build Docker Image

```bash
docker build . -t pdfcomp:latest
```

#### Run on Ubuntu/Linux

```bash
docker run -u=$(id -u $USER):$(id -g $USER) \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix:rw \
  -v $(pwd):/app \
  --rm pdfcomp:latest
```

#### Run on macOS

**Step 1: Install and Configure XQuartz**

```bash
# Install XQuartz
brew install --cask xquartz

# Start XQuartz
open -a XQuartz

# In XQuartz preferences, go to Security tab and enable:
# "Allow connections from network clients"
```

**Step 2: Configure X11 Forwarding**

```bash
# Allow X11 forwarding from localhost
xhost + 127.0.0.1

# Or allow from all (less secure)
# xhost +
```

**Step 3: Run Docker Container**

```bash
# Get your IP address
IP=$(ifconfig en0 | grep inet | awk '$1=="inet" {print $2}')

# Run the container
docker run \
  -e DISPLAY=${IP}:0 \
  -v $(pwd):/app \
  --rm pdfcomp:latest
```

**Alternative: Use the provided script for macOS**

```bash
# Make the script executable
chmod +x run-mac.sh

# Run the script
./run-mac.sh
```

#### Run on Windows

For Windows, native installation is recommended. If you need Docker:

1. Install VcXsrv or Xming for X11 forwarding
2. Configure the X server to allow connections
3. Run:

```powershell
docker run -e DISPLAY=host.docker.internal:0 -v ${PWD}:/app --rm pdfcomp:latest
```

## Usage

1. Launch the application
2. Click "Open 1st PDF" and select the first PDF file
3. Click "Open 2nd PDF" and select the second PDF file
4. Click "START" to compare the PDFs
5. The comparison result will be displayed and saved as `result.png`

## Troubleshooting

### macOS: "Cannot open display" error

- Ensure XQuartz is running
- Check that X11 forwarding is allowed: `xhost + 127.0.0.1`
- Restart XQuartz and try again

### macOS: Better Alternative - Native Installation

Due to X11 complexity on macOS, we recommend using the native installation method instead of Docker.

### Linux: Permission denied on X11

Run: `xhost +local:docker`

### All platforms: Missing poppler

The `pdf2image` library requires poppler-utils to be installed on your system.
