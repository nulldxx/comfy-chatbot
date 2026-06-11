#!/bin/bash

# Docker Container Test Script
# Tests the Flask application container build and functionality

set -e  # Exit on any error

echo "🐳 Flask Application Container Test Suite"
echo "========================================="
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Test configuration
CONTAINER_NAME="flask-app-test"
TEST_PORT="5001"
TEST_USERNAME="testuser"
TEST_PASSWORD="testpass"
TIMEOUT=30

# Determine which compose file to use
if [ -n "$COMPOSE_FILE" ]; then
    COMPOSE_CMD="docker compose -f $COMPOSE_FILE"
    echo -e "${BLUE}📋 Using compose file: $COMPOSE_FILE${NC}"
elif [ -f "docker-compose.test.yml" ]; then
    COMPOSE_CMD="docker compose -f docker-compose.test.yml"
    echo -e "${BLUE}📋 Using test compose file: docker-compose.test.yml${NC}"
else
    COMPOSE_CMD="docker compose"
    echo -e "${BLUE}📋 Using default compose file: docker-compose.yml${NC}"
fi

# Cleanup function
cleanup() {
    echo -e "${YELLOW}🧹 Cleaning up test environment...${NC}"
    
    # Ensure we're in the project root directory
    SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
    cd "$PROJECT_ROOT" 2>/dev/null || true
    
    docker compose down --remove-orphans 2>/dev/null || true
    $COMPOSE_CMD down --remove-orphans 2>/dev/null || true
    docker rm -f $CONTAINER_NAME 2>/dev/null || true
    rm -f cookies.txt test-response.html api-response.json app-response.html 2>/dev/null || true
    echo -e "${GREEN}✅ Cleanup completed${NC}"
}

# Set cleanup trap
trap cleanup EXIT

# Function to wait for container to be healthy
wait_for_healthy() {
    local max_attempts=30
    local attempt=1
    
    echo -e "${BLUE}⏳ Waiting for container to be healthy...${NC}"
    while [ $attempt -le $max_attempts ]; do
        if docker ps --format "table {{.Names}}\t{{.Status}}" | grep -q "healthy"; then
            echo -e "${GREEN}✅ Container is healthy${NC}"
            return 0
        fi
        echo -e "${YELLOW}   Attempt $attempt/$max_attempts - waiting...${NC}"
        sleep 2
        ((attempt++))
    done
    
    echo -e "${RED}❌ Container failed to become healthy within $((max_attempts * 2)) seconds${NC}"
    return 1
}

# Test 1: Build the container
echo -e "${BLUE}📦 Test 1: Building Docker container...${NC}"

# Ensure we're in the project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_ROOT"

# Verify docker-compose.yml exists (skip check in CI with COMPOSE_FILE set)
if [ -z "$COMPOSE_FILE" ] && [ ! -f "docker-compose.yml" ]; then
    echo -e "${RED}❌ docker-compose.yml not found in $PWD${NC}"
    echo "Expected to find it in: $PROJECT_ROOT"
    exit 1
fi

$COMPOSE_CMD build --no-cache
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Docker build successful${NC}"
else
    echo -e "${RED}❌ Docker build failed${NC}"
    exit 1
fi
echo ""

# Test 2: Start the container
echo -e "${BLUE}🚀 Test 2: Starting container...${NC}"
$COMPOSE_CMD up -d
if [ $? -eq 0 ]; then
    echo -e "${GREEN}✅ Container started${NC}"
else
    echo -e "${RED}❌ Container failed to start${NC}"
    exit 1
fi

# Wait for container to be healthy
wait_for_healthy

echo ""

