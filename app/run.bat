@echo off
REM AI Hedge Fund Web Application Setup and Runner (Windows)
REM This script makes it easy for non-technical users to run the full web application

REM Colors for output
set "INFO=[INFO]"
set "SUCCESS=[SUCCESS]"
set "WARNING=[WARNING]"
set "ERROR=[ERROR]"

REM Check Node.js
where node >nul 2>&1
if %errorlevel% neq 0 (
    echo %ERROR% Node.js is not installed. Please install from https://nodejs.org/
    pause
    exit /b 1
)

REM Check npm
where npm >nul 2>&1
if %errorlevel% neq 0 (
    echo %ERROR% npm is not installed. Please install Node.js from https://nodejs.org/
    pause
    exit /b 1
)

REM Check Python (or python3)
where python >nul 2>&1
if %errorlevel% neq 0 (
    where python3 >nul 2>&1
    if %errorlevel% neq 0 (
        echo %ERROR% Python is not installed. Please install from https://python.org/
        pause
        exit /b 1
    )
)

REM Determine the Python launcher (python or python3)
set "PY=python"
where python >nul 2>&1
if %errorlevel% neq 0 set "PY=python3"

echo %SUCCESS% Using Python launcher: %PY%

REM Ensure correct working directory
if not exist "frontend" (
    echo %ERROR% This script must be run from the app\ directory
    echo %ERROR% Please navigate to the app\ directory and run: run.bat
    pause
    exit /b 1
)

if not exist "backend" (
    echo %ERROR% This script must be run from the app\ directory
    echo %ERROR% Please navigate to the app\ directory and run: run.bat
    pause
    exit /b 1
)

echo.
echo %INFO% AI Hedge Fund Web Application Setup
echo %INFO% This script will install dependencies and start both frontend and backend services
echo.

REM Check for .env
if not exist "..\.env" (
    if exist "..\.env.example" (
        echo %WARNING% No .env file found. Creating from .env.example...
        copy "..\.env.example" "..\.env"
        echo %WARNING% Please edit ..\.env to add your API keys:
        echo %WARNING%   - OPENAI_API_KEY=your-openai-api-key
        echo %WARNING%   - GROQ_API_KEY=your-groq-api-key
        echo %WARNING%   - FINANCIAL_DATASETS_API_KEY=your-financial-datasets-api-key
        echo.
    ) else (
        echo %ERROR% No .env or .env.example file found in the root directory.
        echo %ERROR% Please create a .env file with your API keys.
        pause
        exit /b 1
    )
) else (
    echo %SUCCESS% Environment file (.env)
)

REM Setup database
echo %INFO% Setting up database...
echo %INFO% Database: SQLite (hedge_fund.db)
echo %INFO% Location: Project root directory
echo %INFO% Tables will be created automatically on first backend startup

if exist "..\hedge_fund.db" (
    echo %SUCCESS% Database file already exists
) else (
    echo %INFO% Database will be created when backend starts for the first time
)

REM Install backend dependencies into a .venv at the repo root
echo %INFO% Installing backend dependencies...
cd ..

if not exist ".venv\Scripts\python.exe" (
    echo %INFO% Creating virtual environment (.venv)...
    %PY% -m venv .venv
)

.venv\Scripts\python.exe -c "import uvicorn; import fastapi" >nul 2>&1
if %errorlevel% equ 0 (
    echo %SUCCESS% Backend dependencies already installed
) else (
    echo %INFO% Installing Python dependencies into .venv...
    .venv\Scripts\python.exe -m pip install -U pip >nul
    .venv\Scripts\python.exe -m pip install -e ".[dev]"
    if %errorlevel% neq 0 (
        echo %ERROR% Failed to install backend dependencies
        pause
        exit /b 1
    ) else (
        echo %SUCCESS% Backend dependencies installed successfully
    )
)

cd app

REM Install frontend dependencies
echo %INFO% Installing frontend dependencies...
cd frontend

if exist "node_modules" (
    echo %SUCCESS% Frontend dependencies already installed
) else (
    echo %INFO% Installing Node.js dependencies...
    npm install
    if %errorlevel% neq 0 (
        echo %ERROR% Failed to install frontend dependencies
        pause
        exit /b 1
    )
    echo %SUCCESS% Frontend dependencies installed
)

cd ..

REM Start services
echo %INFO% Starting the AI Hedge Fund web application...
echo %INFO% Press Ctrl+C to stop all services
echo.

REM Start backend
echo %INFO% Launching backend server...
REM Run from project root to ensure proper Python imports
cd ..
start /b .venv\Scripts\uvicorn.exe app.backend.main:app --reload --host 127.0.0.1 --port 8006
cd app

timeout /t 3 /nobreak >nul

REM Check database initialization
echo %INFO% Checking database initialization...
timeout /t 2 /nobreak >nul

if exist "..\hedge_fund.db" (
    echo %SUCCESS% Database initialized successfully
) else (
    echo %WARNING% Database file not found, but will be created on first API call
)

REM Start frontend
echo %INFO% Launching frontend development server...
cd frontend
start /b npm run dev
cd ..

timeout /t 5 /nobreak >nul

echo %INFO% Opening browser...
timeout /t 2 /nobreak >nul
start http://localhost:5177

echo.
echo %SUCCESS% AI Hedge Fund web application is now running
echo %INFO% Frontend: http://localhost:5177
echo %INFO% Backend:  http://localhost:8006
echo %INFO% Docs:     http://localhost:8006/docs
echo %INFO% Database: SQLite (hedge_fund.db in project root)
echo.
echo %INFO% Press any key to stop both services...
pause >nul

REM Stop services
taskkill /f /im "uvicorn.exe" >nul 2>&1
taskkill /f /im "node.exe" >nul 2>&1

echo %SUCCESS% Services stopped. Goodbye
pause
