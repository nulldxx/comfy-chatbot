# Flask Application Template

A production-ready Flask application template with authentication, Docker support, and CI/CD infrastructure. This template provides a solid foundation for building web applications with modern deployment practices.

## ✨ Features

- **Session-based Authentication**: Secure login system with configurable credentials
- **Docker Support**: Multi-stage Docker build for production deployment
- **Production Ready**: Gunicorn WSGI server with optimized configuration
- **Health Checks**: Built-in health endpoint for container orchestration
- **CI/CD Ready**: GitHub Actions workflow templates included
- **Testing Framework**: Unit tests and Docker container validation
- **Security**: Non-root container execution, secure session management

## 🚀 Quick Start

### Prerequisites
- Docker and Docker Compose
- Python 3.11+ (for local development)

### Using Docker (Recommended)

1. **Clone the repository**
   ```bash
   git clone <your-repo-url>
   cd flask-app-template
   ```

2. **Start the application**
   ```bash
   docker-compose up --build -d
   ```

3. **Access the application**
   - Open your browser to `http://localhost:5000`
   - Login with default credentials: `user` / `password`

### Local Development

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Run the application**
   ```bash
   python app.py
   ```

## ⚙️ Configuration

Configure the application using environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `APP_USERNAME` | `user` | Authentication username |
| `APP_PASSWORD` | `password` | Authentication password |
| `SECRET_KEY` | `your-secret-key-change-this-in-production` | Flask session secret |

### Docker Compose Configuration

```yaml
environment:
  - APP_USERNAME=myuser
  - APP_PASSWORD=mypassword
  - SECRET_KEY=your-very-secure-secret-key
```

## 🔧 API Endpoints

- `GET /` - Main application page (requires authentication)
- `GET /login` - Login page
- `POST /login` - Authentication endpoint
- `GET /logout` - Logout endpoint
- `GET /health` - Health check endpoint
- `GET /api/data` - Sample API endpoint (requires authentication)

## 🐳 Docker Details

### Multi-Stage Build
- **Builder stage**: Compiles dependencies with build tools
- **Runtime stage**: Minimal production image (~150MB)
- **Base**: Python 3.11 slim for security and size optimization

### Security Features
- Non-root user execution
- Read-only filesystem where possible
- Minimal attack surface
- Secure session management

## 🧪 Testing

### Run All Tests
```bash
./scripts/test-all
```

### Individual Test Components
```bash
# Python unit tests
python -m pytest tests/ -v

# Import verification
python tests/test_imports.py

# Docker container tests
./test-docker/test-container.sh
```

## 📁 Project Structure

```
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── Dockerfile            # Multi-stage Docker build
├── docker-compose.yml    # Development compose file
├── gunicorn.conf.py      # Production server configuration
├── templates/            # HTML templates
│   ├── index.html       # Main page
│   └── login.html       # Login page
├── tests/               # Test suite
├── scripts/             # Automation scripts
└── test-docker/         # Container testing
```

## 🚀 Deployment

### Production Deployment
1. Set secure environment variables
2. Use Docker Compose or container orchestration
3. Configure reverse proxy (nginx, traefik, etc.)
4. Set up SSL certificates

### Environment Variables for Production
```bash
export APP_USERNAME="your-secure-username"
export APP_PASSWORD="your-secure-password"
export SECRET_KEY="your-very-long-random-secret-key"
```

## 🔒 Security Best Practices

- Change default credentials before deployment
- Use a strong, random secret key
- Deploy behind HTTPS
- Regularly update dependencies
- Monitor container logs

## 🆘 Support

This template provides a solid foundation for Flask applications. Customize it according to your specific needs:

1. Add your application logic to `app.py`
2. Update templates in `templates/`
3. Add additional routes and functionality
4. Extend the test suite
5. Configure deployment for your environment

## 📝 License

This template is provided as-is for educational and development purposes.