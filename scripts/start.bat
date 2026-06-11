@echo off
echo 🚀 Flask App Docker Setup
echo ==========================

REM Check if Docker is running
docker info >nul 2>&1
if errorlevel 1 (
    echo ❌ Docker is not running. Please start Docker and try again.
    pause
    exit /b 1
)

REM Build and run with Docker Compose
echo 🔨 Building and starting the Flask application...
docker compose up --build -d

if %errorlevel% equ 0 (
    echo.
    echo 🎉 Flask application is now running!
    echo 📱 Access your application at: http://localhost:5000
    echo 🔑 Default login: user / password
    echo.
    echo 🛑 To stop the application, run: docker compose down
    echo 📊 To view logs, run: docker compose logs -f flask-app
) else (
    echo ❌ Failed to start the Flask application. Check the logs for errors.
    docker compose logs flask-app
)

pause
