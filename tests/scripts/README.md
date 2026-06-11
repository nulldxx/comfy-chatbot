# Scripts Directory

This directory contains automation scripts for Flask application development and deployment.

## Scripts Overview

### `test-all`
**Comprehensive test suite** that runs all tests in the correct order.

**Usage:**
```bash
./scripts/test-all
```

**What it does:**
1. **Phase 1: Python Unit Tests**
   - Import tests (`tests/test_imports.py`)
   - Unit tests (`tests/test_simple.py`) 
2. **Phase 2: Docker Container Tests**
   - Comprehensive Docker container test suite (`test-docker/test-container.sh`)
   - Multiple validation checks

**Output:**
- ✅ Colored output showing test progress
- 📊 Summary statistics and timing
- 🎉 Success celebration or 🔧 troubleshooting tips
- 📋 Complete test coverage report

**Benefits:**
- One command runs everything
- Proper test sequencing
- Comprehensive coverage
- Clear pass/fail reporting
- Troubleshooting guidance

### `make-release`
Creates new release with automatic version incrementing.

### `setup.sh` / `setup.ps1`
Platform-specific setup scripts for end users.

### `start.sh` / `start.bat`
Platform-specific startup scripts.

## Usage Examples

```bash
# Run complete test suite (recommended)
./scripts/test-all

# Run individual components
python tests/test_imports.py
./test-docker/test-container.sh

# Release management
./scripts/make-release

# User setup (run once)
./scripts/setup.sh      # Linux/macOS
./scripts/setup.ps1     # Windows PowerShell
```

## Test Script Features

### Error Handling
- Stops on first critical error
- Continues through expected failures (e.g., OpenCV outside Docker)
- Proper cleanup on exit

### Reporting
- Real-time colored output
- Test timing and statistics
- Pass/fail counts per category
- Actionable troubleshooting tips

### Compatibility
- Works with both `pytest` and `unittest`
- Handles missing dependencies gracefully
- Cross-platform compatible

## Development Workflow

1. **Make changes** to code
2. **Run tests**: `./scripts/test-all`
3. **Fix any failures** shown in output
4. **Commit changes** when all tests pass
5. **Release**: `./scripts/make-release`

This ensures robust, tested releases every time.