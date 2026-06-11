# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

This is a Flask application template designed for containerized deployment. It provides a simple hello world application with authentication, Docker support, and production-ready infrastructure. The template includes session-based authentication, health checks, and is designed for production deployment using Gunicorn.

## Development Commands

### Building and Running
```bash
# Build and run locally (development)
docker-compose up --build -d

# Stop the application
docker-compose down

# View application logs
docker-compose logs -f flask-app
```

### Testing

#### Comprehensive Test Suite (Recommended)
```bash
# Run all tests: Python unit tests + Docker container tests
./scripts/test-all

# This runs the complete test suite:
# 1. Python import tests
# 2. Python unit tests  
# 3. Docker container tests
```

#### Individual Test Components

**Python Unit Tests**
```bash
# Run import tests (verify all dependencies work)
python tests/test_imports.py

# Run unit tests
python -m pytest tests/test_simple.py -v

# Run all tests
python -m unittest discover tests/
```

**Docker Container Testing**
```bash
# Run comprehensive Docker container test suite
./test-docker/test-container.sh

# This test script validates:
# - Docker build process
# - Container startup and health
# - Web interface accessibility
# - API functionality
# - Authentication system
```

### Release Management
```bash
# Create new release (increments patch version automatically)
./scripts/make-release

# Setup application for end users
./scripts/setup.sh  # Linux/macOS
./scripts/setup.ps1  # Windows PowerShell
```

### Local Development
```bash
# Install dependencies (optional for local testing)
pip install -r requirements.txt

# Run Flask development server (not recommended for production)
python app.py

# Production server (Gunicorn - used in Docker)
gunicorn --config gunicorn.conf.py app:app
```

## Architecture Overview

### Core Application Structure
- **app.py**: Main Flask application with authentication and basic routes
- **gunicorn.conf.py**: Production WSGI server configuration with optimized worker settings
- **templates/**: HTML templates for web interface (index.html, login.html)

### Key Components
1. **Authentication System**: Session-based login with environment variable credentials
2. **Security**: Non-root container execution, secure session management
3. **API Endpoints**: RESTful endpoints for basic application functionality
4. **Health Checks**: Built-in health check endpoint for container orchestration

### Docker Multi-Stage Build
- **Builder stage**: Compiles Python packages with build dependencies
- **Runtime stage**: Minimal image with only runtime requirements
- Uses Python 3.11 slim base image for security and size optimization

### Configuration
Environment variables for deployment:
- `APP_USERNAME`: Authentication username (default: 'user')
- `APP_PASSWORD`: Authentication password (default: 'password')  
- `SECRET_KEY`: Flask session secret (change in production)

## Development Guidelines

### Security Practices
- All routes except `/health` and `/login` require authentication
- Session-based authentication with configurable credentials
- Non-root user execution in container
- Secure session management with configurable secret key

### Performance Considerations
- Gunicorn multi-worker configuration scales with CPU cores
- Minimal Docker image for fast deployment
- Health checks ensure container reliability

### Testing Strategy
- Unit tests cover core application functions
- Import tests verify all dependencies work correctly in container environment
- Health check endpoint tests ensure proper API responses
- Mock authentication in tests using Flask test client sessions

### File Organization
```
/app.py                 # Main application logic
/templates/             # Jinja2 HTML templates  
/tests/                 # Unit tests and import verification
/scripts/               # Build, setup, and release automation
/test-docker/           # Docker container testing
```

### Deployment Notes
- Uses multi-stage Docker build to minimize image size
- Gunicorn configuration optimized for container deployment
- Health checks ensure container reliability in orchestrated environments
- Scripts provide automated setup and testing across platforms