# Test 3: Health check
echo -e "${BLUE}🩺 Test 3: Testing health endpoint...${NC}"
health_response=$(curl -s http://localhost:5000/health)
if echo "$health_response" | grep -q '"status":"healthy"'; then
    echo -e "${GREEN}✅ Health check passed${NC}"
    echo "   Response: $health_response"
else
    echo -e "${RED}❌ Health check failed${NC}"
    echo "   Response: $health_response"
    exit 1
fi
echo ""

# Test 4: Login page
echo -e "${BLUE}🔐 Test 4: Testing login page...${NC}"
login_response=$(curl -s -w "%{http_code}" http://localhost:5000/ -o test-response.html)
if [ "$login_response" = "200" ] || [ "$login_response" = "302" ]; then
    echo -e "${GREEN}✅ Login page accessible (HTTP $login_response)${NC}"
else
    echo -e "${RED}❌ Login page failed (HTTP $login_response)${NC}"
    exit 1
fi
echo ""

# Test 5: Authentication
echo -e "${BLUE}🔑 Test 5: Testing authentication...${NC}"
auth_response=$(curl -s -w "%{http_code}" \
    -X POST \
    -d "username=user&password=password" \
    -c cookies.txt \
    http://localhost:5000/login \
    -o /dev/null)

if [ "$auth_response" = "302" ]; then
    echo -e "${GREEN}✅ Authentication successful (HTTP $auth_response)${NC}"
else
    echo -e "${RED}❌ Authentication failed (HTTP $auth_response)${NC}"
    exit 1
fi
echo ""

# Test 6: Sample API endpoint
echo -e "${BLUE}🔗 Test 6: Testing sample API endpoint...${NC}"
api_response=$(curl -s -w "%{http_code}" \
    -b cookies.txt \
    http://localhost:5000/api/data \
    -o api-response.json)

if [ "$api_response" = "200" ]; then
    echo -e "${GREEN}✅ Sample API working (HTTP $api_response)${NC}"
    # Check if response contains expected data
    if grep -q '"message"' api-response.json && grep -q '"status"' api-response.json; then
        echo -e "${GREEN}   Response contains expected JSON structure${NC}"
    else
        echo -e "${YELLOW}   Warning: Response may not contain expected data structure${NC}"
    fi
    rm -f api-response.json
else
    echo -e "${RED}❌ Sample API failed (HTTP $api_response)${NC}"
    exit 1
fi
echo ""

app_response=$(curl -s -w "%{http_code}" \
    -b cookies.txt \
    http://localhost:5000/ \
    -o app-response.html)

if [ "$app_response" = "200" ]; then
    echo -e "${GREEN}✅ Main application page accessible (HTTP $app_response)${NC}"
    
    # Check if page contains expected content
    if grep -q "Flask Application" app-response.html; then
        echo -e "${GREEN}   Page contains expected application content${NC}"
    else
        echo -e "${YELLOW}   Warning: Page may not contain expected content${NC}"
    fi
    
    rm -f app-response.html
else
    echo -e "${RED}❌ Main application page failed (HTTP $app_response)${NC}"
    exit 1
fi
echo ""

# Test 8: Container logs check
echo -e "${BLUE}📋 Test 8: Checking container logs for errors...${NC}"
error_count=$($COMPOSE_CMD logs flask-app 2>&1 | grep -i -c "error\|exception\|traceback" || true)
if [ "$error_count" -eq 0 ]; then
    echo -e "${GREEN}✅ No errors found in container logs${NC}"
else
    echo -e "${YELLOW}⚠️  Found $error_count potential error(s) in logs${NC}"
    echo "Recent logs:"
    $COMPOSE_CMD logs --tail=10 flask-app
fi
echo ""

# Test 9: Performance test
echo -e "${BLUE}⚡ Test 9: Basic performance test...${NC}"
start_time=$(date +%s%N)
perf_response=$(curl -s -w "%{http_code}" \
    -b cookies.txt \
    http://localhost:5000/api/data \
    -o /dev/null)
end_time=$(date +%s%N)

response_time=$(( (end_time - start_time) / 1000000 )) # Convert to milliseconds

if [ "$perf_response" = "200" ]; then
    echo -e "${GREEN}✅ Performance test passed (${response_time}ms)${NC}"
    if [ "$response_time" -lt 1000 ]; then
        echo -e "${GREEN}   Excellent response time${NC}"
    elif [ "$response_time" -lt 3000 ]; then
        echo -e "${YELLOW}   Good response time${NC}"
    else
        echo -e "${YELLOW}   Response time could be improved${NC}"
    fi
else
    echo -e "${RED}❌ Performance test failed (HTTP $perf_response)${NC}"
    exit 1
fi
echo ""

# Final summary
echo -e "${GREEN}🎉 All tests completed successfully!${NC}"
echo ""
echo -e "${BLUE}📊 Test Summary:${NC}"
echo -e "${GREEN}✅ Docker build${NC}"
echo -e "${GREEN}✅ Container startup${NC}"
echo -e "${GREEN}✅ Health check${NC}"
echo -e "${GREEN}✅ Login page${NC}"
echo -e "${GREEN}✅ Authentication${NC}"
echo -e "${GREEN}✅ Sample API${NC}"
echo -e "${GREEN}✅ Main application page${NC}"
echo -e "${GREEN}✅ Container logs${NC}"
echo -e "${GREEN}✅ Performance test${NC}"
echo ""
echo -e "${BLUE}🌐 Application is ready at: http://localhost:5000${NC}"
echo -e "${BLUE}🔑 Default credentials: user / password${NC}"
echo ""
echo -e "${GREEN}✨ Test suite completed successfully! ✨${NC}"