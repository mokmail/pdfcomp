@echo off
REM Windows Setup Script for pdfcomp
REM This script installs Python dependencies for running pdfcomp on Windows

echo === pdfcomp Windows Setup ===
echo.

REM Check if Python is installed
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo X Python is not installed!
    echo Please install Python 3.9 or later from: https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation
    pause
    exit /b 1
)

echo + Python is installed
python --version

REM Check if pip is available
pip --version >nul 2>&1
if %errorlevel% neq 0 (
    echo X pip is not installed!
    echo Please reinstall Python with pip included
    pause
    exit /b 1
)

echo + pip is installed

REM Install Python dependencies
echo.
echo Installing Python dependencies...
if exist requirements.txt (
    pip install -r requirements.txt
) else (
    pip install pdf2image Pillow
)

if %errorlevel% neq 0 (
    echo X Failed to install Python dependencies
    pause
    exit /b 1
)

echo + Python dependencies installed successfully

REM Check for poppler
echo.
echo ==========================================
echo IMPORTANT: Poppler Installation Required
echo ==========================================
echo.
echo The pdf2image library requires poppler to be installed.
echo.
echo Please follow these steps:
echo 1. Download poppler for Windows from:
echo    https://github.com/oschwartz10612/poppler-windows/releases/
echo.
echo 2. Extract the downloaded file to a location like:
echo    C:\Program Files\poppler
echo.
echo 3. Add the bin folder to your PATH:
echo    - Open System Properties ^(Win + Pause^)
echo    - Click "Advanced system settings"
echo    - Click "Environment Variables"
echo    - Under "System variables", find and edit "Path"
echo    - Add: C:\Program Files\poppler\Library\bin
echo.
echo 4. Restart your command prompt
echo.

REM Verify installation
echo Verifying Python packages...
python -c "import pdf2image; import PIL; print('+ All Python packages imported successfully')" 2>nul

if %errorlevel% neq 0 (
    echo X Package verification failed
    pause
    exit /b 1
)

echo.
echo ==========================================
echo Setup complete!
echo ==========================================
echo.
echo After installing poppler, you can run the application with:
echo   python pdfcomp.py
echo.
pause
