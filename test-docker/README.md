# Docker Container Testing

This directory contains scripts to build and test the Flask application container.

## Test Script

### `test-container.sh`

Comprehensive test script that builds and validates the Docker container functionality.

**What it tests:**
1. **Docker Build** - Builds the container from scratch
2. **Container Startup** - Starts the container and waits for healthy status
3. **Health Check** - Validates the `/health` endpoint
4. **Login Page** - Ensures the web interface is accessible
5. **Authentication** - Tests login functionality
6. **Sample API** - Validates the main API endpoint
7. **Main Application Page** - Tests the full web interface
8. **Container Logs** - Checks for errors in container logs
9. **Performance** - Basic response time measurement

### Usage

```bash
# Run from the test-docker directory
cd test-docker
./test-container.sh

# Or run from the project root
./test-docker/test-container.sh
```

### Output

The script provides colored output showing the progress and results of each test:
- 🟢 Green checkmarks for successful tests
- 🟡 Yellow warnings for minor issues
- 🔴 Red X marks for failures (script will exit)

### Requirements

- Docker and Docker Compose installed
- curl command available
- bash shell
- Ports 5000 available (or modify the script)

### What happens during testing

1. Builds the Docker image from scratch (no cache)
2. Starts the container using docker-compose
3. Waits up to 60 seconds for the container to report healthy
4. Runs a series of HTTP tests against the running container
5. Validates the web interface and API endpoints
6. Checks container logs for errors
7. Measures basic API performance
8. Cleans up all test artifacts when done

### Troubleshooting

If tests fail:

1. **Build failures**: Check Docker is running and you have sufficient disk space
2. **Container won't start**: Check port 5000 is available
3. **Health check fails**: Wait longer or check container logs with `docker compose logs`
4. **Authentication fails**: Verify default credentials (user/password) haven't changed
5. **API tests fail**: Check that the Flask application is running correctly

### Customization

You can modify the test script variables at the top:
- `TEST_PORT`: Change if port 5000 is unavailable
- `TEST_USERNAME`/`TEST_PASSWORD`: Change test credentials
- `TIMEOUT`: Adjust wait times for slower systems